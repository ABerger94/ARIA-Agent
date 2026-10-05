"""
ARIA MCP client — connect to Model Context Protocol servers and use their tools.

Alek's desktop agent can call any MCP server (filesystem, GitHub, Brave search,
SQLite, ...) and every server tool shows up as a first-class ARIA tool named
``mcp_<server>__<tool>`` so the normal tool loop (validation, audit, truncation)
applies unchanged.

The optional ``mcp`` Python package is required at runtime (``pip install mcp``).
If it is missing, the mcp_* tools explain how to install it instead of failing.

Sessions live in a dedicated background asyncio loop so ARIA's synchronous
dispatch can call them without an event loop of its own.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
import json
import re
import shlex
import threading
from typing import Any, Dict, List, Optional, Tuple

from aria import config

SETTINGS_KEY = "mcp_servers"

_GEMINI_TYPES = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}

_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def sanitize_tool_name(raw: str, limit: int = 64) -> str:
    """Make a name safe for Gemini function declarations."""
    return _NAME_RE.sub("_", raw or "unnamed")[:limit]


def convert_schema(node: Any) -> Dict[str, Any]:
    """Convert an MCP JSON Schema node to a Gemini function-declaration schema."""
    if not isinstance(node, dict):
        return {"type": "OBJECT"}
    t = str(node.get("type", "object")).lower()
    out: Dict[str, Any] = {"type": _GEMINI_TYPES.get(t, "OBJECT")}
    desc = node.get("description")
    if desc:
        out["description"] = str(desc)[:500]
    if t == "object":
        props = node.get("properties") or {}
        if isinstance(props, dict):
            out["properties"] = {k: convert_schema(v) for k, v in props.items()}
        req = node.get("required") or []
        if req:
            out["required"] = [str(r) for r in req]
    elif t == "array" and "items" in node:
        out["items"] = convert_schema(node["items"])
    if "enum" in node and isinstance(node["enum"], list):
        out["enum"] = node["enum"][:50]
    return out


def _mcp_available() -> bool:
    try:
        import mcp  # noqa: F401
        return True
    except Exception:
        return False


def _missing_dep_message() -> str:
    return ("MCP support needs the 'mcp' Python package. Install it with "
            "`pip install \"mcp>=1.0,<2\"`, restart ARIA, then run mcp_connect again.")


# ---------------------------------------------------------------------------
# Server configuration (persisted in workspace/settings.json)
# ---------------------------------------------------------------------------

def get_servers() -> Dict[str, Dict[str, Any]]:
    d = config.get_setting(SETTINGS_KEY, {}) or {}
    return d if isinstance(d, dict) else {}


def save_servers(servers: Dict[str, Dict[str, Any]]) -> None:
    config.set_setting(SETTINGS_KEY, servers)


def add_server(name: str, transport: str = "stdio", command: str = "",
               args: Any = None, env: Any = None, cwd: str = "",
               url: str = "", headers: Any = None,
               enabled: bool = True) -> str:
    """Add or replace an MCP server configuration. Returns a status message."""
    name = sanitize_tool_name((name or "").strip().lower().replace("-", "_"), 40)
    if not name:
        return "Give the server a name, e.g. mcp_setup(name='filesystem', command='npx', ...)."
    transport = (transport or "stdio").lower()
    if transport not in ("stdio", "sse", "http"):
        return f"Unknown transport '{transport}'. Use 'stdio', 'sse', or 'http'."
    if transport == "stdio" and not command:
        return "stdio servers need a command (e.g. command='npx', args=['-y','@modelcontextprotocol/server-filesystem','/path'])."
    if transport in ("sse", "http") and not url:
        return f"{transport} servers need a url."
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = shlex.split(args)
    if isinstance(env, str):
        try:
            env = json.loads(env) if env.strip() else {}
        except Exception:
            return "env must be a JSON object string, e.g. '{\"GITHUB_TOKEN\": \"...\"}'."
    if isinstance(headers, str):
        try:
            headers = json.loads(headers) if headers.strip() else {}
        except Exception:
            return "headers must be a JSON object string."
    servers = get_servers()
    servers[name] = {
        "transport": transport,
        "command": command or "",
        "args": list(args or []),
        "env": dict(env or {}),
        "cwd": cwd or "",
        "url": url or "",
        "headers": dict(headers or {}),
        "enabled": bool(enabled),
    }
    save_servers(servers)
    return (f"MCP server '{name}' saved ({transport}). "
            f"Run mcp_connect(name='{name}') to connect and load its tools.")


def remove_server(name: str) -> str:
    servers = get_servers()
    key = (name or "").strip().lower()
    if key not in servers:
        return f"No MCP server named '{name}'. Known: {', '.join(sorted(servers)) or 'none'}."
    disconnect(key)
    del servers[key]
    save_servers(servers)
    return f"MCP server '{name}' removed."


# ---------------------------------------------------------------------------
# Bridge: background asyncio loop owning live MCP sessions
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HTTP / Streamable-HTTP MCP Client (Pure Python, no mcp package required)
# ---------------------------------------------------------------------------

class HTTPMCPClient:
    """Lightweight pure-Python MCP client for streamable-HTTP and JSON-RPC servers."""

    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None):
        self.url = url
        self.headers = dict(headers or {})
        self.session = None
        self.session_id = None
        self._id = 0

    def _get_session(self):
        if self.session is None:
            import requests
            self.session = requests.Session()
        return self.session

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _post(self, payload: dict) -> dict:
        s = self._get_session()
        h = dict(self.headers)
        h["Content-Type"] = "application/json"
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        r = s.post(self.url, headers=h, json=payload, timeout=30)
        r.raise_for_status()
        if "mcp-session-id" in r.headers:
            self.session_id = r.headers["mcp-session-id"]

        text = r.text.strip()
        data = None
        if text.startswith("event:") or "data:" in text:
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    break
        else:
            data = r.json()
        return data or {}

    def initialize(self):
        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ARIA", "version": "1.0.0"}
            }
        }
        return self._post(req)

    def list_tools(self) -> list:
        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {}
        }
        res = self._post(req)
        raw_tools = res.get("result", {}).get("tools", [])
        tools = []
        for t in raw_tools:
            tools.append(SimpleNamespace(
                name=t.get("name", ""),
                description=t.get("description", ""),
                inputSchema=t.get("inputSchema", {})
            ))
        return tools

    def call_tool(self, name: str, arguments: dict):
        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments or {}
            }
        }
        res = self._post(req)
        if "error" in res:
            err = res["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return SimpleNamespace(isError=True, content=[SimpleNamespace(type="text", text=f"Error: {msg}")])

        result_obj = res.get("result", {})
        raw_content = result_obj.get("content", [])
        content = []
        for b in raw_content:
            if isinstance(b, dict):
                content.append(SimpleNamespace(
                    type=b.get("type", "text"),
                    text=b.get("text", str(b))
                ))
            else:
                content.append(SimpleNamespace(type="text", text=str(b)))
        return SimpleNamespace(isError=result_obj.get("isError", False), content=content)

class MCPBridge:
    """Owns one background thread + asyncio loop; sessions live in that loop."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # name -> {"config": dict, "cm": ctx manager, "session": ClientSession,
        #           "tools": [Tool], "aria_names": [str]}
        self._sessions: Dict[str, Dict[str, Any]] = {}

    # -- loop management ----------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop and self._loop.is_running():
                return self._loop
            started = self._thread is not None and self._thread.is_alive()

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            with self._lock:
                self._loop = loop
            loop.run_forever()

        if not started:
            with self._lock:
                self._thread = threading.Thread(target=_runner, daemon=True,
                                                name="aria-mcp-loop")
                self._thread.start()
        for _ in range(100):
            with self._lock:
                loop = self._loop
            if loop and loop.is_running():
                return loop
            threading.Event().wait(0.05)
        raise RuntimeError("MCP event loop did not start.")

    def _run(self, coro, timeout: float = 60.0):
        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    # -- connection ----------------------------------------------------------
    # Sessions are owned by a per-server "actor" coroutine that lives in the
    # loop thread and holds the `async with` contexts open for the session's
    # whole lifetime. Sync callers talk to the actor through an asyncio.Queue;
    # every MCP operation therefore runs inside the loop thread where the
    # anyio/asyncio primitives were created.

    async def _server_actor(self, name: str, cfg: Dict[str, Any],
                            queue: "asyncio.Queue",
                            ready: "asyncio.Future") -> None:
        try:
            transport = cfg.get("transport", "stdio")
            if transport == "http":
                headers = dict(cfg.get("headers") or {})
                if "robinhood.com" in cfg.get("url", "") and "Authorization" not in headers:
                    tfile = os.path.join(config.WORKSPACE_DIR, "robinhood_token.json")
                    if os.path.exists(tfile):
                        try:
                            with open(tfile, "r") as tf:
                                tdata = json.load(tf)
                                tok = tdata.get("access_token")
                                if tok:
                                    headers["Authorization"] = f"Bearer {tok}"
                        except Exception:
                            pass
                client = HTTPMCPClient(cfg["url"], headers=headers)
                await asyncio.to_thread(client.initialize)
                tools = await asyncio.to_thread(client.list_tools)
                if not ready.done():
                    ready.set_result(list(tools or []))
                while True:
                    item = await queue.get()
                    if item is None:
                        return
                    tool, args, fut = item
                    try:
                        result = await asyncio.to_thread(client.call_tool, tool, args or {})
                        if not fut.done():
                            fut.set_result(result)
                    except Exception as e:
                        if not fut.done():
                            fut.set_exception(e)
            elif transport == "stdio":
                from mcp import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client
                params = StdioServerParameters(
                    command=cfg["command"],
                    args=list(cfg.get("args") or []),
                    env=dict(cfg.get("env") or {}) or None,
                    cwd=cfg.get("cwd") or None,
                )
                cm = stdio_client(params)
                async with cm as streams:
                    read, write = streams
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        if not ready.done():
                            ready.set_result(list(tools.tools or []))
                        while True:
                            item = await queue.get()
                            if item is None:  # shutdown sentinel
                                return
                            tool, args, fut = item
                            try:
                                result = await session.call_tool(tool, args or {})
                                if not fut.done():
                                    fut.set_result(result)
                            except Exception as e:
                                if not fut.done():
                                    fut.set_exception(e)
            else:
                from mcp import ClientSession
                from mcp.client.sse import sse_client
                cm = sse_client(cfg["url"], headers=dict(cfg.get("headers") or {}) or None)
                async with cm as streams:
                    read, write = streams
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        if not ready.done():
                            ready.set_result(list(tools.tools or []))
                        while True:
                            item = await queue.get()
                            if item is None:  # shutdown sentinel
                                return
                            tool, args, fut = item
                            try:
                                result = await session.call_tool(tool, args or {})
                                if not fut.done():
                                    fut.set_result(result)
                            except Exception as e:
                                if not fut.done():
                                    fut.set_exception(e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not ready.done():
                ready.set_exception(e)
            # If we failed after ready, the serve loop is dead; pending
            # callers get here only via new calls, which check liveness.

    async def _spawn_actor(self, name: str, cfg: Dict[str, Any]):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        ready: asyncio.Future = loop.create_future()
        task = asyncio.create_task(
            self._server_actor(name, cfg, queue, ready), name=f"aria-mcp-{name}")
        tools = await asyncio.wait_for(asyncio.shield(ready), timeout=25.0)
        return task, queue, tools

    async def _actor_call(self, name: str, tool: str, arguments: Dict[str, Any],
                          timeout: float):
        entry = self._sessions.get(name)
        if entry is None:
            raise RuntimeError(f"MCP server '{name}' is not connected.")
        if entry["task"].done():
            raise RuntimeError(f"MCP server '{name}' connection died.")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        await entry["queue"].put((tool, arguments, fut))
        return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)

    async def _stop_actor(self, entry: Dict[str, Any]) -> None:
        task = entry.get("task")
        queue = entry.get("queue")
        if queue is not None:
            try:
                await asyncio.wait_for(queue.put(None), timeout=5.0)
            except Exception:
                pass
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=10.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                pass

    def connect(self, name: str, timeout: float = 30.0) -> Tuple[bool, str]:
        """Connect one server and register its tools. Returns (ok, message)."""
        servers = get_servers()
        key = (name or "").strip().lower()
        if key not in servers:
            return False, (f"No MCP server named '{name}'. "
                           f"Known: {', '.join(sorted(servers)) or 'none'}.")
        cfg = servers[key]
        if cfg.get("transport") != "http" and not _mcp_available():
            return False, _missing_dep_message()
        if key in self._sessions:
            return True, f"MCP server '{key}' is already connected."
        try:
            task, queue, tools = self._run(self._spawn_actor(key, cfg), timeout=35.0)
        except Exception as e:
            return False, f"Could not connect to MCP server '{key}': {e}"
        entry = {"config": cfg, "task": task, "queue": queue,
                 "tools": tools, "aria_names": []}
        self._sessions[key] = entry
        aria_names = _register_server_tools(key, tools)
        entry["aria_names"] = aria_names
        return True, (f"Connected to MCP server '{key}' "
                      f"({cfg.get('transport')}); loaded {len(aria_names)} tools: "
                      f"{', '.join(aria_names[:8])}"
                      f"{'...' if len(aria_names) > 8 else ''}.")

    def disconnect(self, name: str) -> Tuple[bool, str]:
        key = (name or "").strip().lower()
        entry = self._sessions.pop(key, None)
        if entry is None:
            return False, f"MCP server '{key}' is not connected."
        try:
            self._run(self._stop_actor(entry), timeout=20.0)
        except Exception:
            pass
        _unregister_server_tools(key, entry.get("aria_names") or [])
        return True, f"Disconnected MCP server '{key}'."

    def disconnect_all(self) -> str:
        names = list(self._sessions.keys())
        for n in names:
            self.disconnect(n)
        return f"Disconnected {len(names)} MCP server(s)." if names else "No MCP servers were connected."

    def status(self) -> List[Dict[str, Any]]:
        out = []
        for name, cfg in sorted(get_servers().items()):
            entry = self._sessions.get(name)
            out.append({
                "name": name,
                "transport": cfg.get("transport"),
                "enabled": bool(cfg.get("enabled", True)),
                "connected": entry is not None,
                "tools": len(entry.get("aria_names") or []) if entry else 0,
            })
        return out

    def call_tool(self, name: str, tool: str, arguments: Dict[str, Any],
                  timeout: float = 120.0) -> str:
        """Call a tool on a connected server; returns formatted text."""
        try:
            result = self._run(self._actor_call(name, tool, arguments, timeout),
                               timeout=timeout + 10.0)
        except Exception as e:
            return f"[MCP call failed on '{name}': {e}]"
        return format_tool_result(result)


_BRIDGE: Optional[MCPBridge] = None
_BRIDGE_LOCK = threading.Lock()


def get_bridge() -> MCPBridge:
    global _BRIDGE
    with _BRIDGE_LOCK:
        if _BRIDGE is None:
            _BRIDGE = MCPBridge()
        return _BRIDGE


# ---------------------------------------------------------------------------
# Tool registration into ARIA's dispatch + schemas
# ---------------------------------------------------------------------------

def _register_server_tools(server: str, tools: List[Any]) -> List[str]:
    """Register each MCP tool as an ARIA tool. Returns the ARIA tool names."""
    from aria.tools import schemas as _schemas
    from aria.tools.dispatch import register_tool
    bridge = get_bridge()
    aria_names: List[str] = []
    seen = set()
    for t in tools:
        base = f"mcp_{server}__{getattr(t, 'name', 'tool')}"
        aria_name = sanitize_tool_name(base)
        i = 2
        while aria_name in seen:  # de-dupe after sanitization
            aria_name = sanitize_tool_name(f"{base}_{i}")
            i += 1
        seen.add(aria_name)
        schema = getattr(t, "inputSchema", None) or {}
        desc = getattr(t, "description", "") or getattr(t, "name", "")
        decl = {
            "name": aria_name,
            "description": f"[MCP:{server}] {desc}".strip()[:600],
            "parameters": convert_schema(schema),
        }
        _schemas.register_dynamic_tool_declaration(aria_name, decl, toolkit="mcp")
        mcp_tool_name = getattr(t, "name", "")
        server_key = server

        def _handler(a, _s=server_key, _t=mcp_tool_name, _b=bridge):
            return _b.call_tool(_s, _t, a or {})

        register_tool(aria_name, _handler)
        aria_names.append(aria_name)
    return aria_names


def _unregister_server_tools(server: str, aria_names: List[str]) -> None:
    from aria.tools import schemas as _schemas
    for n in aria_names:
        _schemas.unregister_dynamic_tool_declaration(n, toolkit="mcp")


def disconnect(name: str) -> Tuple[bool, str]:
    return get_bridge().disconnect(name)


def autoconnect_enabled_servers() -> str:
    """Connect every enabled server; failures are collected, never raised."""
    bridge = get_bridge()
    ok, failed = [], []
    for name, cfg in get_servers().items():
        if not cfg.get("enabled", True):
            continue
        good, msg = bridge.connect(name)
        (ok if good else failed).append(f"{name}: {msg}")
    parts = []
    if ok:
        parts.append(f"connected {len(ok)}: " + "; ".join(ok))
    if failed:
        parts.append(f"failed {len(failed)}: " + "; ".join(failed))
    summary = " | ".join(parts) if parts else "no MCP servers configured"
    try:
        config.add_log(f"[ARIA] MCP autoconnect: {summary}")
    except Exception:
        pass
    return summary


def format_tool_result(result: Any) -> str:
    """Flatten an MCP CallToolResult into readable text."""
    if getattr(result, "isError", False):
        prefix = "[MCP tool error] "
    else:
        prefix = ""
    chunks: List[str] = []
    content = getattr(result, "content", None) or []
    for block in content:
        btype = getattr(block, "type", "")
        if btype == "text":
            chunks.append(getattr(block, "text", ""))
        elif btype == "image":
            chunks.append("[image result omitted]")
        elif btype == "resource":
            res = getattr(block, "resource", None)
            uri = getattr(res, "uri", "") if res else ""
            chunks.append(f"[resource: {uri}]")
        else:
            chunks.append(f"[{btype or 'unknown'} result omitted]")
    text = "\n".join(c for c in chunks if c).strip()
    if not text:
        text = "(empty result)"
    return prefix + text
