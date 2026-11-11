"""
ARIA provider fallback chain (default order: gemini -> ollama_cloud -> groq
-> openrouter -> mistral -> ollama_local, configurable via PROVIDER_CHAIN).

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
import copy
import inspect
import json
import math
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Optional, Callable, List, Dict, Any

from aria.config import (
    GROQ_API_KEY, GROQ_MODEL,
    OPENROUTER_API_KEY, OPENROUTER_MODEL, MISTRAL_API_KEY, MISTRAL_MODEL,
    OLLAMA_CLOUD_API_KEY, OLLAMA_CLOUD_MODEL,
    GEMINI_API_KEY, GEMINI_API_KEYS, GEMINI_MODEL,
    FREELLMAPI_API_KEY, FREELLMAPI_MODEL, FREELLMAPI_BASE_URL,
    OLLAMA_VISION_MODEL, OLLAMA_CODE_MODEL,
    OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_LOCAL_TIMEOUT_S,
    OLLAMA_LOCAL_VISION_MODEL, OLLAMA_LOCAL_VISION_TIMEOUT_S,
    OLLAMA_NUM_CTX,
    OLLAMA_ALLOW_REMOTE,
    PROVIDER_CHAIN, PROVIDER_MODE, KEY_QUARANTINE_DURATION_S,  # noqa: F401 — PROVIDER_MODE is this module's live mode global: read via provider_mode(), rebound by set_provider_mode(), patched directly by tests
    quarantine_key, key_is_quarantined, key_mask, add_log,
)
from aria import config as _config_silent
from aria.agent.cooldown import CooldownRegistry
from aria import runtime, usage

# Live PROVIDER_MODE. The Settings → Providers "Local only"
# toggle (aria/providers_panel.py) flips the mode WITHOUT a
# restart, so every gate in this module reads provider_mode() —
# this module's PROVIDER_MODE global, read fresh at call time —
# rather than a value captured at import. set_provider_mode() is
# the single entry point that keeps the live global, config's
# global, and the keys file in step: it persists through
# config.set_provider_mode() and rebinds this module's global,
# which is also the name the existing tests patch directly.
def provider_mode() -> str:
    """The live PROVIDER_MODE: exactly "auto" or "local_only"
    (any other stored value behaves as auto, matching config's
    normalization)."""
    raw = str(globals().get("PROVIDER_MODE", "auto") or "auto")
    return "local_only" if raw.strip().lower() == "local_only" else "auto"


# Pass 36 #06: the mode generation. Every real mode flip bumps
# it; a turn captures (mode, generation) once in
# current_request_policy() and the walk re-checks both before
# every transport attempt, so a toggle flip mid-turn aborts the
# walk instead of letting stale legs keep calling the cloud.
_MODE_GENERATION = 0


def current_request_policy() -> Dict[str, Any]:
    """The authoritative mode + generation for one turn. Capture
    once at turn start and pass it to resolve_role_model() and
    provider_call() so the role tag, the chain, and the walk all
    resolve from the SAME policy instead of three live reads."""
    return {"mode": provider_mode(), "generation": _MODE_GENERATION}


def set_provider_mode(mode) -> str:
    """Flip PROVIDER_MODE live and persist it (the Settings
    toggle's backend). Takes effect on the next provider call —
    the chain walk, role routing, failure wording, the Live gate,
    and vision all read the live value. Returns the effective
    mode.

    Pass 36 #07: the persistence result is no longer discarded.
    PROVIDER_MODE_STATUS carries it — {"mode", "saved", "error",
    "env_override", "source"} — so the panel can show "saved" vs
    "NOT saved" and warn when an env var beats the stored value
    at the next restart; an invalid explicit value is a visible
    configuration error (see config.set_provider_mode_result),
    not a silent coercion. provider_mode_status() reads it."""
    global PROVIDER_MODE, _MODE_GENERATION, ACTIVE_PROVIDER
    previous_mode = provider_mode()
    try:
        result = _config_silent.set_provider_mode_result(mode)
    except Exception:
        result = {"mode": ("local_only" if str(mode or "").strip().lower()
                           == "local_only" else "auto"),
                  "saved": False, "error": None, "env_override": False,
                  "source": "unknown"}
    PROVIDER_MODE = result["mode"]
    PROVIDER_MODE_STATUS.clear()
    PROVIDER_MODE_STATUS.update(result)
    # Pass 36 #06: a real flip bumps the generation so in-flight
    # walks notice it at their next per-attempt recheck. Setting
    # the mode that is already live changes nothing and bumps nothing.
    if result["mode"] != previous_mode:
        _MODE_GENERATION += 1
        # Pass 36 #18: the last-serving provider belongs to the old
        # mode — clear it. get_active_provider() is "" until the
        # next successful call re-derives it; nothing may claim the
        # old leg is still live under the new mode.
        ACTIVE_PROVIDER = ""
    if result.get("error"):
        add_log(f"provider mode: invalid value — {result['error']}",
                level="error")
    elif result.get("saved"):
        if result.get("env_override"):
            add_log(f"provider mode set to '{result['mode']}' (saved, "
                    f"but a PROVIDER_MODE env var overrides the saved "
                    f"value at the next restart)")
        else:
            add_log(f"provider mode set to '{result['mode']}' (saved)")
    else:
        add_log(f"provider mode set to '{result['mode']}' for this "
                f"session — NOT saved; the next restart restores the "
                f"previous value", level="warn")
    return result["mode"]


# Pass 36 #07: outcome of the last set_provider_mode() call —
# the panel's saved-vs-NOT-saved + env-override + error surface.
# Empty until the toggle is used once; provider_mode_status()
# merges it with the live mode.
PROVIDER_MODE_STATUS: Dict[str, Any] = {}


def provider_mode_status() -> Dict[str, Any]:
    """Live mode + persistence outcome for the Settings panel.
    Never raises."""
    try:
        status = _config_silent.provider_mode_status()
    except Exception:
        status = {"mode": provider_mode(), "stored": None,
                  "source": "unknown", "env_override": False,
                  "error": None}
    status["mode"] = provider_mode()
    if PROVIDER_MODE_STATUS:
        status["saved"] = PROVIDER_MODE_STATUS.get("saved")
        status["error"] = PROVIDER_MODE_STATUS.get("error") or status.get("error")
    return status


def effective_chain_names(mode: Optional[str] = None) -> List[str]:
    """PROVIDER_CHAIN as PROVIDER_MODE gates it.

    auto: the configured chain, untouched. local_only: exactly
    ["ollama_local"] — every API-keyed leg is bypassed (the keys
    themselves are never modified, just never walked), so a
    local-only turn cannot reach the cloud no matter what
    PROVIDER_CHAIN contains. Any value other than the literal
    "local_only" behaves as auto; config already normalizes the
    stored value, this is the second line of defence. Every chain
    consumer — the walk, resolve_role_model, brain's primary
    readout, the frame status — goes through here so they can
    never disagree about which legs may serve.

    Pass 36 #06: mode= pins the answer to one turn's policy
    instead of the live value; None (default) reads live, as before.
    """
    if (mode if mode is not None else provider_mode()) == "local_only":
        return ["ollama_local"]
    return list(PROVIDER_CHAIN)


ACTIVE_PROVIDER = (effective_chain_names() or ["ollama_cloud"])[0]


# Session stats for the OPS dashboard: failover count, per-provider call
# counts, and the most recent failover (failed_name, epoch).
PROVIDER_STATS: Dict[str, Any] = {
    "failovers": 0, "calls": {}, "last_failover": None,
}

# Sanitized session diagnostics for Settings. Turn failures stay on the
# execution context, so a background task cannot overwrite a foreground error.
_HEALTH_LOCK = threading.RLock()
_PROVIDER_HEALTH: Dict[str, Dict[str, Any]] = {}
_RECENT_FOREGROUND_PROVIDER = {}

# Pass 36 #18: a "live" health entry is only fresh this long. A
# success from an hour ago does not make a dead daemon live now.
_PROVIDER_HEALTH_FRESH_S = 300


def provider_health() -> Dict[str, Dict[str, Any]]:
    with _HEALTH_LOCK:
        return copy.deepcopy(_PROVIDER_HEALTH)


def is_provider_live(name: str) -> bool:
    """True only on a RECENT success (pass 36 #18): the health entry
    says 'ok' and its check is inside _PROVIDER_HEALTH_FRESH_S.
    Configured-but-untested is 'configured', not live — a dead
    local daemon used to show live from is_available() alone."""
    entry = _PROVIDER_HEALTH.get(name) or {}
    if entry.get("status") != "ok":
        return False
    try:
        checked_at = float(entry.get("checked_at") or 0)
    except (TypeError, ValueError):
        return False
    return (time.time() - checked_at) <= _PROVIDER_HEALTH_FRESH_S


def local_verification_cached() -> Optional[Dict[str, Any]]:
    """The cached local-leg verification, if any — no network
    (pass 36 #18). The frame reads this on the render loop instead
    of probing the daemon; a cached non-ok result (daemon
    unreachable, model missing, non-loopback) is what turns the
    local row 'unreachable'."""
    try:
        host = _ollama_host_root()
        strict = not bool(OLLAMA_ALLOW_REMOTE)
        entry = _LOCAL_VERIFY_CACHE.get(f"{host}|{OLLAMA_MODEL}|{strict}")
        return dict(entry) if entry else None
    except Exception:
        return None


def _record_provider_failure(provider, *, kind=None, code=None, retry_at=0, latency_ms=None):
    error = str(getattr(provider, 'last_error', '') or '')
    match = re.search(r'\bHTTP (\d{3})\b', error)
    code = code or (int(match.group(1)) if match else None)
    if kind is None:
        if code in (401, 403): kind = 'auth'
        elif code == 429: kind = 'rate_limit'
        elif code == 402: kind = 'billing'
        elif code in (404, 410): kind = 'model'
        elif code == 400: kind = 'request'
        elif code and code >= 500: kind = 'server'
        elif any(word in error.lower() for word in ('network', 'timeout', 'connection', 'urlerror')):
            kind = 'network'
        else: kind = 'response'
    descriptions = {
        'auth': 'API key or permission rejected',
        'rate_limit': 'rate limit or provider quota reached',
        'billing': 'provider billing or credits required',
        'model': 'model or endpoint unavailable',
        'request': 'provider rejected the request',
        'server': 'provider service error',
        'network': 'network connection failed or timed out',
        'response': 'no usable model response',
        'unconfigured': 'API key, model, or endpoint not configured',
        'cooldown': 'temporarily cooling down after a failed request',
        'budget': 'project model budget blocked this request',
        # Pass 36 #05: the local leg failed verification (non-loopback
        # host, or the daemon answered but lacks the model) — a typed
        # error, never a silent fall-through to cloud.
        'local_unverified': 'local Ollama verification failed',
        # Pass 36 #12: a leg whose model cannot accept images is a
        # routing dead-end, not a generic failure — the walk must
        # not retry the image on it (or on the default text model).
        'no_vision_capability': 'model cannot accept images',
        # Pass 36 #06: the mode flipped mid-walk; the walk aborted.
        'mode_change': 'provider mode changed during this turn',
        # Pass 36 #15: the request-wide deadline ran out before an
        # attempt could start — the walk stopped, it did not hang.
        'deadline': 'request-wide deadline reached',
    }
    summary = descriptions[kind] + (f' (HTTP {code})' if code else '')
    if retry_at > time.time():
        summary += f'; retry in about {math.ceil(retry_at - time.time())} seconds'
    entry = {'provider': provider.name, 'model': getattr(provider, 'model', ''),
             'kind': kind, 'code': code, 'summary': summary, 'status': 'error',
             'checked_at': time.time()}
    if latency_ms is not None:
        entry['latency_ms'] = latency_ms
    runtime.current().provider_failures.append(entry)
    with _HEALTH_LOCK:
        _PROVIDER_HEALTH[provider.name] = entry


def provider_failure_message() -> str:
    ctx = runtime.current()
    if ctx.budget_error:
        return ctx.budget_error
    # Pass 36 #06: the mode flipped mid-turn and the walk aborted
    # on purpose. Say so — the turn did not "fail to reach a
    # model", it stopped because the policy changed; completed
    # tool calls kept their receipts (brain persists them).
    changed = [f for f in ctx.provider_failures
               if f.get('kind') == 'mode_change']
    if changed:
        return ("Provider mode changed during this turn, so I stopped "
                "before calling another model. Anything I had already "
                "done kept its receipt — ask me to continue and I will "
                "pick it up under the new mode.")
    if provider_mode() == "local_only":
        # Strict local-only: the walk had exactly one leg and it did
        # not answer. Say so plainly — the generic messages below
        # ("add an API key", "no provider responded") would mislead:
        # no cloud provider was tried, by design, and none will be
        # as a silent fallback. A typed verification failure names
        # its own reason (#05) instead of the generic wording.
        unverified = [f for f in ctx.provider_failures
                      if f.get('kind') == 'local_unverified']
        if unverified:
            return ("Local Ollama verification failed: "
                    f"{unverified[0].get('summary', '')} "
                    "No cloud provider was tried.")
        return ("Local-only mode is on and the local model is unavailable. "
                "No cloud provider was tried. Check that Ollama is running "
                "on this machine, or set PROVIDER_MODE back to auto.")
    failures = ctx.provider_failures
    configured = [f for f in failures if f['kind'] != 'unconfigured']
    if failures and not configured:
        return ('No model provider is configured. Add an API key in '
                'Settings → Connections, then restart ARIA.')
    if not configured:
        return 'No model provider responded. Check Settings → Providers for connection details.'
    detail = '; '.join(f"{f['provider']}: {f['summary']}" for f in configured[:3])
    return f"I couldn't get a model response. {detail}. Check Settings → Providers for details."


def connection_report() -> str:
    """Copyable diagnostics containing no credentials or response bodies."""
    health = provider_health()
    mode = 'ON' if usage.project_budget_enabled() else 'OFF (usage tracking only)'
    lines = ['ARIA model connections', 'Project spending limits: ' + mode]
    lines.append(f'Provider mode: {provider_mode()}')
    chain = _build_chain()
    for provider in chain:
        state = health.get(provider.name, {}).get('summary')
        if not state:
            state = 'ready; not tested' if provider.is_available() else 'not configured or quarantined'
        elapsed = health.get(provider.name, {}).get('latency_ms')
        if elapsed is not None:
            state += f'; last attempt {elapsed / 1000:.2f}s'
        lines.append(f'{provider.name} / {provider.model}: {state}')
    if not chain:
        lines.append('No providers configured.')
    return '\n'.join(lines)


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
    except Exception as _e_silent:
        _config_silent.log_silent("_record_call_stats", _e_silent)


# --------------------------------------------------------------------------
# Translation: Gemini-native <-> OpenAI-compatible
# --------------------------------------------------------------------------

def _synth_tool_id(name: str, args: Dict[str, Any]) -> str:
    """Deterministic synthetic tool-call id from name+args (md5, not hash():
    stable within the process and collision-safe enough for a turn)."""
    try:
        digest = hashlib.md5(
            (name + json.dumps(args, sort_keys=True)).encode("utf-8"),
            usedforsecurity=False
        ).hexdigest()[:12]
    except Exception:
        digest = "000000000000"
    return f"call_{digest}"


def gemini_contents_to_oai_messages(
    system_instruction: str,
    contents: List[Dict[str, Any]],
    include_extra_content: bool = True,
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
                tool_call = {
                    "id": tid,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args),
                    },
                }
                if include_extra_content and isinstance(fc.get('_oai_extra_content'), dict):
                    tool_call['extra_content'] = copy.deepcopy(fc['_oai_extra_content'])
                tool_calls.append(tool_call)
            msg: Dict[str, Any] = {
                "role": "assistant",
                # Never None: several OpenAI-compatible servers (Ollama
                # included) 400 on null assistant content. Empty string is
                # accepted everywhere, with or without tool_calls.
                "content": "\n".join(text_bits) if text_bits else "",
            }
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if include_extra_content:
                for part in parts:
                    if isinstance(part.get('_oai_message_extra_content'), dict):
                        msg['extra_content'] = copy.deepcopy(part['_oai_message_extra_content'])
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


def _payload_fingerprint(payload: Dict[str, Any]) -> str:
    """Compact shape summary of a chat-completions payload, for 400 diagnosis.

    Logs structure only — roles, content kinds, part types, counts — never
    message text and never the API key (the key travels in headers, not the
    body). When a provider 400s on "invalid message type", this shows exactly
    which message had the offending shape.
    """
    try:
        descs = []
        for m in payload.get("messages", []) or []:
            role = m.get("role", "?")
            c = m.get("content")
            if c is None:
                ck = "null"
            elif isinstance(c, str):
                ck = "str"
            elif isinstance(c, list):
                kinds = sorted({p.get("type", "?") for p in c if isinstance(p, dict)})
                ck = "list[" + ",".join(kinds) + "]"
            else:
                ck = type(c).__name__
            extra = ""
            if m.get("tool_calls"):
                extra += f"+tool_calls({len(m['tool_calls'])})"
            if m.get("tool_call_id"):
                extra += "+tool_call_id"
            descs.append(f"{role}:{ck}{extra}")
        if len(descs) > 20:
            descs = descs[:8] + ["..."] + descs[-4:]
        tools = payload.get("tools")
        return (f"model={payload.get('model')} msgs={len(payload.get('messages', []) or [])} "
                f"[{', '.join(descs)}] tools={len(tools) if tools else 0}")
    except Exception:
        return "fingerprint failed"


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
        elif isinstance(msg.get('refusal'), str) and msg['refusal'].strip():
            parts.append({'text': msg['refusal']})
        if isinstance(msg.get('extra_content'), dict):
            parts.append({'_oai_message_extra_content': copy.deepcopy(msg['extra_content'])})
        for tc in msg.get("tool_calls") or []:
            fn = (tc or {}).get("function", {}) or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else {}
            except Exception:
                # Pass 25: a parse failure used to be silently
                # replaced with {} — and {} then reached handlers
                # with their defaults (media_key toggling
                # playback). Preserve the failure as a marker the
                # dispatcher rejects before anything executes.
                args = {"_args_parse_error": True}
            if not isinstance(args, dict):
                args = {"_args_parse_error": True}
            call = {
                "name": fn.get("name", ""),
                "args": args,
                "_oai_id": tc.get("id"),
            }
            # Gemini requires this opaque signature on the next tool-result
            # request. Preserve it verbatim; never invent or log a signature.
            if isinstance(tc.get('extra_content'), dict):
                call['_oai_extra_content'] = copy.deepcopy(tc['extra_content'])
            parts.append({'functionCall': call})
    except Exception as e:
        add_log(f"oai_response_to_parts parse error: {e}")
    return parts


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

# A5: per-attempt socket timeout for every provider leg (see
# OpenAICompatProvider.call for the rationale). Was a hard-coded 90s.
PROVIDER_CALL_TIMEOUT_S = 30

# Pass 36 #15: default request-wide deadline for provider_call
# (monotonic seconds). The walk stops starting new attempts past
# it; a caller with its own turn budget (brain) passes a tighter
# deadline instead. Long enough to keep the local CPU leg's 180s
# (vision 300s) profile usable across a failover or two.
REQUEST_DEADLINE_S = 900


def _provider_timeout():
    """Interactive stalls fail over quickly; delegated/mission calls keep 30s."""
    if runtime.current().kind != 'foreground':
        return PROVIDER_CALL_TIMEOUT_S
    try:
        return max(3, min(30, float(_config_silent.get_setting('foreground_provider_timeout_s', 8))))
    except (TypeError, ValueError):
        return 8


def _merge_extra_content(target, incoming):
    """Merge streamed extension objects without changing opaque signatures."""
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_extra_content(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _has_model_output(parts):
    """Metadata and empty candidates cannot satisfy a model request."""
    return any(isinstance(part, dict) and (
        isinstance(part.get('text'), str) and bool(part['text'].strip()) and not part.get('thought')
        or isinstance(part.get('functionCall'), dict)
        and isinstance(part['functionCall'].get('name'), str)
        and bool(part['functionCall']['name'].strip())) for part in parts or [])


def _usable_response(data):
    try:
        return _has_model_output(data['candidates'][0]['content']['parts'])
    except (KeyError, IndexError, TypeError):
        return False


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
        return bool(self.base_url and self.model and self.api_key
                    and self.api_key != 'INSERT') and not key_is_quarantined(self.api_key)

    def _call_timeout(self) -> float:
        """Per-attempt timeout (socket + first-output deadline) for
        this leg. Cloud legs fail over fast; a leg that needs longer
        (the local CPU leg) overrides this — see LocalOllamaProvider."""
        return _provider_timeout()

    def _payload_options(self) -> Dict[str, Any]:
        """Provider-specific payload options (pass 36 #17). Cloud
        legs send none — Ollama's `options` object is meaningless
        to them; the local leg overrides this with num_ctx."""
        return {}

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

    def _parse_sse_stream(self, resp, on_text_chunk, first_output_deadline=None) -> List[Dict[str, Any]]:
        """Accumulate OpenAI SSE deltas -> parts. Feeds on_text_chunk per
        chunk; a final on_text_chunk(None) signals stream completion.
        Captures the `usage` block when the provider sends one (usually in
        the final chunk) into self._stream_usage."""
        text_buf = ""
        tc_accum: Dict[int, Dict[str, Any]] = {}
        message_extra = {}
        self._stream_usage = None
        try:
            for raw_line in resp:
                runtime.check_cancelled()
                if (first_output_deadline is not None and not text_buf
                        and not any(acc.get('name') for acc in tc_accum.values())
                        and time.monotonic() >= first_output_deadline):
                    raise TimeoutError('No model output before the interactive reply deadline')
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == '[DONE]':
                    break
                if not data_str:
                    continue
                try:
                    chunk = json.loads(data_str)
                except Exception:
                    continue
                if isinstance(chunk.get("usage"), dict):
                    self._stream_usage = chunk["usage"]
                delta = ((chunk.get("choices") or [{}])[0]).get("delta", {}) or {}
                if isinstance(delta.get('extra_content'), dict):
                    _merge_extra_content(message_extra, delta['extra_content'])
                c = delta.get("content")
                if isinstance(c, str) and c:
                    text_buf += c
                    if on_text_chunk:
                        try:
                            on_text_chunk(c)
                        except Exception as _e_silent:
                            _config_silent.log_silent("_parse_sse_stream", _e_silent)
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
                    if isinstance(tc.get('extra_content'), dict):
                        _merge_extra_content(acc.setdefault('extra_content', {}), tc['extra_content'])
        finally:
            if on_text_chunk:
                try:
                    on_text_chunk(None)  # stream-complete signal: brain treats None as done
                except Exception as _e_silent:
                    _config_silent.log_silent("_parse_sse_stream", _e_silent)

        parts: List[Dict[str, Any]] = []
        if text_buf:
            parts.append({"text": text_buf})
        if message_extra:
            parts.append({'_oai_message_extra_content': message_extra})
        for idx in sorted(tc_accum):
            acc = tc_accum[idx]
            try:
                args = json.loads(acc["args"]) if acc["args"] else {}
            except Exception:
                # Same marker as oai_response_to_parts above: a
                # truncated/streamed args fragment must never
                # become an executable {}.
                args = {"_args_parse_error": True}
            if not isinstance(args, dict):
                args = {"_args_parse_error": True}
            call = {
                "name": acc["name"] or "",
                "args": args,
                "_oai_id": acc["id"],
            }
            if acc.get('extra_content'):
                call['_oai_extra_content'] = acc['extra_content']
            parts.append({'functionCall': call})
        return parts

    def call(self, system_instruction, contents, tool_decls=None, on_text_chunk=None,
             timeout_cap: Optional[float] = None):
        # One attempt, then fail over without backoff sleeps. Interactive
        # connections default to 8s; delegated/mission calls retain 30s.
        # A stream with only heartbeats also gets a first-output deadline.
        # Once content or a tool call arrives, normal streaming can continue.
        # Pass 36 #15: timeout_cap is the request-wide deadline's
        # remaining time (monotonic seconds) handed down by the walk —
        # the connect allowance (socket timeout) and the first-output
        # allowance are both capped by it, so a long local profile
        # still cannot outrun the turn's budget. It bounds attempt
        # starts, not a stream already producing output.
        self.last_error = None
        if not self.is_available():
            return None
        # Google-specific extensions must not leak into a fallback provider's
        # strict OpenAI payload. Gemini pooled keys share the same wire format.
        messages = gemini_contents_to_oai_messages(
            system_instruction, contents,
            include_extra_content=self.name == 'gemini' or self.name.startswith('gemini_'))
        tools = gemini_decls_to_oai_tools(tool_decls) if tool_decls else None
        runtime.check_cancelled()
        payload: Dict[str, Any] = {"model": self.model, "messages": messages,
                                   "max_tokens": usage.max_output_tokens()}
        if tools:
            payload["tools"] = tools
        # Pass 36 #17: the local leg sets num_ctx here (Ollama's
        # /v1 accepts an `options` object); cloud legs send none.
        _opts = self._payload_options()
        if _opts:
            payload["options"] = _opts
        url = self.base_url + "/chat/completions"
        body = json.dumps(payload).encode("utf-8")

        ticket = usage.reserve(self.name, self.model, payload)
        t0, usage_data, request_ok = time.time(), None, False
        timeout = self._call_timeout()
        if timeout_cap is not None:
            # Never pass a zero/negative timeout to urlopen (it
            # means "block forever" or errors immediately) — the
            # walk guarantees >= 1s of remaining time.
            timeout = max(1.0, min(timeout, float(timeout_cap)))
        first_output_deadline = (time.monotonic() + timeout
                                 if runtime.current().kind == 'foreground' else None)
        try:
            runtime.check_cancelled()
            if on_text_chunk:
                payload["stream"] = True
                sbody = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=sbody, headers=self._headers())
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    parts = self._parse_sse_stream(resp, on_text_chunk, first_output_deadline)
                usage_data, request_ok = getattr(self, "_stream_usage", None), True
                if not _has_model_output(parts):
                    self.last_error = 'No usable model response'
                    return None
                _record_call_stats(self.name, int((time.time() - t0) * 1000),
                                   getattr(self, "_stream_usage", None))
                return {"candidates": [{"content": {"parts": parts}}]}
            req = urllib.request.Request(url, data=body, headers=self._headers())
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            _record_call_stats(self.name, int((time.time() - t0) * 1000),
                               data.get("usage"))
            usage_data, request_ok = data.get("usage"), True
            parts = oai_response_to_parts(data)
            if not _has_model_output(parts):
                self.last_error = 'No usable model response'
                return None
            return {"candidates": [{"content": {"parts": parts}}]}
        except runtime.Cancelled:
            raise
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
            if e.code in (400, 401, 403):
                # 400 is a malformed request (OUR payload), not a bad key:
                # never quarantine — the key is fine and the main loop must
                # not pay for a bad vision payload or model tag.
                if e.code == 400:
                    add_log(f"{self.name}: HTTP 400 (bad request, key NOT quarantined): "
                            f"{detail[:120]}")
                    # 400 means OUR payload was malformed — log its shape so the
                    # next occurrence self-diagnoses instead of needing a manual
                    # OPS-log capture.
                    add_log(f"{self.name}: 400 payload shape: "
                            f"{_payload_fingerprint(payload)}")
                    return None
                # Only authentication/permission failures quarantine a key.
                quarantine_key(self.api_key, KEY_QUARANTINE_DURATION_S, e.code)
                add_log(f"{self.name}: HTTP {e.code} ({key_mask(self.api_key)}): "
                        f"{detail[:120]}")
                return None
            if e.code in (500, 502, 503, 504):
                # Transient: move on now, retried next turn (no quarantine).
                add_log(f"{self.name}: HTTP {e.code}, moving on")
                return None
            if e.code in (402, 404, 410):
                # Billing/model errors do not invalidate credentials. The
                # role-model fallback in
                # _attempt_provider_call parks the dead tag and retries the
                # default model; on a default model the chain just moves on.
                add_log(f"{self.name}: HTTP {e.code} (key NOT quarantined): "
                        f"{detail[:120]}")
                return None
            add_log(f"{self.name}: unhandled HTTP {e.code}")
            return None
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                socket.timeout) as e:
            self.last_error = f"network drop: {e}"[:200]
            # A network failure says nothing about key validity. Provider/model
            # cooldowns already handle failover and permit recovery next turn.
            add_log(f"{self.name}: network drop, moving on")
            return None
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            add_log(f"{self.name}: error: {e}")
            return None
        finally:
            usage.finish(ticket, usage=usage_data, latency_ms=int((time.time() - t0) * 1000),
                         status='ok' if request_ok else 'error')


class LocalOllamaProvider(OpenAICompatProvider):
    """The local Ollama server (OLLAMA_HOST, default
    http://localhost:11434) speaking its OpenAI-compatible /v1 —
    the chain's last-resort leg, there when every cloud leg is down.

    Two deliberate differences from a cloud leg:

    - No API key exists to configure or quarantine, so availability
      is just "a model tag is configured". The placeholder key is
      sent as a Bearer header Ollama ignores. A dead endpoint fails
      fast inside call() (connection refused) and the walk moves on,
      exactly like any other provider's network drop; provider/model
      failure cooldowns still apply by name.
    - The timeout floor is OLLAMA_LOCAL_TIMEOUT_S (default 180s),
      not the cloud legs' interactive deadline (8s foreground): on a
      CPU-only machine, prefill of ARIA's system prompt + tool
      schemas alone takes far longer than 8s, so under the cloud
      deadline this leg could never produce a first token.
      Calls serving the local vision model (PROVIDER_MODE=
      local_only) get the higher OLLAMA_LOCAL_VISION_TIMEOUT_S
      floor instead — see _call_timeout.
    """

    def __init__(self, name: str, base_url: str, model: str, verify: bool = False):
        super().__init__(name, base_url, "ollama-local", model)
        # Pass 36 #05: the daemon + model capability check, run at
        # leg construction when asked (the provider_call walk asks;
        # the frame render loop does not — see _build_chain).
        # Cached (300s) inside verify_local_ollama, so repeated
        # construction stays cheap. None until verified.
        self.local_verification: Optional[Dict[str, Any]] = None
        if verify:
            self.verify()

    def verify(self, force: bool = False) -> Dict[str, Any]:
        """(Re-)verify this leg's endpoint and model; cached."""
        self.local_verification = verify_local_ollama(
            _ollama_host_root(), self.model, force=force)
        return self.local_verification

    def is_available(self) -> bool:
        # Pass 36 #05: strict mode gates on loopback, not just on
        # "a tag is configured" — a non-loopback host is not a
        # local leg. Daemon liveness does NOT gate availability
        # (a dead daemon fails fast inside call(), as before, and
        # the walk records it as a network failure).
        if not (self.base_url and self.model):
            return False
        strict = not bool(OLLAMA_ALLOW_REMOTE)
        if strict and not is_loopback_host(self.base_url):
            return False
        return True

    def call(self, system_instruction, contents, tool_decls=None, on_text_chunk=None,
             timeout_cap: Optional[float] = None):
        # Pass 36 #05: local traffic goes through the dedicated
        # opener (no proxy, no redirects) for the duration of this
        # call only — the global opener is restored afterwards.
        # (Tests monkeypatch urllib.request.urlopen, which this
        # swap does not disturb.)
        previous = getattr(urllib.request, "_opener", None)
        try:
            urllib.request._opener = _local_opener()
            return super().call(system_instruction, contents,
                                tool_decls=tool_decls,
                                on_text_chunk=on_text_chunk,
                                timeout_cap=timeout_cap)
        finally:
            urllib.request._opener = previous

    def _call_timeout(self) -> float:
        try:
            local = float(OLLAMA_LOCAL_TIMEOUT_S)
        except (TypeError, ValueError):
            local = 180.0
        floor = local
        # Vision on the local VL model is the slowest call ARIA
        # makes (image prefill on CPU). When THIS leg is serving
        # the vision model — _attempt_provider_call's override swap
        # sets .model before call() runs — the floor rises to
        # OLLAMA_LOCAL_VISION_TIMEOUT_S (default 300s).
        if self.model and self.model == OLLAMA_LOCAL_VISION_MODEL:
            try:
                floor = max(floor, float(OLLAMA_LOCAL_VISION_TIMEOUT_S))
            except (TypeError, ValueError):
                floor = max(floor, 300.0)
        return max(float(_provider_timeout()), floor)

    def _payload_options(self) -> Dict[str, Any]:
        """Pass 36 #17: size the local leg's context explicitly.
        Ollama's /v1 default context is far below ARIA's prompt
        (system instruction alone is ~12,334 chars); without
        num_ctx the daemon truncates the prompt silently."""
        try:
            num_ctx = int(OLLAMA_NUM_CTX)
        except (TypeError, ValueError):
            num_ctx = 8192
        return {"num_ctx": num_ctx}


def _ollama_local_base_url() -> str:
    """OLLAMA_HOST + /v1 (the OpenAI-compatible surface). A host
    already ending in /v1 is used as-is. Pass 36 #16: a non-string
    host (raw JSON value) used to crash this .rstrip() and take
    the whole chain down with it — coerce defensively, the config
    validators above are the first line, this is the second."""
    raw = OLLAMA_HOST  # module global: config-validated at import,
    # rebindable by tests; a non-string is coerced, never crashed on.
    host = raw if isinstance(raw, str) and raw.strip() else "http://localhost:11434"
    host = host.strip().rstrip("/")
    return host if host.endswith("/v1") else host + "/v1"


# --- Pass 36 #05: locality enforcement for ollama_local -----------
# "ollama_local" used to be a registry name with no enforcement:
# any OLLAMA_HOST was taken verbatim, over the same opener as the
# cloud legs (proxies + redirects included), and Ollama can serve
# cloud-backed tags through its OpenAI-compatible API. Strict mode
# (the default) requires a verified loopback endpoint; a LAN or
# remote Ollama needs the explicit non-strict route
# (OLLAMA_ALLOW_REMOTE=1) and is then never treated as verified-local.

def is_loopback_host(host: str) -> bool:
    """True for localhost / 127.0.0.0/8 / ::1 (any spelling). Never raises."""
    try:
        from urllib.parse import urlparse
        name = (urlparse(host).hostname or "").strip().lower()
    except Exception:
        return False
    if name in ("localhost", "::1"):
        return True
    if name.startswith("127."):
        try:
            parts = name.split(".")
            return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255
                                           for p in parts)
        except Exception:
            return False
    return False


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Local traffic never follows a redirect: a redirect off the
    loopback daemon is a misconfiguration, not a route."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code,
            f"redirect to {newurl} refused for local Ollama",
            headers, fp)


def _local_opener():
    """Dedicated opener for local Ollama traffic: no proxy (a proxy
    would carry 'local' inference off-machine), no redirects."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler())


def _ollama_host_root() -> str:
    """OLLAMA_HOST without the /v1 suffix — the /api/tags surface."""
    base = _ollama_local_base_url()
    return base[:-3] if base.endswith("/v1") else base


_LOCAL_VERIFY_CACHE: Dict[str, Dict[str, Any]] = {}
_LOCAL_VERIFY_TTL_S = 300


def verify_local_ollama(host=None, model=None, *, force=False) -> Dict[str, Any]:
    """Verify the local Ollama endpoint: loopback (strict mode),
    daemon reachable, and the configured chat model present on it
    (daemon + model capability check). Returns {"ok", "code",
    "reason", "strict", "host", "model", "models"}; "code" is one
    of ok / non_loopback / daemon_unreachable / model_missing /
    bad_response. Results are cached (300s) per (host, model) so
    leg construction stays cheap; force=True re-probes. Never
    raises — an unverifiable endpoint is a result, not an exception."""
    host = host or _ollama_host_root()
    model = model or OLLAMA_MODEL
    strict = not bool(OLLAMA_ALLOW_REMOTE)
    cache_key = f"{host}|{model}|{strict}"
    now = time.monotonic()
    cached = _LOCAL_VERIFY_CACHE.get(cache_key)
    if cached and not force and now - cached.get("_at", 0) < _LOCAL_VERIFY_TTL_S:
        return {k: v for k, v in cached.items() if not k.startswith("_")}
    result: Dict[str, Any] = {"ok": False, "code": "daemon_unreachable",
                              "reason": "", "strict": strict,
                              "host": host, "model": model, "models": []}
    if strict and not is_loopback_host(host):
        result.update(code="non_loopback",
                      reason=(f"OLLAMA_HOST {host} is not a loopback address; "
                              f"strict local mode rejects it. Set "
                              f"OLLAMA_ALLOW_REMOTE=1 for an explicit "
                              f"non-strict route, or point OLLAMA_HOST at "
                              f"this machine."))
    else:
        try:
            req = urllib.request.Request(
                host.rstrip("/") + "/api/tags",
                headers={"User-Agent": "ARIA-Agent/1.0"})
            with _local_opener().open(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name") for m in (data.get("models") or [])
                      if isinstance(m, dict) and m.get("name")]
            result["models"] = models
            if model and model not in models:
                result.update(code="model_missing",
                              reason=(f"model '{model}' is not on the local "
                                      f"Ollama daemon (has: "
                                      f"{', '.join(models) or 'none'}). "
                                      f"Pull it with `ollama pull {model}`."))
            else:
                result.update(ok=True, code="ok",
                              reason="loopback daemon reachable, model present")
        except urllib.error.HTTPError as e:
            result.update(code="daemon_unreachable",
                          reason=f"local Ollama daemon HTTP {e.code} on /api/tags")
        except Exception as e:
            result.update(code="daemon_unreachable",
                          reason=f"local Ollama daemon unreachable ({e})".strip()[:160])
    _LOCAL_VERIFY_CACHE[cache_key] = dict(result, _at=now)
    return result


def ollama_local_verified() -> bool:
    """True when the configured ollama_local leg is verified-local:
    a loopback host (strict) and no definitive verification failure
    (non-loopback, or a daemon that answered but lacks the model).
    A daemon that is merely unreachable right now does NOT revoke
    verification — that is a transient failure, recorded as a
    network error when the leg is attempted. Used by usage.reserve
    (#13) to exempt verified-local inference from priced-model rules."""
    try:
        host = _ollama_host_root()
        strict = not bool(OLLAMA_ALLOW_REMOTE)
        if strict and not is_loopback_host(host):
            return False
        cached = _LOCAL_VERIFY_CACHE.get(f"{host}|{OLLAMA_MODEL}|{strict}")
        if cached and cached.get("code") in ("non_loopback", "model_missing"):
            return False
        return True
    except Exception:
        return False


def _gemini_providers() -> List[Provider]:
    """One provider per pooled Gemini key (Alek, 2026-10-27).
    The first keeps the name 'gemini'; the rest are 'gemini_2',
    'gemini_3', ... — distinct names give each key its own
    failure cooldowns on both axes, and the OPS log shows WHICH
    key is serving or failing. The chain walk itself is the
    rotation: a key that 429s, 503s or quarantines hands the
    turn to the next key. With a single key this is exactly the
    old single provider."""
    keys = [k for k in (GEMINI_API_KEYS or []) if k]
    if not keys and GEMINI_API_KEY:
        keys = [GEMINI_API_KEY]
    out: List[Provider] = []
    for i, k in enumerate(keys):
        name = "gemini" if i == 0 else f"gemini_{i + 1}"
        out.append(OpenAICompatProvider(
            name, "https://generativelanguage.googleapis.com/v1beta/openai",
            k, GEMINI_MODEL))
    return out


def _provider_registry(verify_local: bool = False) -> Dict[str, Provider]:
    """Every named OpenAI-compatible leg, including opt-in ones
    not in the default PROVIDER_CHAIN. _build_chain picks from
    this by configured order; the tier opt-in path builds a named
    leg from it on demand (pass 25).

    Pass 36 #16: each leg is constructed independently — one
    malformed local setting used to raise inside _build_chain
    and kill Gemini with it. A leg that fails to construct is
    logged and skipped; the rest of the chain still serves.
    verify_local=True runs the #05 daemon + model check at leg
    construction (the provider_call walk asks for it; the frame
    render loop must not — see _build_chain)."""
    registry: Dict[str, Provider] = {}

    def _add(name, build):
        try:
            registry[name] = build()
        except Exception as e:
            add_log(f"provider registry: leg '{name}' failed to construct ({e}); skipped")

    _add("groq", lambda: OpenAICompatProvider(
        "groq", "https://api.groq.com/openai/v1", GROQ_API_KEY, GROQ_MODEL))
    _add("openrouter", lambda: OpenAICompatProvider(
        "openrouter", "https://openrouter.ai/api/v1",
        OPENROUTER_API_KEY, OPENROUTER_MODEL,
        extra_headers={"HTTP-Referer": "https://github.com/ABerger94/ARIA-Agent",
                       "X-Title": "ARIA-Agent"}))
    _add("mistral", lambda: OpenAICompatProvider(
        "mistral", "https://api.mistral.ai/v1", MISTRAL_API_KEY, MISTRAL_MODEL))
    _add("ollama_cloud", lambda: OpenAICompatProvider(
        "ollama_cloud", "https://ollama.com/v1",
        OLLAMA_CLOUD_API_KEY, OLLAMA_CLOUD_MODEL))
    # FreeLLMAPI (opt-in, Alek 2026-11-04): a local router fronting
    # many free tiers behind one OpenAI-compatible endpoint. It is
    # NOT in the default PROVIDER_CHAIN — naming "freellmapi" in
    # PROVIDER_CHAIN (or as a tier's light_provider) is the only
    # way it serves. With no FREELLMAPI_API_KEY configured the leg
    # reports unavailable and the walk moves on, exactly like any
    # other unconfigured provider.
    _add("freellmapi", lambda: OpenAICompatProvider(
        "freellmapi", FREELLMAPI_BASE_URL,
        FREELLMAPI_API_KEY, FREELLMAPI_MODEL))
    # Local Ollama (Alek, 2026-11-09): the machine's own Ollama
    # server running OLLAMA_MODEL (default qwen2.5:7b). It IS in
    # the default PROVIDER_CHAIN, last — the fallback when every
    # cloud leg is down. It needs no key; if Ollama is not
    # running the connection is refused instantly and the walk
    # ends, the same end state as before this leg existed.
    _add("ollama_local", lambda: LocalOllamaProvider(
        "ollama_local", _ollama_local_base_url(), OLLAMA_MODEL,
        verify=verify_local))
    return registry


def _providers_by_name(name: Optional[str]) -> List[Provider]:
    """Leg(s) for a provider name the configured chain may not
    contain — the tier opt-in path (freellmapi, or any registry
    leg dropped from PROVIDER_CHAIN). Empty for unknown names:
    the caller logs and keeps the configured order."""
    if not name:
        return []
    if name == "gemini":
        return _gemini_providers()
    provider = _provider_registry().get(name)
    return [provider] if provider is not None else []


def _build_chain(verify_local: bool = False) -> List[Provider]:
    """Providers in configured order. Phase 2/3 entries plug in here.

    The names come from effective_chain_names() — PROVIDER_CHAIN as
    PROVIDER_MODE gates it — so in local_only mode every caller of
    this function (the chain walk, provider_ping, the frame/OPS
    readouts) sees exactly the one local leg.

    verify_local=True runs the #05 daemon + model verification at
    leg construction. The chain walk uses it; the frame/OPS
    readouts must NOT (a readiness probe has no place on the
    render loop — see #18)."""
    registry = _provider_registry(verify_local=verify_local)
    chain: List[Provider] = []
    for n in effective_chain_names():
        if n == "gemini":
            try:
                chain.extend(_gemini_providers())
            except Exception as e:
                # Pass 36 #16: one bad leg never takes the chain down.
                add_log(f"provider chain: gemini legs failed to construct ({e}); skipped")
        elif n in registry:
            chain.append(registry[n])
    return chain


# --- Role-based model routing (Ollama Cloud) ---
# Routes by ROLE, never by hard-coded model name at call sites: if a tag is
# retired or 403s, set OLLAMA_VISION_MODEL / OLLAMA_CODE_MODEL to "" (or a new
# tag) in aria_keys.json and the role silently falls back to the default
# OLLAMA_CLOUD_MODEL. The conversational loop always uses the default.

def resolve_role_model(role: str, policy=None) -> Optional[str]:
    """Map a task role to an Ollama Cloud model tag.

    Returns None for the default/unknown roles (provider's configured model)
    and when the role's override is empty/disabled — or when the tag is in
    failure cooldown (a broken role model falls back silently instead of
    being hammered every turn).

    PROVIDER_MODE=local_only changes the map entirely: a role must
    never resolve to a cloud tag (or a settings override naming
    one), because the override is applied to a leg and would send
    the turn off-machine. Vision resolves to the LOCAL vision
    model, served by the ollama_local leg (in this mode the
    override applies to that leg — see _attempt_provider_call);
    every other role returns None, so the local provider's
    configured model (OLLAMA_MODEL) handles the turn.

    Pass 36 #06: policy= pins the mode to one turn's captured
    request policy instead of a fresh live read — the role tag
    can no longer be resolved under the old mode and applied
    under the new one.
    """
    r = (role or "default").strip().lower()
    # PROVIDER_MODE / OLLAMA_LOCAL_VISION_MODEL are read through
    # globals() on purpose: tests/test_ultimate_e.py AST-extracts
    # this function with a fixed namespace that predates both
    # names, and globals().get() keeps that contract — an extracted
    # copy sees "auto" and the default vision tag, not a NameError.
    # The same extraction is why `policy` carries no Dict
    # annotation: the extracted copy compiles without the module's
    # `from __future__ import annotations`, so a Dict[...] default
    # would NameError (pass 36 #06 regression, caught by that test).
    # The name read IS the live value: set_provider_mode() rebinds
    # this module's PROVIDER_MODE global, so a Settings-toggle flip
    # is visible here on the next call.
    mode = (policy or {}).get("mode") if policy else None
    if mode is None:
        mode = globals().get("PROVIDER_MODE", "auto")
    if mode == "local_only":
        if r != "vision":
            return None
        tag = globals().get(
            "OLLAMA_LOCAL_VISION_MODEL", "qwen2.5vl:7b") or None
        if tag and _ROLE_MODEL_COOLDOWN.get(tag, 0) > time.time():
            add_log(f"role 'vision': local model '{tag}' in failure "
                    f"cooldown, using default")
            return None
        return tag
    override = usage.settings().get("role_models", {}).get(r)
    if override:
        return override
    tag: Optional[str] = None
    if r == "vision":
        tag = OLLAMA_VISION_MODEL or None
    elif r == "code":
        tag = OLLAMA_CODE_MODEL or None
    if tag and _ROLE_MODEL_COOLDOWN.get(tag, 0) > time.time():
        add_log(f"role '{r}': model '{tag}' in failure cooldown, using default")
        return None
    return tag


# Pass 36 #10: role bindings. A role override is a (provider, model)
# pair, not a bare tag — the tag alone used to resolve and log and
# then never apply, because the only leg that honored it
# (ollama_cloud) left the default chain. resolve_role_route binds
# the override to its eligible leg and says plainly when no leg can
# serve it, so the drop is a logged decision, never a silent one.
# The code role's serving leg is Alek's product call, still open:
# until he picks one, code resolves to "ignored" with that reason.
_KNOWN_ROLE_PROVIDERS = frozenset({
    "gemini", "groq", "openrouter", "mistral", "ollama_cloud",
    "freellmapi", "ollama_local"})

_CODE_NO_LEG_REASON = (
    "no eligible provider serves the code role in the default chain "
    "(ollama_cloud left the chain 2026-11-10); which leg should serve "
    "code is Alek's product call, still open — the default model serves")


def resolve_role_route(role: str, policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Bind a task role to a named eligible provider + model.

    Returns {"role", "provider", "model", "status", "reason"}:
    - "ok": provider + model bound and eligible under this policy.
    - "ignored": a tag exists but no eligible leg can serve it —
      callers MUST log the reason, never drop it silently.
    - "default": no override configured; the leg's default model serves.
    Settings overrides may be a bare tag (bound by the role's
    default provider) or {"provider": ..., "model": ...} (explicit
    binding, validated in Work Center)."""
    r = (role or "default").strip().lower()
    mode = (policy or {}).get("mode") if policy else None
    if mode is None:
        mode = globals().get("PROVIDER_MODE", "auto")

    def _ignored(reason: str, model=None, provider=None):
        return {"role": r, "provider": provider, "model": model,
                "status": "ignored", "reason": reason}

    def _cooled(tag: str):
        return bool(tag and _ROLE_MODEL_COOLDOWN.get(tag, 0) > time.time())

    if mode == "local_only":
        if r != "vision":
            return _ignored(
                f"role '{r}' has no local binding in local-only mode; "
                f"the local default model serves")
        tag = globals().get(
            "OLLAMA_LOCAL_VISION_MODEL", "qwen2.5vl:7b") or None
        if not tag:
            return _ignored("no local vision model is configured "
                            "(OLLAMA_LOCAL_VISION_MODEL is empty)")
        if _cooled(tag):
            return _ignored(f"local vision model '{tag}' is in failure "
                            f"cooldown, using default", model=tag,
                            provider="ollama_local")
        return {"role": r, "provider": "ollama_local", "model": tag,
                "status": "ok", "reason": ""}

    override = usage.settings().get("role_models", {}).get(r)
    if isinstance(override, dict):
        provider = (override.get("provider") or "").strip()
        model = (override.get("model") or "").strip()
        if provider not in _KNOWN_ROLE_PROVIDERS:
            return _ignored(f"role '{r}' names unknown provider "
                            f"'{provider}'", model=model or None,
                            provider=provider or None)
        if not model:
            return _ignored(f"role '{r}' names provider '{provider}' "
                            f"with no model", provider=provider)
        if _cooled(model):
            return _ignored(f"role model '{model}' is in failure "
                            f"cooldown, using default", model=model,
                            provider=provider)
        return {"role": r, "provider": provider, "model": model,
                "status": "ok", "reason": ""}
    if isinstance(override, str) and override.strip():
        tag = override.strip()
        if r == "vision":
            if _cooled(tag):
                return _ignored(f"role model '{tag}' is in failure "
                                f"cooldown, using default", model=tag,
                                provider="ollama_cloud")
            return {"role": r, "provider": "ollama_cloud", "model": tag,
                    "status": "ok", "reason": ""}
        if r == "code":
            return _ignored(_CODE_NO_LEG_REASON, model=tag)
        return _ignored(f"role '{r}' is not a routable role", model=tag)
    if r == "vision":
        tag = OLLAMA_VISION_MODEL or None
        if not tag:
            return {"role": r, "provider": None, "model": None,
                    "status": "default",
                    "reason": "no vision model configured; default serves"}
        if _cooled(tag):
            return _ignored(f"role model '{tag}' is in failure "
                            f"cooldown, using default", model=tag,
                            provider="ollama_cloud")
        return {"role": r, "provider": "ollama_cloud", "model": tag,
                "status": "ok", "reason": ""}
    if r == "code":
        return _ignored(_CODE_NO_LEG_REASON,
                         model=OLLAMA_CODE_MODEL or None)
    return {"role": r, "provider": None, "model": None,
            "status": "default",
            "reason": "no role override configured; default serves"}


# --- Pass 36 #12: vision capability + failure classes --------------
# A vision payload must only ever be served by a model known to
# accept images. The old walk retried a failed vision tag on the
# default text model (which can hallucinate a "seen" answer) and
# parked the tag for 15 minutes whatever the cause.

_VISION_MODEL_HINTS = (
    "vl", "vision", "multimodal", "llava", "moondream", "gemma",
    "gpt-4o", "gemini", "pixtral", "llama-4", "qwen2-vl", "qwen2.5vl")


def model_supports_images(provider_name: str, model) -> bool:
    """True when `model` is known to accept images. The configured
    vision tags are capable by definition; otherwise a tag hint
    (vl/vision/multimodal/gemma/...) decides. Conservative by
    design: an unknown tag is text-only, so the walk never bets an
    image on a guess."""
    tag = (model or "").strip().lower()
    if not tag:
        return False
    configured = {
        str(OLLAMA_VISION_MODEL or "").strip().lower(),
        str(globals().get("OLLAMA_LOCAL_VISION_MODEL", "") or "").strip().lower(),
    }
    if tag in configured:
        return True
    return any(h in tag for h in _VISION_MODEL_HINTS)


def classify_vision_failure(error) -> str:
    """Bucket a vision-call failure: 'missing_model' (404/410, the
    tag is not on the daemon), 'format' (400 / image rejected),
    'transient' (network, timeout, 5xx), or 'unknown'."""
    e = (error or "").strip().lower()
    if (e.startswith("http 404") or e.startswith("http 410")
            or "model not found" in e or "no such model" in e
            or "does not exist" in e):
        return "missing_model"
    if e.startswith("http 400") or (
            "image" in e and any(w in e for w in (
                "format", "invalid", "unsupported", "rejected"))):
        return "format"
    if (e.startswith("http 5") or "network" in e or "timeout" in e
            or "timed out" in e or "connection" in e
            or "urlerror" in e or "unreachable" in e):
        return "transient"
    return "unknown"


def _contents_have_images(contents) -> bool:
    """True when any content part carries inline image data."""
    try:
        for block in contents or []:
            for p in (block.get("parts") or []):
                idata = p.get("inline_data") if isinstance(p, dict) else None
                if isinstance(idata, dict) and idata.get("data"):
                    return True
    except Exception:
        return False
    return False


def local_prompt_char_budget() -> int:
    """Prompt character budget for the local leg (pass 36 #17):
    (num_ctx minus the completion reserve) * ~4 chars/token.
    The system instruction, history, tool schemas, and tool
    results must fit inside it; brain._fit_contents_to_local_
    context enforces it per turn. Never below 1024 chars."""
    try:
        ctx = int(OLLAMA_NUM_CTX)
    except (TypeError, ValueError):
        ctx = 8192
    try:
        reserve = int(usage.max_output_tokens())
    except Exception:
        reserve = 2048
    return max(1024, (max(ctx - reserve, 0)) * 4)


def _daemon_unreachable(error) -> bool:
    """True when a leg failure says the daemon itself cannot be
    reached (connection refused, DNS failure) — as opposed to a
    mid-stream drop or a slow response. Pass 36 #15: the second
    model attempt on that daemon would hit the same dead endpoint,
    so the walk skips it instead of paying a second timeout."""
    e = (error or "").lower()
    return any(w in e for w in (
        "connection refused", "refused", "name or service not known",
        "temporary failure in name resolution", "unreachable",
        "no route to host", "econnrefused"))


# --- Turn-tier routing ("Jev", Alek 2026-11-03) ---
# classify_task_role (brain) sorts a turn by WHAT it is; tiers sort it by
# HOW MUCH MODEL it deserves. A 'light' turn — short, conversational, no
# action or tool signals — starts the provider chain at a cheap leg
# (default groq) instead of burning the head of the chain (gemini) on
# chit-chat; the walk still wraps around to every other provider on
# failure, so a light turn can still reach gemini, just last. Anything
# ambiguous is 'standard' (the previous behaviour, gemini first); code
# work, preauthorized and headless/delegated turns are 'heavy' and never
# route light. The classifier is local heuristics only — zero API cost —
# and conservative by design: a false 'standard' costs a little quota,
# a false 'light' puts a weak model on a real task.

_LIGHT_BLOCKERS = (
    # action verbs / tool-domain nouns: any hit keeps the turn standard
    "open", "play", "pause", "resume", "skip", "send", "delete", "remove",
    "create", "write", "edit", "fix", "set ", "turn on", "turn off",
    "search", "find", "check", "call", "text", "message", "email", "mail",
    "remind", "schedule", "add", "download", "upload", "start", "stop",
    "launch", "run", "install", "buy", "order", "book", "screenshot",
    "look at", "volume", "mute", "record", "save", "move", "rename",
    "close", "lock", "print", "show me", "restart", "shut", "browse",
    "post", "pay", "file", "folder", "window", "song", "music", "video",
    "photo of", "picture of", "image of", "selfie", "camera", "screen",
    "weather", "timer",
    "alarm", "calendar", "meeting", "appointment", "inbox", "note",
    "list", "lights", "thermostat", "temperature", "news", "mission",
    "delegate", "app ", "website", "link", "url", "google", "spotify",
    "youtube", "brightness", "dim ",
)

_LIGHT_OPENERS = (
    "what", "who", "when", "where", "why", "how", "is ", "are ", "was ",
    "were ", "do ", "does ", "did ", "tell me", "explain", "define",
    "meaning of", "thank", "hello", "hi ", "hey", "good morning",
    "good afternoon", "good evening", "good night", "say ", "translate",
    "how do you say",
)


def classify_turn_tier(user_prompt: str, role: str = "default", *,
                       preauthorized: bool = False,
                       headless: bool = False) -> str:
    """Sort a conversational turn into 'light', 'standard' or 'heavy'.

    role is brain.classify_task_role's verdict for the same prompt.
    Never raises; anything unclear is 'standard'.
    """
    try:
        if preauthorized or headless:
            return "heavy"
        if (role or "default").strip().lower() == "code":
            return "heavy"
        low = (user_prompt or "").strip().lower()
        if not low or len(low) > 140:
            return "standard"
        if any(b in low for b in _LIGHT_BLOCKERS):
            return "standard"
        if low.endswith("?") or low.startswith(_LIGHT_OPENERS):
            return "light"
        return "standard"
    except Exception:
        return "standard"


def resolve_tier_chain_start(tier: str) -> Optional[str]:
    """Provider a tier's chain walk should start at, or None for the
    configured order. Only 'light' reorders in v1. Settings override:
    usage.settings()['turn_tiers'] = {'enabled': False} kills routing;
    {'light_provider': 'groq'} picks the light leg."""
    try:
        cfg = usage.settings().get("turn_tiers") or {}
        if cfg.get("enabled") is False:
            return None
        if (tier or "").strip().lower() == "light":
            return cfg.get("light_provider") or "groq"
    except Exception:
        return None
    return None


def _rotate_chain(chain: List[Provider], start_name: Optional[str]) -> List[Provider]:
    """Rotate chain so the first provider named start_name leads,
    preserving relative order (the rest wrap around as fallbacks).
    Unchanged when start_name is empty, absent, or already first."""
    if not start_name or not chain:
        return chain
    for i, p in enumerate(chain):
        if p.name == start_name:
            return chain[i:] + chain[:i] if i else chain
    return chain


# --- Dynamic routing: failure-adaptive role models ---
# When a role model fails (exception or empty result), _attempt_provider_call
# falls back to the default model AND parks the broken tag here so later
# turns don't keep trying it. Cooldown is 15 minutes — long enough to stop
# the hammering, short enough to recover if the tag comes back.
_ROLE_MODEL_COOLDOWN: Dict[str, float] = {}
ROLE_MODEL_COOLDOWN_S = 900


def _note_role_model_failed(tag: Optional[str],
                            duration_s: int = ROLE_MODEL_COOLDOWN_S) -> None:
    """Park a failed role model. Pass 36 #12: the park length now
    follows the failure class — a missing model parks the full 15
    minutes; a transient connection failure or a format rejection
    parks only 60 seconds, so a daemon outage stops suppressing
    recovery for a quarter of an hour."""
    try:
        if tag:
            _ROLE_MODEL_COOLDOWN[tag] = time.time() + duration_s
            add_log(f"role model '{tag}' parked for {duration_s // 60} min after failure")
            # Merger Phase 1 (blueprint SS3.2): parking a role model
            # is a classified SKIP in the failure record — the role
            # is skipped and the default model serves — not just a
            # log line (audit SS1D census: this failure never used
            # to surface anywhere durable).
            try:
                from aria.agent.failures import surface_failure
                surface_failure(
                    f"providers.role_model:{tag}",
                    f"role model '{tag}' failed and was parked",
                    decision="skip",
                    user_message="A specialist model failed; the default model is serving.")
            except Exception:
                pass
    except Exception as _e_silent:
        _config_silent.log_silent("_note_role_model_failed", _e_silent)


def clear_role_model_cooldown() -> None:
    """Reset failure-adaptive role parking (used by tests; also handy if
    Alek swaps a model tag and wants the new one tried immediately)."""
    try:
        _ROLE_MODEL_COOLDOWN.clear()
    except Exception as _e_silent:
        _config_silent.log_silent("clear_role_model_cooldown", _e_silent)


# --- Provider cooldown registry (A5; mechanism extracted in Merger
# Phase 2, blueprint SS3.1) ---
# Pattern reimplemented from Mark-XXXIX-OR's or_client.py (FatihMakes,
# CC BY-NC 4.0) — credit for the pattern: a provider that just failed
# is skipped for a cooldown window instead of being re-walked at full
# timeout cost on every subsequent turn. This is a per-PROVIDER axis,
# complementary to aria.memory's per-KEY quarantine: it also covers
# failures that never quarantine a key (5xx, network drops, empty
# results), which previously cost a full leg every single turn.
# The registry MECHANISM now lives in aria.agent.cooldown and is
# shared with the background pool (orpool) — one mechanism, two
# scopes. The wrappers below keep A5's exact behaviour, including
# the HTTP-400 carve-out.
PROVIDER_COOLDOWN_S = 60
_PROVIDER_COOLDOWN = CooldownRegistry(PROVIDER_COOLDOWN_S)


def _provider_in_cooldown(name: str) -> bool:
    return _PROVIDER_COOLDOWN.in_cooldown(name)


def _note_provider_failed(name: str, last_error: Optional[str] = None) -> None:
    # HTTP 400 is OUR malformed payload, not a sick provider — the same
    # payload would fail everywhere, so cooling the provider down
    # would only hide the road. (Matches the no-quarantine rule for
    # 400 in OpenAICompatProvider.call.)
    # Duration comes from the failure class (Gap fill 5; the class
    # table lives with the per-model axis below — it is defined
    # later in the module, resolved at call time).
    cls = _classify_error(last_error)
    if cls == "bad_request":
        return
    _PROVIDER_COOLDOWN.mark_failed(name, _FAILURE_DURATIONS[cls][0])


def _note_provider_ok(name: str) -> None:
    _PROVIDER_COOLDOWN.mark_ok(name)


def clear_provider_cooldown() -> None:
    """Reset the provider cooldown registry (used by tests; also handy
    after swapping a key/model and wanting an immediate retry)."""
    _PROVIDER_COOLDOWN.clear()


# --- Per-(provider, model) cooldown + failure classes (Gap fill 5) ---
# The A5 registry above cools a whole PROVIDER for a flat 60 s.
# Two gaps that left money on the table: (1) a 429 is usually a
# per-MODEL quota — cooling all of Gemini because one model is
# rate-limited throws away the models that would answer; (2) a
# flat 60 s treats an auth failure (won't heal in a minute) the
# same as a network blip (heals in seconds). This second axis
# keys the shared registry by 'provider:model' and picks the
# duration from the failure class read off last_error.
# The HTTP-400 carve-out applies on BOTH axes (our malformed
# payload is not a sick provider OR a sick model).
_MODEL_COOLDOWN = CooldownRegistry(PROVIDER_COOLDOWN_S)

# failure class -> (provider-axis seconds, model-axis seconds)
_FAILURE_DURATIONS = {
    "rate_limit": (60, 300),    # per-model quota: park the model long
    "auth": (300, 300),         # key/permission: won't heal in a minute
    "server": (60, 60),         # 5xx
    "transient": (30, 30),      # network drop, timeout, raised, empty
}


def _classify_error(last_error: Optional[str]) -> str:
    """Bucket a provider.last_error string into a failure class.
    Anything unrecognized is 'transient' (shortest cooldown) —
    unknown failures shouldn't park things for long."""
    e = (last_error or "").strip()
    if e.startswith("HTTP 400"):
        return "bad_request"
    if e.startswith("HTTP 429"):
        return "rate_limit"
    if e.startswith("HTTP 401") or e.startswith("HTTP 403"):
        return "auth"
    if e.startswith("HTTP 5"):
        return "server"
    return "transient"


def _pair_key(name: str, model: Optional[str]) -> str:
    return f"{name}:{(model or '').strip()}"


def _model_in_cooldown(name: str, model: Optional[str]) -> bool:
    return _MODEL_COOLDOWN.in_cooldown(_pair_key(name, model))


def _note_model_failed(name: str, model: Optional[str],
                       last_error: Optional[str] = None) -> None:
    cls = _classify_error(last_error)
    if cls == "bad_request":
        return
    _MODEL_COOLDOWN.mark_failed(
        _pair_key(name, model), _FAILURE_DURATIONS[cls][1])


def _note_model_ok(name: str, model: Optional[str]) -> None:
    _MODEL_COOLDOWN.mark_ok(_pair_key(name, model))


def clear_model_cooldown() -> None:
    """Reset the per-(provider, model) registry (tests; key swaps)."""
    _MODEL_COOLDOWN.clear()


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


def _role_override_leg(provider_name: str, mode: Optional[str] = None,
                       override_provider: Optional[str] = None) -> bool:
    """The leg a role model_override may run on. Auto mode: the
    ollama_cloud leg only — an override must never retag a keyed
    cloud leg or the local leg. Local-only mode: the ollama_local
    leg, the only leg there is; resolve_role_model guarantees the
    override in that mode is the local vision tag, never a cloud
    model.

    Pass 36 #06: mode= pins the answer to one turn's policy;
    None (default) reads live, as before.
    Pass 36 #10: override_provider= names the leg an explicit
    role binding chose (resolve_role_route). An explicitly bound
    provider is eligible in auto mode; in local_only only
    ollama_local ever is, whatever the binding says."""
    effective = mode if mode is not None else provider_mode()
    if effective == "local_only":
        return provider_name == "ollama_local"
    if override_provider:
        return provider_name == override_provider
    return provider_name == "ollama_cloud"


def _attempt_provider_call(provider, system_instruction, contents, tool_decls,
                           on_text_chunk, model_override: Optional[str],
                           policy: Optional[Dict[str, Any]] = None,
                           override_provider: Optional[str] = None,
                           has_images: bool = False,
                           timeout_cap: Optional[float] = None):
    """Call one provider, optionally on a role-specific model.

    A failing role model (exception OR empty result — provider.call swallows
    HTTP errors into last_error + None) falls back to the provider's default
    model WITHOUT quarantining the API key (the key is fine; the tag or
    payload was bad). The provider's configured model is always restored.
    timeout_cap: the walk's remaining request-deadline seconds; the
    leg caps its connect + first-output allowances by it (pass 36 #15).
    Returns (data, used_fallback).
    """
    use_override = bool(model_override) and _role_override_leg(
        provider.name, (policy or {}).get("mode") if policy else None,
        override_provider)
    saved = provider.model if use_override else None
    attempted_models: List[str] = []
    # Pass 36 #15: only hand the cap to a call() that accepts it —
    # test doubles take *args/**kwargs or a fixed arity.
    try:
        _call_params = inspect.signature(provider.call).parameters
        _call_accepts_cap = ("timeout_cap" in _call_params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in _call_params.values()))
    except (TypeError, ValueError):
        _call_accepts_cap = False
    call_kwargs = ({"timeout_cap": timeout_cap}
                   if timeout_cap is not None and _call_accepts_cap else {})
    if use_override:
        # Pass 36 #10: report the route actually attempted.
        add_log(f"role override: attempting {provider.name} on model "
                f"'{model_override}'")
    try:
        if use_override:
            provider.model = model_override
        attempted_models.append(getattr(provider, "model", "") or "")
        try:
            data = provider.call(
                system_instruction, contents,
                tool_decls=tool_decls, on_text_chunk=on_text_chunk,
                **call_kwargs,
            )
        except (runtime.Cancelled, usage.BudgetExceeded):
            raise
        except Exception as e:
            data = None
            provider.last_error = f"raised: {e}"
        if _usable_response(data):
            return data, False
        if not use_override:
            return data, False
        add_log(f"provider {provider.name}: role model '{model_override}' failed "
                f"({provider.last_error}); retrying default model (key NOT quarantined)")
        # Pass 36 #12: park by failure class, and never retry an
        # image payload on a default model that cannot accept
        # images — that retry used to hand the picture to a text
        # model that could hallucinate a "seen" answer.
        failure_kind = classify_vision_failure(provider.last_error)
        if failure_kind == "missing_model":
            _note_role_model_failed(model_override)
        else:
            _note_role_model_failed(model_override, duration_s=60)
        _note_model_failed(provider.name, model_override,
                           provider.last_error)
        # Pass 36 #15: the daemon itself is unreachable (connection
        # refused / DNS) — the default-model retry would hit the
        # same dead endpoint, so skip it instead of paying a second
        # timeout on a call that cannot succeed.
        if (provider.name == "ollama_local"
                and _daemon_unreachable(provider.last_error)):
            provider.last_error = (
                f"local daemon unreachable ({provider.last_error}); "
                f"default-model retry skipped")
            add_log(f"provider {provider.name}: {provider.last_error}")
            return data, False
        if has_images and not model_supports_images(provider.name, saved):
            provider.last_error = (
                f"vision model '{model_override}' failed "
                f"({failure_kind}: {provider.last_error}); default model "
                f"'{saved}' cannot accept images — not retried")
            add_log(f"provider {provider.name}: {provider.last_error}")
            return data, False
        provider.model = saved
        attempted_models.append(getattr(provider, "model", "") or "")
        try:
            data = provider.call(
                system_instruction, contents,
                tool_decls=tool_decls, on_text_chunk=on_text_chunk,
                **call_kwargs,
            )
        except (runtime.Cancelled, usage.BudgetExceeded):
            raise
        except Exception as e:
            data = None
            provider.last_error = f"raised: {e}"
        return data, True
    finally:
        if saved is not None:
            provider.model = saved
        # Pass 36 #18: which models were actually attempted on this
        # leg (override, then default on fallback) — the walk uses
        # this for per-(provider, model) cooldowns and health, so a
        # default-model retry can no longer clear the failed
        # specialist's pair cooldown or vice versa.
        provider._last_attempted_models = attempted_models


def provider_call(
    system_instruction: str,
    contents: List[Dict[str, Any]],
    tool_decls: Optional[List[Dict[str, Any]]] = None,
    on_text_chunk: Optional[Callable[[Optional[str]], None]] = None,
    model_override: Optional[str] = None,
    only_provider: Optional[str] = None,
    chain_start: Optional[str] = None,
    policy: Optional[Dict[str, Any]] = None,
    override_provider: Optional[str] = None,
    deadline: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Walk the provider chain in order; first provider that returns
    candidates wins. Quarantined/unconfigured providers are skipped
    without network traffic, so recovery is automatic on quarantine expiry.

    deadline: one shared monotonic deadline for the whole request
    (pass 36 #15). Defaults to now + REQUEST_DEADLINE_S (900s); the
    brain passes its turn deadline instead. The walk stops starting
    new attempts past it, and each attempt's connect + first-output
    allowances are capped by the remaining time — so a 180s local
    attempt plus a 300s vision attempt can no longer stack past the
    turn's budget. It bounds attempt starts, not a stream already
    producing output.

    model_override: temporarily run the role-override leg on a different
    model tag (see resolve_role_model) — the ollama_cloud leg in auto
    mode, the ollama_local leg in local_only mode. Prefer passing role=
    to provider_text; the main agent loop leaves this unset.
    only_provider: restrict the walk to a single named provider (used for
    vision, where only ollama_cloud can serve the image payload in auto
    mode — walking the other providers would 400/404/403 and quarantine
    THEIR keys; in local_only mode vision names ollama_local instead).
    chain_start: begin the walk at this provider instead of the head of
    the chain (turn-tier routing, see classify_turn_tier); the remaining
    providers wrap around as fallbacks in their configured order.
    policy: one turn's captured request policy (see
    current_request_policy()). The chain is snapshotted from it, the
    role tag is resolved from it, and the walk re-checks the live mode
    + generation against it before EVERY attempt (#06): a toggle flip
    mid-turn aborts the walk (returning None, with a 'mode_change'
    failure recorded) instead of starting more cloud calls under a
    stale policy. Already-completed tool receipts are preserved by
    the caller (brain persists them before reporting the abort).
    """
    global ACTIVE_PROVIDER
    ctx = runtime.current()
    ctx.provider_failures = []
    ctx.budget_error = ''
    # One authoritative policy per turn: capture once, re-check often.
    policy = dict(policy) if policy else current_request_policy()
    walk_mode = policy.get("mode") or provider_mode()
    # Pass 36 #15: one shared monotonic deadline for the whole
    # request. The walk stops starting attempts past it, and each
    # attempt is capped by the remaining time.
    request_deadline = (deadline if deadline is not None
                        else time.monotonic() + REQUEST_DEADLINE_S)
    # Pass 36 #12: an image payload is only ever served by a model
    # known to accept images — the walk skips text-only legs for it
    # instead of retrying the picture on the default text model.
    has_images = _contents_have_images(contents)
    # _attempt_provider_call takes optional policy= /
    # override_provider= / has_images= / timeout_cap= kwargs; older
    # doubles of it (the tier-routing tests' 6-arg fake) take none of
    # them. Pass each only when the helper accepts it, so a double
    # still walks exactly like the real helper.
    try:
        _attempt_params = inspect.signature(
            _attempt_provider_call).parameters
        _attempt_accepts_policy = "policy" in _attempt_params
        _attempt_accepts_provider = "override_provider" in _attempt_params
        _attempt_accepts_images = "has_images" in _attempt_params
        _attempt_accepts_cap = "timeout_cap" in _attempt_params
    except (TypeError, ValueError):
        _attempt_accepts_policy = False
        _attempt_accepts_provider = False
        _attempt_accepts_images = False
        _attempt_accepts_cap = False
    chain = usage.order(_build_chain())
    if not chain:
        add_log("provider_call: chain is empty, nothing configured")
        return None
    if ctx.kind == 'foreground' and not usage.settings().get('provider_order'):
        with _HEALTH_LOCK:
            recent = _RECENT_FOREGROUND_PROVIDER.get(ctx.project)
        if recent and time.monotonic() - recent[1] < 300:
            # Keep explicit project ordering authoritative. Otherwise a working
            # fallback gets the next turn instead of paying the slow leg again.
            chain.sort(key=lambda provider: provider.name != recent[0])
    # Local-only: the walk is the single local leg, full stop. A tier
    # chain_start must not rotate it or — worse — prepend an opt-in
    # leg via _providers_by_name below: that would put a cloud
    # provider back on a strict local-only turn.
    if chain_start and walk_mode != "local_only":
        rotated = _rotate_chain(chain, chain_start)
        if rotated is not chain and rotated and rotated[0].name == chain_start:
            add_log(f"tier routing: chain starts at '{chain_start}'")
            chain = rotated
        elif not chain or chain[0].name != chain_start:
            # Named start is not in the configured chain: an opt-in
            # leg (freellmapi) or a provider dropped from
            # PROVIDER_CHAIN. Lead with it — the configured chain
            # stays as fallback — instead of silently yielding
            # the default order (pass 25). Unknown names log and
            # keep the configured order.
            extra = _providers_by_name(chain_start)
            if extra:
                add_log(f"tier routing: '{chain_start}' is not in "
                        f"the configured chain; leading with the "
                        f"opt-in leg")
                chain = extra + chain
            else:
                add_log(f"tier routing: unknown chain start "
                        f"'{chain_start}'; using the configured "
                        f"chain order")
    # A5: skip providers inside their failure-cooldown window — unless
    # EVERY candidate is cooling down, in which case walk them anyway:
    # a possibly-sick provider beats a guaranteed-dead turn.
    candidates = [p for p in chain
                  if (not only_provider or p.name == only_provider)
                  and p.is_available()]
    ignore_cooldowns = bool(candidates) and all(
        _provider_in_cooldown(p.name) for p in candidates)

    def _eff_model(p) -> Optional[str]:
        # The model this walk would actually attempt on p: the
        # role override applies to one leg only (mirrors
        # _attempt_provider_call; see _role_override_leg).
        if model_override and _role_override_leg(p.name, walk_mode,
                                                 override_provider):
            return model_override
        return getattr(p, "model", None)

    # Gap fill 5: the per-(provider, model) axis gets the same
    # escape hatch — skip cooling pairs unless EVERY candidate's
    # pair is cooling, in which case walk them anyway.
    ignore_pairs = bool(candidates) and all(
        _model_in_cooldown(p.name, _eff_model(p)) for p in candidates)
    # Pass 36 #10: a role override with no eligible leg in this
    # walk is IGNORED with its reason in the log — never silently.
    # (The eligible set respects only_provider: a vision-only walk
    # on ollama_cloud counts that leg even though it is not in the
    # configured chain.)
    if model_override:
        walk_names = [p.name for p in chain
                      if not only_provider or p.name == only_provider]
        if not any(_role_override_leg(n, walk_mode, override_provider)
                   for n in walk_names):
            bound = (f" (bound to {override_provider})"
                     if override_provider else "")
            add_log(f"role override '{model_override}'{bound} ignored: "
                    f"no eligible leg in this walk can serve it; "
                    f"default model serves")
    failed_endpoints = set()
    for provider in chain:
        runtime.check_cancelled()
        # Pass 36 #06: re-check eligibility before EVERY transport
        # attempt. A mode flip (or a generation bump from any mode
        # set) aborts the walk here — no further provider is
        # attempted under the stale policy, and the abort is
        # recorded as a typed failure for the caller to report.
        if (provider_mode() != walk_mode
                or _MODE_GENERATION != policy.get("generation")):
            add_log(f"provider mode changed mid-turn ({walk_mode} -> "
                    f"{provider_mode()}); aborting provider walk")
            ctx.provider_failures.append({
                'provider': getattr(provider, 'name', '?'),
                'model': getattr(provider, 'model', ''),
                'kind': 'mode_change', 'code': None,
                'summary': (f"provider mode changed from '{walk_mode}' "
                            f"to '{provider_mode()}' during this turn; "
                            f"the walk stopped before calling another model"),
                'status': 'error', 'checked_at': time.time()})
            return None
        if only_provider and provider.name != only_provider:
            continue
        # Pass 36 #15: the shared request deadline — no new attempt
        # starts past it, whatever leg is next. The remaining time
        # becomes this attempt's timeout cap, and the OPS log gets
        # the progress (how much of the request budget is left).
        remaining = request_deadline - time.monotonic()
        if remaining <= 0:
            add_log(f"provider {provider.name}: request deadline "
                    f"reached, not attempting")
            _record_provider_failure(provider, kind='deadline')
            return None
        timeout_cap = max(1.0, remaining)
        endpoint = getattr(provider, 'base_url', '')
        if not provider.is_available():
            # Pass 36 #05: a strict-rejected local leg (non-loopback
            # host) is a typed verification failure, not "unconfigured".
            if getattr(provider, "name", "") == "ollama_local" and callable(
                    getattr(provider, "verify", None)):
                verification = provider.verify()
                if not verification.get("ok"):
                    _record_provider_failure(provider, kind='local_unverified')
                    if ctx.provider_failures:
                        ctx.provider_failures[-1]["summary"] = verification.get(
                            "reason") or "local Ollama verification failed"
                    continue
            key = getattr(provider, 'api_key', '')
            if not key or key == 'INSERT' or not getattr(provider, 'model', ''):
                _record_provider_failure(provider, kind='unconfigured')
            else:
                code = _config_silent._KEY_QUARANTINE_CODE.get(key)
                retry_at = _config_silent._KEY_QUARANTINE_UNTIL.get(key, 0)
                kind = 'network' if code == 'network_drop' else None
                _record_provider_failure(provider, kind=kind,
                                         code=code if isinstance(code, int) else None,
                                         retry_at=retry_at)
            continue
        # Pass 36 #12: an image payload is served only by a model
        # known to accept images. A text-only leg is skipped with a
        # typed reason — never handed the picture to hallucinate a
        # "seen" answer.
        if has_images and not model_supports_images(
                provider.name, _eff_model(provider)):
            add_log(f"provider {provider.name}: model "
                    f"'{_eff_model(provider)}' cannot accept images, skipping")
            _record_provider_failure(provider, kind='no_vision_capability')
            continue
        if endpoint and endpoint in failed_endpoints:
            _record_provider_failure(provider, kind='network')
            continue
        if not ignore_cooldowns and _provider_in_cooldown(provider.name):
            add_log(f"provider {provider.name} in failure cooldown, skipping")
            _record_provider_failure(provider, kind='cooldown',
                                     retry_at=_PROVIDER_COOLDOWN.until(provider.name))
            continue
        if not ignore_pairs and _model_in_cooldown(
                provider.name, _eff_model(provider)):
            add_log(f"model {_eff_model(provider)} on provider "
                    f"{provider.name} in failure cooldown, skipping")
            _record_provider_failure(provider, kind='cooldown',
                                     retry_at=_MODEL_COOLDOWN.until(_pair_key(provider.name, _eff_model(provider))))
            continue
        # Pass 36 #05: verify the local leg before attempting it. A
        # daemon that answered but lacks the model is a definitive
        # failure — record it typed and skip, instead of falling
        # through to cloud (in auto) or retrying a dead tag (in
        # local-only). A merely unreachable daemon is transient:
        # the attempt below fails fast as a network error, as before.
        if getattr(provider, "name", "") == "ollama_local" and callable(
                getattr(provider, "verify", None)):
            verification = provider.verify()
            if verification.get("code") == "model_missing":
                _record_provider_failure(provider, kind='local_unverified')
                if ctx.provider_failures:
                    ctx.provider_failures[-1]["summary"] = verification.get(
                        "reason") or "local Ollama verification failed"
                add_log(f"provider ollama_local: {verification.get('reason')}")
                continue
        _note_call(provider.name)
        attempt_started = time.monotonic()
        add_log(f"provider {provider.name}: attempting model "
                f"'{_eff_model(provider)}' "
                f"(request deadline {remaining:.0f}s left)")
        try:
            attempt_kwargs = {}
            if _attempt_accepts_policy:
                attempt_kwargs["policy"] = policy
            if _attempt_accepts_provider:
                attempt_kwargs["override_provider"] = override_provider
            if _attempt_accepts_images:
                attempt_kwargs["has_images"] = has_images
            if _attempt_accepts_cap:
                attempt_kwargs["timeout_cap"] = timeout_cap
            data, _used_fallback = _attempt_provider_call(
                provider, system_instruction, contents, tool_decls,
                on_text_chunk, model_override, **attempt_kwargs,
            )
        except runtime.Cancelled:
            raise
        except usage.BudgetExceeded as exc:
            ctx.budget_error = str(exc)
            _record_provider_failure(provider, kind='budget')
            add_log(f'Budget: {exc}')
            continue
        except Exception as e:  # never let one provider kill the turn
            add_log(f"provider {provider.name} raised: {e}")
            provider.last_error = f'raised: {type(e).__name__}'
            data = None
        if _usable_response(data):
            ctx.budget_error = ''
            # Pass 36 #18: the health entry names the model
            # ACTUALLY attempted (override, then default on
            # fallback) — the per-attempt identity the frame and
            # the cooldowns read — not the leg's configured model.
            _attempted = (getattr(provider, "_last_attempted_models", None)
                          or [getattr(provider, "model", "")])
            with _HEALTH_LOCK:
                _PROVIDER_HEALTH[provider.name] = {
                    'provider': provider.name, 'model': _attempted[-1],
                    'status': 'ok', 'summary': 'last model request succeeded',
                    'checked_at': time.time(),
                    'latency_ms': int((time.monotonic() - attempt_started) * 1000),
                }
                if ctx.kind == 'foreground' and not chain_start \
                        and provider.name != 'ollama_local':
                    # A tier-routed success says nothing about which
                    # provider a standard turn should prefer — don't let
                    # a light turn's cheap leg become the sticky head.
                    # Same for the local leg (2026-11-09): an emergency
                    # success while the cloud is down must not promote
                    # the slowest leg to the head of the chain — the
                    # cloud legs get first shot again on the next turn
                    # and recovery stays automatic.
                    _RECENT_FOREGROUND_PROVIDER[ctx.project] = (provider.name, time.monotonic())
            _note_provider_ok(provider.name)
            # Pass 36 #18: cool down / clear by the model ACTUALLY
            # attempted, not the requested override. A successful
            # default retry must not clear the failed specialist's
            # pair cooldown (and a failed override + failed default
            # cools both). _last_attempted_models is recorded by
            # _attempt_provider_call; fakes that never set it fall
            # back to the effective model.
            _attempted = (getattr(provider, "_last_attempted_models", None)
                          or [_eff_model(provider)])
            _note_model_ok(provider.name, _attempted[-1])
            if ACTIVE_PROVIDER != provider.name:
                note = " (fallback)"
                add_log(f"provider now serving: {provider.name}{note}")
                ACTIVE_PROVIDER = provider.name
            return data
        _note_failover(provider.name)
        _note_provider_failed(provider.name,
                              getattr(provider, "last_error", None))
        # Pass 36 #18: every model actually attempted on this leg
        # failed (override, then default on fallback) — cool each
        # one by its own pair key, not just the requested override.
        _attempted = (getattr(provider, "_last_attempted_models", None)
                      or [_eff_model(provider)])
        for _model in _attempted:
            _note_model_failed(provider.name, _model,
                               getattr(provider, "last_error", None))
        _record_provider_failure(provider, latency_ms=int((time.monotonic() - attempt_started) * 1000))
        if endpoint and ctx.provider_failures[-1]['kind'] == 'network':
            failed_endpoints.add(endpoint)
        add_log(f"provider {provider.name} returned nothing, trying next")
    add_log("provider_call: chain exhausted, all providers down")
    return None


def provider_text(system_instruction: str, user_text: str, role: str = "default") -> str:
    """One-shot plain-text call through the provider chain (no tools).

    Used by tools that need a quick classification/read, e.g. triage.
    role: "default" (conversational model), "vision", or "code" — resolved
    via resolve_role_route(); unknown/empty roles use the default model.
    A role with no eligible leg is reported as ignored (with its
    reason) and served by the default model — never silently dropped.
    Returns the concatenated response text, or a bracketed error string.
    """
    try:
        route = resolve_role_route(role)
        if route["status"] == "ignored":
            add_log(f"provider_text role '{role}' ignored: "
                    f"{route['reason']}")
        contents = [{"role": "user", "parts": [{"text": str(user_text)}]}]
        data = provider_call(system_instruction, contents, tool_decls=None,
                             model_override=route.get("model")
                             if route["status"] == "ok" else None,
                             override_provider=route.get("provider")
                             if route["status"] == "ok" else None)
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
