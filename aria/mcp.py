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
import hashlib
import os
from types import SimpleNamespace
import json
import re
import shlex
import threading
from typing import Any, Dict, List, Optional, Tuple

from aria import config
from aria import outcome

# The Robinhood access token is attached only to the real Robinhood
# origin (pass 25): a substring check on the raw URL let any
# attacker URL containing "robinhood.com" — a suffix domain, a
# query string, a path — receive the Bearer token.
_ROBINHOOD_HOST = "robinhood.com"


def is_robinhood_url(url: str) -> bool:
    """True only for an HTTPS URL whose parsed hostname is
    robinhood.com or one of its subdomains."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower()
        return (parsed.scheme == "https"
                and (host == _ROBINHOOD_HOST
                     or host.endswith("." + _ROBINHOOD_HOST)))
    except Exception:
        return False


SETTINGS_KEY = "mcp_servers"

_SCHEMA_TYPES = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}

_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def sanitize_tool_name(raw: str, limit: int = 64) -> str:
    """Make a name safe for function declarations."""
    return _NAME_RE.sub("_", raw or "unnamed")[:limit]


def convert_schema(node: Any) -> Dict[str, Any]:
    """Convert an MCP JSON Schema node to a function-declaration schema."""
    if not isinstance(node, dict):
        return {"type": "OBJECT"}
    t = str(node.get("type", "object")).lower()
    out: Dict[str, Any] = {"type": _SCHEMA_TYPES.get(t, "OBJECT")}
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


def server_config_digest(name: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """Stable digest of one server's stored configuration.

    Pass 27 #19: an mcp_connect approval is bound to the config it
    was approved against — the digest is a hash, so no secret in
    the config (commands, env values, headers) is ever exposed.
    """
    if cfg is None:
        cfg = get_servers().get((name or "").strip().lower()) or {}
    blob = json.dumps(cfg, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _registration_digest(tools: List[Any]) -> str:
    """Stable digest of a server's registered tool set (names +
    descriptions + input schemas). A reconnect that changes the
    tools the server exposes changes the digest, so an approval
    granted against the old registration cannot execute against
    the new one (pass 27 #19)."""
    rows = []
    for t in tools or []:
        schema = getattr(t, "inputSchema", None) or {}
        rows.append([getattr(t, "name", ""),
                     getattr(t, "description", "") or "",
                     json.dumps(schema, sort_keys=True, default=str)])
    rows.sort(key=lambda r: r[0])
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def freeze_mcp_connect_args(name: str) -> Dict[str, Any]:
    """Bind an mcp_connect approval to the CURRENT server config.

    Raises ValueError for an unknown server, so no revisionless
    approval is parked (same contract as usertools freeze).
    """
    key = (name or "").strip().lower()
    servers = get_servers()
    if key not in servers:
        raise ValueError(f"No MCP server named '{name}'.")
    return {"_config_digest": server_config_digest(key, servers[key])}


def servers_digest(servers: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """Stable digest of the ENABLED server set: each enabled
    server's name + config digest, sorted. A connect-all approval
    binds this set (pass 28 #9): a server added, removed,
    disabled, or reconfigured after the park changes the digest,
    so the replay refuses instead of connecting a set the
    approval never covered. Disabled servers are not connected by
    a connect-all and are not part of the set."""
    if servers is None:
        servers = get_servers()
    rows = sorted(
        (name, server_config_digest(name, cfg))
        for name, cfg in (servers or {}).items()
        if (cfg or {}).get("enabled", True))
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def freeze_mcp_connect_all_args() -> Dict[str, Any]:
    """Bind a connect-all mcp_connect approval to the CURRENT
    enabled server set (pass 28 #9). There is no single config
    to bind, but the set itself is the approval's scope."""
    return {"_servers_digest": servers_digest()}


def freeze_discovered_tool_args(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Bind a discovered-MCP-tool approval to the CURRENT tool
    registration of its server AND to the CURRENT server config.
    When the server is not connected there is no registration to
    bind to, so the registration digest is omitted — a tool
    registered without a live session (the state the
    approval-gating tests pin) stays approvable, and the
    call-time check in call_tool still enforces any digest that
    IS present. Pass 28 #5: the config lives on disk, so it binds
    even when disconnected — a reconnect to a reconfigured server
    (new command/URL/env) with the same tool names used to ride
    the old approval, because only the registration was checked.
    A server with no config entry has no config to bind."""
    server, _remote = config.parse_discovered_mcp_tool(tool)
    out = dict(args or {})
    key = (server or "").strip().lower()
    servers = get_servers()
    if key in servers:
        out["_config_digest"] = server_config_digest(key, servers[key])
    digest = get_bridge().registration_digest(server)
    if digest is None:
        return out
    out["_registration_digest"] = digest
    return out


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

# Pass 29 #31: bound on SSE events scanned for the matching
# response frame. The body is already fully received, so this
# bounds work, not wall time.
_SSE_MAX_EVENTS = 200


def _sse_frame(data_lines: List[str], want_id: Any) -> Optional[Dict[str, Any]]:
    """Parse one SSE event's joined `data:` lines; return the frame
    only when its JSON-RPC `id` matches the request (or the request
    carried no id). Notifications (no `id`) and other requests'
    frames return None so the caller keeps scanning."""
    try:
        parsed = json.loads("\n".join(data_lines))
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    if want_id is not None and parsed.get("id") != want_id:
        return None
    return parsed


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
        try:
            r = s.post(self.url, headers=h, json=payload, timeout=30)
        except Exception as e:
            # Pass 28 #10: requests.ReadTimeout is an OSError, NOT
            # builtin TimeoutError, so MCPBridge.call_tool's
            # `except TimeoutError` (pass 27 #30) missed it and the
            # generic handler reported a DEFINITE failure. But a
            # read timeout is ambiguous — the request was sent and
            # the server may have acted; only the reply was lost.
            # Translate it at this transport boundary so callers
            # report UNVERIFIED instead. ConnectTimeout is
            # deliberately left alone: the request was never sent,
            # so a definite failure is correct there.
            import requests as _requests
            if isinstance(e, _requests.ReadTimeout):
                raise TimeoutError(
                    f"MCP HTTP read timed out after 30s: {e}") from e
            raise
        r.raise_for_status()
        if "mcp-session-id" in r.headers:
            self.session_id = r.headers["mcp-session-id"]

        text = r.text.strip()
        data = None
        # Pass 26 B4#3: SSE vs JSON used to be decided on
        # `"data:" in text` — a substring search over the whole
        # body, so a plain-JSON result containing e.g. "Useful
        # data: alpha" took the SSE branch, found no `data:`
        # line, and returned {} — silently discarding the real
        # result. Decide on Content-Type or a leading event: /
        # data: line, never a substring search; fall back to
        # r.json() when no data: line parses instead of {}.
        content_type = ""
        try:
            content_type = str(r.headers.get("Content-Type", "") or "").lower()
        except Exception:
            content_type = ""
        is_sse = ("text/event-stream" in content_type
                  or text.startswith("event:")
                  or text.startswith("data:"))
        if is_sse:
            # Pass 29 #31: the old branch took the FIRST `data:`
            # line and stopped — an SSE notification (no JSON-RPC
            # `id`) or a frame for another request arriving first
            # discarded the real result, so call_tool read an
            # empty result as a silent success. Scan every event,
            # join multiline `data:` lines per event, and take
            # only the frame whose `id` matches this request.
            want_id = payload.get("id")
            event_data: List[str] = []
            events_seen = 0
            for line in text.split("\n"):
                line = line.strip()
                if line == "":
                    if event_data:
                        events_seen += 1
                        data = _sse_frame(event_data, want_id)
                        event_data = []
                        if data is not None:
                            break
                        if events_seen >= _SSE_MAX_EVENTS:
                            break
                    continue
                if line.startswith("data:"):
                    event_data.append(line[5:].strip())
            if data is None and event_data \
                    and events_seen < _SSE_MAX_EVENTS:
                data = _sse_frame(event_data, want_id)
            if data is None:
                try:
                    data = r.json()
                except Exception:
                    data = {}
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
        try:
            return fut.result(timeout=timeout)
        except Exception:
            # Pass 26 B4#1: a timeout here used to leave the
            # coroutine running in the loop with no handle to
            # stop it. run_coroutine_threadsafe's future cancels
            # the underlying task.
            fut.cancel()
            raise

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
                if is_robinhood_url(cfg.get("url", "")) and "Authorization" not in headers:
                    tfile = os.path.join(config.WORKSPACE_DIR, "robinhood_token.json")
                    if os.path.exists(tfile):
                        try:
                            with open(tfile, "r") as tf:
                                tdata = json.load(tf)
                                tok = tdata.get("access_token")
                                if tok:
                                    headers["Authorization"] = f"Bearer {tok}"
                        except Exception as _e_silent:
                            config.log_silent("_server_actor", _e_silent)
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
                    if fut.cancelled():
                        # Pass 26 B3#9: the waiter timed out and
                        # cancelled this future — the call must
                        # never execute, only be dropped.
                        continue
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
                            if fut.cancelled():
                                # Pass 26 B3#9: timed-out waiter —
                                # drop, never execute.
                                continue
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
                            if fut.cancelled():
                                # Pass 26 B3#9: timed-out waiter —
                                # drop, never execute.
                                continue
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

    async def _spawn_actor(self, name: str, cfg: Dict[str, Any],
                           ready_timeout: float = 25.0):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        ready: asyncio.Future = loop.create_future()
        task = asyncio.create_task(
            self._server_actor(name, cfg, queue, ready), name=f"aria-mcp-{name}")
        try:
            tools = await asyncio.wait_for(asyncio.shield(ready),
                                           timeout=ready_timeout)
        except asyncio.TimeoutError:
            # Pass 26 B4#1: the old code let the timeout
            # propagate with the actor task still alive — it sat
            # in queue.get() holding the subprocess/session with
            # no session entry able to stop it. Cancel the actor
            # and wait briefly for it to exit, then re-raise.
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            raise
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
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.TimeoutError:
            # Pass 26 B3#9: shield() deliberately did NOT cancel
            # the future, so the actor would execute this call
            # later and settle the orphan. Cancel the orphan
            # here; the actor loop skips cancelled items.
            fut.cancel()
            raise

    async def _stop_actor(self, entry: Dict[str, Any]) -> None:
        task = entry.get("task")
        queue = entry.get("queue")
        if queue is not None:
            try:
                await asyncio.wait_for(queue.put(None), timeout=5.0)
            except Exception as _e_silent:
                config.log_silent("_stop_actor", _e_silent)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=10.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as _e_silent:
                config.log_silent("_stop_actor", _e_silent)

    def connect(self, name: str, timeout: float = 30.0,
                expected_config_digest: Optional[str] = None) -> Tuple[bool, str]:
        """Connect one server and register its tools. Returns (ok, message).

        Pass 27 #19: when expected_config_digest is given (an
        approved mcp_connect), the stored config must still match
        it — the check runs BEFORE spawning the server command, so
        a stale approval never launches a changed command.
        """
        servers = get_servers()
        key = (name or "").strip().lower()
        if key not in servers:
            return False, (f"No MCP server named '{name}'. "
                           f"Known: {', '.join(sorted(servers)) or 'none'}.")
        cfg = servers[key]
        if expected_config_digest is not None and \
                server_config_digest(key, cfg) != expected_config_digest:
            return False, (f"MCP server '{key}' configuration changed "
                           "since approval — re-approve to connect with "
                           "the new configuration.")
        if cfg.get("transport") != "http" and not _mcp_available():
            return False, _missing_dep_message()
        if key in self._sessions:
            entry = self._sessions[key]
            task = entry.get("task")
            if task is not None and task.done():
                # Pass 26 B4#2: a dead entry is not a connection.
                # Drop it (unregistering its tools) and reconnect
                # below instead of reporting "already connected".
                stale = self._sessions.pop(key, None)
                if stale is not None:
                    _unregister_server_tools(
                        key, stale.get("aria_names") or [])
            else:
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
        # Pass 27 #19: the registration revision the discovered
        # tools were approved against (see _registration_digest).
        entry["registration_digest"] = _registration_digest(tools)
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
        except Exception as _e_silent:
            config.log_silent("disconnect", _e_silent)
        _unregister_server_tools(key, entry.get("aria_names") or [])
        return True, f"Disconnected MCP server '{key}'."

    def disconnect_all(self) -> str:
        names = list(self._sessions.keys())
        for n in names:
            self.disconnect(n)
        return f"Disconnected {len(names)} MCP server(s)." if names else "No MCP servers were connected."

    def registration_digest(self, name: str) -> Optional[str]:
        """Digest of the tool registration currently live for a
        connected server (pass 27 #19); None when not connected."""
        entry = self._sessions.get((name or "").strip().lower())
        return entry.get("registration_digest") if entry else None

    def status(self) -> List[Dict[str, Any]]:
        out = []
        for name, cfg in sorted(get_servers().items()):
            entry = self._sessions.get(name)
            # Pass 26 B4#2: existence is not liveness — a dead
            # actor task reports disconnected.
            alive = (entry is not None and entry.get("task") is not None
                     and not entry["task"].done())
            out.append({
                "name": name,
                "transport": cfg.get("transport"),
                "enabled": bool(cfg.get("enabled", True)),
                "connected": alive,
                "tools": len(entry.get("aria_names") or [])
                       if alive else 0,
            })
        return out

    def call_tool(self, name: str, tool: str, arguments: Dict[str, Any],
                  timeout: float = 120.0) -> str:
        """Call a tool on a connected server; returns formatted text."""
        arguments = dict(arguments or {})
        # Pass 27 #19: a parked discovered-tool call carries the
        # registration digest it was approved against. The server
        # may have reconnected with different tools since — refuse
        # typed FAILED instead of executing the old approval
        # against the new registration. Calls with no digest (auto
        # mode) run as before; the key never reaches the server.
        approved = arguments.pop("_registration_digest", None)
        if approved is not None:
            current = self.registration_digest(name)
            if current is None:
                return outcome.failed(
                    f"mcp_{name}__{tool}",
                    f"MCP server '{name}' is not connected — cannot "
                    "verify the approved tool registration; re-approve "
                    "after reconnecting.")
            if current != approved:
                return outcome.failed(
                    f"mcp_{name}__{tool}",
                    f"MCP server '{name}' tool registration changed "
                    "since approval — re-approve to call the new "
                    "registration.")
        # Pass 28 #5: the parked call also carries the server
        # config digest it was approved against. A reconnect to a
        # reconfigured server with the same tool registration must
        # not ride the old approval; a server dropped from config
        # hashes as {} and so refuses as changed. Calls with no
        # digest (auto mode) run as before; the key never reaches
        # the server.
        approved_config = arguments.pop("_config_digest", None)
        if approved_config is not None:
            if server_config_digest(name) != approved_config:
                return outcome.failed(
                    f"mcp_{name}__{tool}",
                    f"MCP server '{name}' config changed since "
                    "approval — re-approve to call the new config.")
        try:
            result = self._run(self._actor_call(name, tool, arguments, timeout),
                               timeout=timeout + 10.0)
        except TimeoutError:
            # Pass 27 #30: a timeout is NOT a definite failure —
            # the call was queued to the actor and the server may
            # have acted before the reply was lost (the orphan is
            # cancelled, but a call already executing on the server
            # is not recalled). Report UNVERIFIED with the operation
            # identity, never FAILED: a FAILED here invites a blind
            # retry that could double-execute.
            return outcome.unverified(
                f"mcp_{name}__{tool}",
                f"MCP call to '{tool}' on server '{name}' timed out "
                f"after {timeout:g}s — the request was sent and the "
                f"server may have acted; check the server's state "
                f"before retrying")
        except Exception as e:
            # A failed call is a failure, not a result: the old
            # "[MCP call failed ...]" string had no status= prefix,
            # so normalize() read it as DONE (pass 25).
            return outcome.failed(
                f"mcp_{name}__{tool}",
                f"MCP call failed on '{name}': {e}")
        return format_tool_result(result, action=f"mcp_{name}__{tool}")


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

def _unique_tool_name(base: str, seen: set) -> str:
    """De-dupe a sanitized tool name without hanging (pass 25).

    The old loop appended "_2", "_3", ... AFTER truncating to 64
    chars, so for a base already >= 64 chars the suffix was
    truncated away, every candidate equalled the first name, and
    registration spun forever. Reserve suffix space before
    truncating, and bound the attempts."""
    name = sanitize_tool_name(base)
    if name not in seen:
        return name
    for i in range(2, 10002):
        suffix = f"_{i}"
        name = sanitize_tool_name(base[:64 - len(suffix)] + suffix)
        if name not in seen:
            return name
    raise RuntimeError(f"could not de-dupe MCP tool name for {base!r}")


def _register_server_tools(server: str, tools: List[Any]) -> List[str]:
    """Register each MCP tool as an ARIA tool. Returns the ARIA tool names."""
    from aria.tools import schemas as _schemas
    from aria.tools.dispatch import register_tool
    bridge = get_bridge()
    aria_names: List[str] = []
    seen = set()
    for t in tools:
        # Pass 27 #4: an empty (or otherwise unclassifiable)
        # remote name used to register as mcp_<server>__, which
        # the policy regex — requiring a non-empty tool part —
        # does not classify as a discovered tool, so the handler
        # ran ungated in safe mode. Reject such names here and
        # never register them; the server keeps its other tools.
        remote_name = getattr(t, "name", "")
        if (not isinstance(remote_name, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", remote_name)):
            config.log_silent(
                "_register_server_tools",
                ValueError(
                    f"refused MCP tool with invalid remote name "
                    f"{remote_name!r} from server {server!r}"))
            continue
        base = f"mcp_{server}__{remote_name}"
        aria_name = _unique_tool_name(base, seen)
        seen.add(aria_name)
        schema = getattr(t, "inputSchema", None) or {}
        desc = getattr(t, "description", "") or remote_name
        decl = {
            "name": aria_name,
            "description": f"[MCP:{server}] {desc}".strip()[:600],
            "parameters": convert_schema(schema),
        }
        _schemas.register_dynamic_tool_declaration(aria_name, decl, toolkit="mcp")
        mcp_tool_name = remote_name
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
    # Pass 26 B4#2: the dispatch handler registered by
    # _register_server_tools via register_tool lives in
    # dispatch._REGISTRY, and there is no unregister_tool in
    # aria/ — pop the name from the registry here so a
    # disconnected server's tools stop being dispatchable.
    # (dispatch.py belongs to another pass-26 agent's file set;
    # the registry dict itself is importable.)
    try:
        from aria.tools import dispatch as _dispatch
        registry = getattr(_dispatch, "_REGISTRY", None)
        if registry is not None:
            for n in aria_names:
                registry.pop(n, None)
    except Exception as _e_silent:
        config.log_silent("_unregister_server_tools", _e_silent)


def disconnect(name: str) -> Tuple[bool, str]:
    return get_bridge().disconnect(name)


def autoconnect_enabled_servers() -> str:
    """Connect every enabled server; failures are collected, never raised.

    Pass 25: safe mode hard-blocks MCP setup/connect (config.py),
    but startup autoconnect called this with no safe-mode check and
    spawned server commands anyway. Refuse outright in safe mode —
    nothing connects, nothing spawns.
    """
    if config.is_safe_mode():
        summary = "skipped: safe mode is on (MCP autoconnect disabled)"
        try:
            config.add_log(f"[ARIA] MCP autoconnect: {summary}")
        except Exception:
            pass
        return summary
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
    except Exception as _e_silent:
        config.log_silent("autoconnect_enabled_servers", _e_silent)
    return summary


def format_tool_result(result: Any, action: str = "mcp_tool") -> str:
    """Flatten an MCP CallToolResult into readable text.

    An isError result is a failure, not a result: the old shape
    prefixed it with "[MCP tool error] " only, and normalize()
    read it as DONE — a failed remote tool certified its own
    mission step (pass 25). Type it FAILED via outcome.failed;
    the error text is the reason.
    """
    content = getattr(result, "content", None) or []
    chunks: List[str] = []
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
    if getattr(result, "isError", False):
        return outcome.failed(action, text)
    return text
