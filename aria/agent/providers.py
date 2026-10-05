"""
ARIA Provider fallback chain (Phase 1: Gemini pool -> Groq).

Every provider normalizes to the internal parts-dict shape:
    {"candidates": [{"content": {"parts": [...]}}]}
with "functionCall" / "text" parts, so run_agent's tool loop stays
provider-agnostic. Only the gemini_call(...) call site changes.

Recovery needs no timer: the existing key-quarantine mechanism IS the
recovery mechanism. A rate-limited provider is skipped while its keys are
quarantined and retried automatically once the quarantine expires.
"""

from __future__ import annotations

import hashlib
import json
import socket
import time
import urllib.error
import urllib.request
from typing import Optional, Callable, List, Dict, Any

from aria.config import (
    GEMINI_KEY_POOL, GROQ_API_KEY, GROQ_MODEL,
    PROVIDER_CHAIN, KEY_QUARANTINE_DURATION_S,
    quarantine_key, key_is_quarantined, key_mask, add_log,
)

ACTIVE_PROVIDER = "gemini"


def get_active_provider() -> str:
    return ACTIVE_PROVIDER


# --------------------------------------------------------------------------
# Translation: Gemini-native <-> OpenAI-compatible
# --------------------------------------------------------------------------

def _synth_tool_id(name: str, args: Dict[str, Any]) -> str:
    """Deterministic synthetic tool-call id from name+args (md5, not hash():
    stable within the process and collision-safe enough for a turn)."""
    try:
        digest = hashlib.md5(
            (name + json.dumps(args, sort_keys=True)).encode("utf-8")
        ).hexdigest()[:12]
    except Exception:
        digest = "000000000000"
    return f"call_{digest}"


def gemini_contents_to_oai_messages(
    system_instruction: str,
    contents: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Gemini parts-based contents -> OpenAI chat messages.

    functionCall parts become tool_calls (with synthetic stable ids);
    functionResponse parts become tool messages matched FIFO per tool name.
    inline_data (images) are dropped — vision is Gemini-only.
    """
    messages: List[Dict[str, Any]] = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})

    pending_ids: Dict[str, List[str]] = {}  # tool name -> FIFO of synth ids

    for block in contents or []:
        role = block.get("role")
        parts = block.get("parts") or []

        if role == "model":
            text_bits = [p["text"] for p in parts if isinstance(p.get("text"), str)]
            tool_calls = []
            for p in parts:
                fc = p.get("functionCall")
                if not isinstance(fc, dict):
                    continue
                name = fc.get("name", "")
                args = fc.get("args", {}) or {}
                # Reuse the inbound OpenAI id when this turn already crossed
                # providers; otherwise synthesize a stable one.
                tid = fc.get("_oai_id") or _synth_tool_id(name, args)
                pending_ids.setdefault(name, []).append(tid)
                tool_calls.append({
                    "id": tid,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args),
                    },
                })
            msg: Dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(text_bits) if text_bits else None,
            }
            if tool_calls:
                msg["tool_calls"] = tool_calls
            messages.append(msg)
        else:
            # user role: text parts -> user message; functionResponse -> tool
            text_bits = [p["text"] for p in parts if isinstance(p.get("text"), str)]
            if text_bits:
                messages.append({"role": "user", "content": "\n".join(text_bits)})
            for p in parts:
                fr = p.get("functionResponse")
                if not isinstance(fr, dict):
                    continue
                name = fr.get("name", "")
                tids = pending_ids.get(name) or []
                tid = tids.pop(0) if tids else _synth_tool_id(name, {})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": json.dumps(fr.get("response", {})),
                })
    return messages


def gemini_decls_to_oai_tools(
    tool_decls: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Gemini function_declarations -> OpenAI tools array."""
    tools: List[Dict[str, Any]] = []
    for entry in tool_decls or []:
        for fd in entry.get("function_declarations", []) or []:
            tools.append({
                "type": "function",
                "function": {
                    "name": fd.get("name", ""),
                    "description": fd.get("description", ""),
                    "parameters": fd.get("parameters")
                    or {"type": "object", "properties": {}},
                },
            })
    return tools


def oai_response_to_parts(resp_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """OpenAI chat-completion JSON -> Gemini-style parts."""
    parts: List[Dict[str, Any]] = []
    try:
        choice = (resp_json.get("choices") or [{}])[0]
        msg = choice.get("message", {}) or {}
        content = msg.get("content")
        if isinstance(content, str) and content:
            parts.append({"text": content})
        for tc in msg.get("tool_calls") or []:
            fn = (tc or {}).get("function", {}) or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else {}
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            parts.append({
                "functionCall": {
                    "name": fn.get("name", ""),
                    "args": args,
                    "_oai_id": tc.get("id"),
                }
            })
    except Exception as e:
        add_log(f"oai_response_to_parts parse error: {e}")
    return parts


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

class Provider:
    name = "base"

    def is_available(self) -> bool:
        return False

    def call(
        self,
        system_instruction: str,
        contents: List[Dict[str, Any]],
        tool_decls: Optional[List[Dict[str, Any]]] = None,
        on_text_chunk: Optional[Callable[[Optional[str]], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        return None


class GeminiProvider(Provider):
    """Wraps the existing gemini_call — zero behavior change."""

    name = "gemini"

    def is_available(self) -> bool:
        pool = GEMINI_KEY_POOL or []
        if not pool:
            return False
        return any(not key_is_quarantined(k) for k in pool)

    def call(self, system_instruction, contents, tool_decls=None,
             on_text_chunk=None):
        from aria.agent.brain import gemini_call  # deferred: avoids circular import
        return gemini_call(
            system_instruction, contents,
            tool_decls=tool_decls,
            on_text_chunk=on_text_chunk,
        )


class OpenAICompatProvider(Provider):
    """One class for Groq / OpenRouter / Mistral (and Ollama's /v1)."""

    def __init__(self, name: str, base_url: str, api_key: str, model: str):
        self.name = name
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.last_error: Optional[str] = None

    def is_available(self) -> bool:
        return bool(self.base_url and self.model) and not key_is_quarantined(self.api_key)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _parse_sse_stream(self, resp, on_text_chunk) -> List[Dict[str, Any]]:
        """Accumulate OpenAI SSE deltas -> parts. Feeds on_text_chunk like Gemini."""
        text_buf = ""
        tc_accum: Dict[int, Dict[str, Any]] = {}
        try:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data_str)
                except Exception:
                    continue
                delta = ((chunk.get("choices") or [{}])[0]).get("delta", {}) or {}
                c = delta.get("content")
                if isinstance(c, str) and c:
                    text_buf += c
                    if on_text_chunk:
                        try:
                            on_text_chunk(c)
                        except Exception:
                            pass
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    acc = tc_accum.setdefault(idx, {"id": None, "name": None, "args": ""})
                    if tc.get("id"):
                        acc["id"] = tc["id"]
                    fn = tc.get("function", {}) or {}
                    if fn.get("name"):
                        acc["name"] = fn["name"]
                    if fn.get("arguments"):
                        acc["args"] += fn["arguments"]
        finally:
            if on_text_chunk:
                try:
                    on_text_chunk(None)  # stream-complete signal, mirrors Gemini path
                except Exception:
                    pass

        parts: List[Dict[str, Any]] = []
        if text_buf:
            parts.append({"text": text_buf})
        for idx in sorted(tc_accum):
            acc = tc_accum[idx]
            try:
                args = json.loads(acc["args"]) if acc["args"] else {}
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            parts.append({
                "functionCall": {
                    "name": acc["name"] or "",
                    "args": args,
                    "_oai_id": acc["id"],
                }
            })
        return parts

    def call(self, system_instruction, contents, tool_decls=None, on_text_chunk=None):
        if not self.is_available():
            return None
        messages = gemini_contents_to_oai_messages(system_instruction, contents)
        tools = gemini_decls_to_oai_tools(tool_decls) if tool_decls else None
        payload: Dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
        url = self.base_url + "/chat/completions"
        body = json.dumps(payload).encode("utf-8")

        for attempt in range(3):
            try:
                if on_text_chunk:
                    payload["stream"] = True
                    sbody = json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(url, data=sbody, headers=self._headers())
                    with urllib.request.urlopen(req, timeout=90) as resp:
                        parts = self._parse_sse_stream(resp, on_text_chunk)
                    return {"candidates": [{"content": {"parts": parts}}]}
                req = urllib.request.Request(url, data=body, headers=self._headers())
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return {"candidates": [{"content": {"parts": oai_response_to_parts(data)}}]}
            except urllib.error.HTTPError as e:
                try:
                    detail = e.read().decode("utf-8", errors="replace")[:200]
                except Exception:
                    detail = ""
                self.last_error = f"HTTP {e.code}: {detail}".strip()
                if e.code == 429:
                    quarantine_key(self.api_key, 60, 429)
                    add_log(f"{self.name}: rate-limited (429), key quarantined 60s")
                    return None
                if e.code in (400, 401, 402, 403, 404):
                    quarantine_key(self.api_key, KEY_QUARANTINE_DURATION_S, e.code)
                    add_log(f"{self.name}: HTTP {e.code} ({key_mask(self.api_key)}), key quarantined")
                    return None
                if e.code in (500, 502, 503, 504):
                    add_log(f"{self.name}: HTTP {e.code}, retrying...")
                    time.sleep(2 * (attempt + 1))
                    continue
                add_log(f"{self.name}: unhandled HTTP {e.code}")
                return None
            except (urllib.error.URLError, TimeoutError, ConnectionError,
                    socket.timeout) as e:
                add_log(f"{self.name}: network drop ({e}), retrying...")
                time.sleep(2 * (attempt + 1))
                continue
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                add_log(f"{self.name}: error: {e}")
                return None
        self.last_error = self.last_error or "no response after retries"
        return None


def _build_chain() -> List[Provider]:
    """Providers in configured order. Phase 2/3 entries plug in here."""
    registry: Dict[str, Provider] = {
        "gemini": GeminiProvider(),
        "groq": OpenAICompatProvider(
            "groq", "https://api.groq.com/openai/v1", GROQ_API_KEY, GROQ_MODEL),
        # Phase 2: "openrouter", "mistral". Phase 3: "ollama".
    }
    return [registry[n] for n in PROVIDER_CHAIN if n in registry]


def get_active_provider() -> str:
    return ACTIVE_PROVIDER


def provider_call(
    system_instruction: str,
    contents: List[Dict[str, Any]],
    tool_decls: Optional[List[Dict[str, Any]]] = None,
    on_text_chunk: Optional[Callable[[Optional[str]], None]] = None,
) -> Optional[Dict[str, Any]]:
    """Walk the provider chain in order; first provider that returns
    candidates wins. Quarantined/unconfigured providers are skipped
    without network traffic, so recovery is automatic on quarantine expiry."""
    global ACTIVE_PROVIDER
    chain = _build_chain()
    if not chain:
        add_log("provider_call: chain is empty, nothing configured")
        return None
    for provider in chain:
        if not provider.is_available():
            continue
        try:
            data = provider.call(
                system_instruction, contents,
                tool_decls=tool_decls, on_text_chunk=on_text_chunk,
            )
        except Exception as e:  # never let one provider kill the turn
            add_log(f"provider {provider.name} raised: {e}")
            data = None
        if data and data.get("candidates"):
            if ACTIVE_PROVIDER != provider.name:
                note = " (fallback)" if provider.name != "gemini" else " (recovered)"
                add_log(f"provider now serving: {provider.name}{note}")
                ACTIVE_PROVIDER = provider.name
            return data
        add_log(f"provider {provider.name} returned nothing, trying next")
    add_log("provider_call: chain exhausted, all providers down")
    return None


def provider_ping() -> Dict[str, Dict[str, Any]]:
    """Dry-run: one 'pong' turn per configured provider. For --provider-ping."""
    results: Dict[str, Dict[str, Any]] = {}
    for provider in _build_chain():
        if not provider.is_available():
            results[provider.name] = {"ok": False, "why": "unavailable (no key or quarantined)"}
            continue
        t0 = time.time()
        try:
            data = provider.call(
                "You are a connectivity test.",
                [{"role": "user", "parts": [{"text": "Reply with exactly: pong"}]}],
                tool_decls=None, on_text_chunk=None,
            )
            parts = ((data or {}).get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts if "text" in p)
            entry: Dict[str, Any] = {
                "ok": bool(text.strip()),
                "latency_s": round(time.time() - t0, 2),
                "reply": text.strip()[:80],
            }
            err = getattr(provider, "last_error", None)
            if not entry["ok"] and err:
                entry["why"] = err
            results[provider.name] = entry
        except Exception as e:
            results[provider.name] = {"ok": False, "why": str(e)[:120]}
    return results


if __name__ == "__main__":
    import sys
    if "--provider-ping" in sys.argv:
        print(json.dumps(provider_ping(), indent=2))
    else:
        print("usage: python -m aria.agent.providers --provider-ping")
