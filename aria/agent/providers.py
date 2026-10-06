"""
ARIA provider fallback chain (default order: ollama_cloud -> groq -> openrouter
-> mistral -> gemini, configurable via PROVIDER_CHAIN).

Every provider normalizes to the internal parts-dict shape:
    {"candidates": [{"content": {"parts": [...]}}]}
with "functionCall" / "text" parts, so run_agent's tool loop stays
provider-agnostic. Non-OpenAI-native wire formats (the legacy Gemini-style
parts dicts) are translated to OpenAI-compatible chat messages on the way
in and back to parts on the way out.

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
    GROQ_API_KEY, GROQ_MODEL,
    OPENROUTER_API_KEY, OPENROUTER_MODEL, MISTRAL_API_KEY, MISTRAL_MODEL,
    OLLAMA_CLOUD_API_KEY, OLLAMA_CLOUD_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL,
    OLLAMA_VISION_MODEL, OLLAMA_CODE_MODEL,
    PROVIDER_CHAIN, KEY_QUARANTINE_DURATION_S,
    quarantine_key, key_is_quarantined, key_mask, add_log,
)

ACTIVE_PROVIDER = PROVIDER_CHAIN[0] if PROVIDER_CHAIN else "ollama_cloud"


# Session stats for the OPS dashboard: failover count, per-provider call
# counts, and the most recent failover (failed_name, epoch).
PROVIDER_STATS: Dict[str, Any] = {
    "failovers": 0, "calls": {}, "last_failover": None,
}


def _note_failover(failed_name: str) -> None:
    PROVIDER_STATS["failovers"] += 1
    PROVIDER_STATS["last_failover"] = (failed_name, time.time())


def _note_call(name: str) -> None:
    PROVIDER_STATS["calls"][name] = PROVIDER_STATS["calls"].get(name, 0) + 1


def get_active_provider() -> str:
    return ACTIVE_PROVIDER


# Per-call stats for the OPS inspector: latency + token usage of the most
# recent successful model call. Usage comes from the provider's `usage`
# block when present (OpenAI-compatible); absent means "not reported".
LAST_CALL_STATS: Dict[str, Any] = {}


def get_last_call_stats() -> Dict[str, Any]:
    return dict(LAST_CALL_STATS)


def _record_call_stats(provider_name: str, latency_ms: int, usage: Any) -> None:
    try:
        u = usage if isinstance(usage, dict) else {}
        LAST_CALL_STATS.clear()
        LAST_CALL_STATS.update({
            "provider": provider_name,
            "latency_ms": latency_ms,
            "prompt_tokens": u.get("prompt_tokens"),
            "completion_tokens": u.get("completion_tokens"),
        })
    except Exception:
        pass


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
    inline_data (images) become OpenAI image_url parts (data: URLs) so
    vision-capable models can see them; pure-text parts stay plain strings.
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
            # user role: text parts -> user message; functionResponse -> tool.
            # inline_data images become image_url parts (vision-capable
            # models); when images are present the message uses the multipart
            # content-item form, otherwise the plain joined string as before.
            images = [p["inline_data"] for p in parts
                      if isinstance(p.get("inline_data"), dict) and p["inline_data"].get("data")]
            if images:
                # Per Ollama's multimodal guidance: image content BEFORE text.
                content_items: List[Dict[str, Any]] = []
                for idata in images:
                    mime = idata.get("mime_type", "image/jpeg")
                    content_items.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{idata['data']}"},
                    })
                for p in parts:
                    if isinstance(p.get("text"), str):
                        content_items.append({"type": "text", "text": p["text"]})
                messages.append({"role": "user", "content": content_items})
            else:
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


def _lowercase_schema_types(obj: Any) -> Any:
    """Recursively lowercase Gemini-style type names for OpenAI schemas.

    ARIA's declarations use Gemini conventions ("STRING", "OBJECT"); the
    OpenAI-compatible endpoints 400 on anything but lowercase. Normalizing
    here (not in schemas.py) leaves the declaration files untouched and
    covers dynamic/MCP declarations too.
    """
    if isinstance(obj, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str)
                    else _lowercase_schema_types(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_lowercase_schema_types(v) for v in obj]
    return obj


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
                    "parameters": _lowercase_schema_types(
                        fd.get("parameters")
                        or {"type": "object", "properties": {}}),
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


class OpenAICompatProvider(Provider):
    """One class for Groq / OpenRouter / Mistral (and Ollama's /v1)."""

    def __init__(self, name: str, base_url: str, api_key: str, model: str,
                 extra_headers: Optional[Dict[str, str]] = None):
        self.name = name
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.extra_headers = extra_headers or {}
        self.last_error: Optional[str] = None

    def is_available(self) -> bool:
        return bool(self.base_url and self.model) and not key_is_quarantined(self.api_key)

    def _headers(self) -> Dict[str, str]:
        # Groq sits behind Cloudflare, which 403s (error 1010) Python's
        # default urllib User-Agent before the API key is even read.
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ARIA-Agent/1.0 (Windows NT 10.0; Win64; x64)",
        }
        headers.update(self.extra_headers)
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _parse_sse_stream(self, resp, on_text_chunk) -> List[Dict[str, Any]]:
        """Accumulate OpenAI SSE deltas -> parts. Feeds on_text_chunk per
        chunk; a final on_text_chunk(None) signals stream completion.
        Captures the `usage` block when the provider sends one (usually in
        the final chunk) into self._stream_usage."""
        text_buf = ""
        tc_accum: Dict[int, Dict[str, Any]] = {}
        self._stream_usage = None
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
                if isinstance(chunk.get("usage"), dict):
                    self._stream_usage = chunk["usage"]
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
                    on_text_chunk(None)  # stream-complete signal: brain treats None as done
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
        # Fail fast (Alek 2026-10-05): one attempt, then the turn moves to the
        # next provider immediately. No retries, no backoff sleeps.
        self.last_error = None
        if not self.is_available():
            return None
        messages = gemini_contents_to_oai_messages(system_instruction, contents)
        tools = gemini_decls_to_oai_tools(tool_decls) if tool_decls else None
        payload: Dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
        url = self.base_url + "/chat/completions"
        body = json.dumps(payload).encode("utf-8")

        t0 = time.time()
        try:
            if on_text_chunk:
                payload["stream"] = True
                sbody = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=sbody, headers=self._headers())
                with urllib.request.urlopen(req, timeout=90) as resp:
                    parts = self._parse_sse_stream(resp, on_text_chunk)
                _record_call_stats(self.name, int((time.time() - t0) * 1000),
                                   getattr(self, "_stream_usage", None))
                return {"candidates": [{"content": {"parts": parts}}]}
            req = urllib.request.Request(url, data=body, headers=self._headers())
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            _record_call_stats(self.name, int((time.time() - t0) * 1000),
                               data.get("usage"))
            return {"candidates": [{"content": {"parts": oai_response_to_parts(data)}}]}
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                detail = ""
            self.last_error = f"HTTP {e.code}: {detail}".strip()
            if e.code == 429:
                quarantine_key(self.api_key, 60, 429)
                add_log(f"{self.name}: rate-limited (429), key quarantined 60s: "
                        f"{detail[:120]}")
                return None
            if e.code in (400, 401, 402, 403, 404):
                # 400 is a malformed request (OUR payload), not a bad key:
                # never quarantine — the key is fine and the main loop must
                # not pay for a bad vision payload or model tag.
                if e.code == 400:
                    add_log(f"{self.name}: HTTP 400 (bad request, key NOT quarantined): "
                            f"{detail[:120]}")
                    return None
                # 401/403 (bad key) and 402/404 keep the long quarantine.
                quarantine_key(self.api_key, KEY_QUARANTINE_DURATION_S, e.code)
                add_log(f"{self.name}: HTTP {e.code} ({key_mask(self.api_key)}): "
                        f"{detail[:120]}")
                return None
            if e.code in (500, 502, 503, 504):
                # Transient: move on now, retried next turn (no quarantine).
                add_log(f"{self.name}: HTTP {e.code}, moving on")
                return None
            add_log(f"{self.name}: unhandled HTTP {e.code}")
            return None
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                socket.timeout) as e:
            self.last_error = f"network drop: {e}"[:200]
            quarantine_key(self.api_key, 60, "network_drop")
            add_log(f"{self.name}: network drop, moving on")
            return None
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            add_log(f"{self.name}: error: {e}")
            return None

def _build_chain() -> List[Provider]:
    """Providers in configured order. Phase 2/3 entries plug in here."""
    registry: Dict[str, Provider] = {
        "groq": OpenAICompatProvider(
            "groq", "https://api.groq.com/openai/v1", GROQ_API_KEY, GROQ_MODEL),
        "openrouter": OpenAICompatProvider(
            "openrouter", "https://openrouter.ai/api/v1",
            OPENROUTER_API_KEY, OPENROUTER_MODEL,
            extra_headers={"HTTP-Referer": "https://github.com/ABerger94/ARIA-Agent",
                           "X-Title": "ARIA-Agent"}),
        "mistral": OpenAICompatProvider(
            "mistral", "https://api.mistral.ai/v1", MISTRAL_API_KEY, MISTRAL_MODEL),
        "ollama_cloud": OpenAICompatProvider(
            "ollama_cloud", "https://ollama.com/v1",
            OLLAMA_CLOUD_API_KEY, OLLAMA_CLOUD_MODEL),
        "gemini": OpenAICompatProvider(
            "gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
            GEMINI_API_KEY, GEMINI_MODEL),
    }
    return [registry[n] for n in PROVIDER_CHAIN if n in registry]


def get_active_provider() -> str:
    return ACTIVE_PROVIDER


# --- Role-based model routing (Ollama Cloud) ---
# Routes by ROLE, never by hard-coded model name at call sites: if a tag is
# retired or 403s, set OLLAMA_VISION_MODEL / OLLAMA_CODE_MODEL to "" (or a new
# tag) in aria_keys.json and the role silently falls back to the default
# OLLAMA_CLOUD_MODEL. The conversational loop always uses the default.

def resolve_role_model(role: str) -> Optional[str]:
    """Map a task role to an Ollama Cloud model tag.

    Returns None for the default/unknown roles (provider's configured model)
    and when the role's override is empty/disabled — or when the tag is in
    failure cooldown (a broken role model falls back silently instead of
    being hammered every turn).
    """
    r = (role or "default").strip().lower()
    tag: Optional[str] = None
    if r == "vision":
        tag = OLLAMA_VISION_MODEL or None
    elif r == "code":
        tag = OLLAMA_CODE_MODEL or None
    if tag and _ROLE_MODEL_COOLDOWN.get(tag, 0) > time.time():
        add_log(f"role '{r}': model '{tag}' in failure cooldown, using default")
        return None
    return tag


# --- Dynamic routing: failure-adaptive role models ---
# When a role model fails (exception or empty result), _attempt_provider_call
# falls back to the default model AND parks the broken tag here so later
# turns don't keep trying it. Cooldown is 15 minutes — long enough to stop
# the hammering, short enough to recover if the tag comes back.
_ROLE_MODEL_COOLDOWN: Dict[str, float] = {}
ROLE_MODEL_COOLDOWN_S = 900


def _note_role_model_failed(tag: Optional[str]) -> None:
    try:
        if tag:
            _ROLE_MODEL_COOLDOWN[tag] = time.time() + ROLE_MODEL_COOLDOWN_S
            add_log(f"role model '{tag}' parked for {ROLE_MODEL_COOLDOWN_S // 60} min after failure")
    except Exception:
        pass


def clear_role_model_cooldown() -> None:
    """Reset failure-adaptive role parking (used by tests; also handy if
    Alek swaps a model tag and wants the new one tried immediately)."""
    try:
        _ROLE_MODEL_COOLDOWN.clear()
    except Exception:
        pass


def chain_all_quarantined() -> bool:
    """True when every configured provider's key is currently quarantined
    (e.g. all 429'd) — the chain has no road, not just a flat tire."""
    try:
        chain = _build_chain()
        keyed = [p for p in chain if p.api_key]
        if not keyed:
            return False
        return all(key_is_quarantined(p.api_key) for p in keyed)
    except Exception:
        return False


def _attempt_provider_call(provider, system_instruction, contents, tool_decls,
                           on_text_chunk, model_override: Optional[str]):
    """Call one provider, optionally on a role-specific model.

    A failing role model (exception OR empty result — provider.call swallows
    HTTP errors into last_error + None) falls back to the provider's default
    model WITHOUT quarantining the API key (the key is fine; the tag or
    payload was bad). The provider's configured model is always restored.
    Returns (data, used_fallback).
    """
    use_override = bool(model_override) and provider.name == "ollama_cloud"
    saved = provider.model if use_override else None
    try:
        if use_override:
            provider.model = model_override
        try:
            data = provider.call(
                system_instruction, contents,
                tool_decls=tool_decls, on_text_chunk=on_text_chunk,
            )
        except Exception as e:
            data = None
            provider.last_error = f"raised: {e}"
        if data and data.get("candidates"):
            return data, False
        if not use_override:
            return data, False
        add_log(f"provider {provider.name}: role model '{model_override}' failed "
                f"({provider.last_error}); retrying default model (key NOT quarantined)")
        _note_role_model_failed(model_override)
        provider.model = saved
        try:
            data = provider.call(
                system_instruction, contents,
                tool_decls=tool_decls, on_text_chunk=on_text_chunk,
            )
        except Exception as e:
            data = None
            provider.last_error = f"raised: {e}"
        return data, True
    finally:
        if saved is not None:
            provider.model = saved


def provider_call(
    system_instruction: str,
    contents: List[Dict[str, Any]],
    tool_decls: Optional[List[Dict[str, Any]]] = None,
    on_text_chunk: Optional[Callable[[Optional[str]], None]] = None,
    model_override: Optional[str] = None,
    only_provider: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Walk the provider chain in order; first provider that returns
    candidates wins. Quarantined/unconfigured providers are skipped
    without network traffic, so recovery is automatic on quarantine expiry.

    model_override: temporarily run the ollama_cloud leg on a different
    model tag (see resolve_role_model). Prefer passing role= to
    provider_text; the main agent loop leaves this unset.
    only_provider: restrict the walk to a single named provider (used for
    vision, where only ollama_cloud can serve the image payload — walking
    the other providers would 400/404/403 and quarantine THEIR keys).
    """
    global ACTIVE_PROVIDER
    chain = _build_chain()
    if not chain:
        add_log("provider_call: chain is empty, nothing configured")
        return None
    for provider in chain:
        if only_provider and provider.name != only_provider:
            continue
        if not provider.is_available():
            continue
        _note_call(provider.name)
        try:
            data, _used_fallback = _attempt_provider_call(
                provider, system_instruction, contents, tool_decls,
                on_text_chunk, model_override,
            )
        except Exception as e:  # never let one provider kill the turn
            add_log(f"provider {provider.name} raised: {e}")
            _note_failover(provider.name)
            data = None
        if data and data.get("candidates"):
            if ACTIVE_PROVIDER != provider.name:
                note = " (fallback)"
                add_log(f"provider now serving: {provider.name}{note}")
                ACTIVE_PROVIDER = provider.name
            return data
        _note_failover(provider.name)
        add_log(f"provider {provider.name} returned nothing, trying next")
    add_log("provider_call: chain exhausted, all providers down")
    return None


def provider_text(system_instruction: str, user_text: str, role: str = "default") -> str:
    """One-shot plain-text call through the provider chain (no tools).

    Used by tools that need a quick classification/read, e.g. triage.
    role: "default" (conversational model), "vision", or "code" — resolved
    via resolve_role_model(); unknown/empty roles use the default model.
    Returns the concatenated response text, or a bracketed error string.
    """
    try:
        contents = [{"role": "user", "parts": [{"text": str(user_text)}]}]
        data = provider_call(system_instruction, contents, tool_decls=None,
                             model_override=resolve_role_model(role))
    except Exception as e:
        add_log(f"provider_text: chain error: {e}")
        return f"[provider_text error: {e}]"
    if not data or not data.get("candidates"):
        return "[No provider responded.]"
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as e:
        add_log(f"provider_text: unparseable response: {e}")
        return "[Unparseable provider response.]"
    text = "".join(
        p.get("text", "") for p in parts if isinstance(p, dict)
    ).strip()
    return text or "[No text in provider response.]"


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
