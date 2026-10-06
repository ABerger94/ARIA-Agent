"""ARIA ULTIMATE — Module 1: Routines.

Named, replayable multi-step automations. "Watch me do this, save it as X."

Storage: ~/ARIA/routines/<name>.json ->
    {"name": ..., "created": iso, "trusted": bool, "steps": [{"tool": ..., "args": {...}}, ...]}
Name sanitization: lowercase, alnum + `_`/`-` only, max 40 chars.

Recording state lives module-level here; dispatch checks it through the
functions below (never by importing this module at its own top level).

Top-level imports: stdlib + aria.config only. dispatch/approval are imported
lazily inside functions — never at module level (no import cycles).
"""

# ============================================================================
# COORDINATOR INTEGRATION — EXACT CAPTURE-HOOK SNIPPET FOR dispatch.execute_tool
# ----------------------------------------------------------------------------
# File: aria/tools/dispatch.py, inside execute_tool().
# Insert AFTER execution completes (after "# 7. Truncation and caching",
# just before `return r, False`). The import is lazy and lives inside the
# function body: routines.py never imports dispatch at module level, and
# dispatch never imports routines at module level, so no import cycle.
#
# Preauthorized calls are skipped so that approved replays and trusted-routine
# replays don't re-record themselves while a recording happens to be open.
#
# --- BEGIN SNIPPET (paste verbatim, at function-body indentation) ---
#     # Module 1 — Routine capture (ARIA ULTIMATE)
#     if not preauthorized:
#         from aria import routines as _routines_mod
#         _routines_mod.maybe_capture(fn_name, args or {})
# --- END SNIPPET ---
# ============================================================================

import json
import os
import re
import time
from datetime import datetime, timezone

from aria import config

MAX_NAME_LEN = 40

# Tools never captured into a routine: the routine tools themselves, the
# approval controls, and save_memory (memory writes are personal, not
# procedural — do NOT capture them).
_EXCLUDED_TOOLS = frozenset({
    "routine_record_start",
    "routine_record_stop",
    "run_routine",
    "list_routines",
    "delete_routine",
    "describe_routine",
    "trust_routine",
    "untrust_routine",
    "approve",
    "deny",
    "set_approval_mode",
    "save_memory",
})

# Module-level recording state. Set/checked via functions only.
_RECORDING = {"name": None, "steps": [], "started_ts": None}

_NAME_BAD_CHARS = re.compile(r"[^a-z0-9_-]")
_PARAM_RE = re.compile(r"\{\{(\w+)\}\}")


def _routines_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "ARIA", "routines")


def _routine_path(name: str) -> str:
    return os.path.join(_routines_dir(), f"{name}.json")


def sanitize_name(name) -> str:
    """Lowercase, alnum + `_`/`-` only, max 40 chars. '' if nothing valid."""
    s = _NAME_BAD_CHARS.sub("_", str(name or "").lower().strip())
    s = re.sub(r"_+", "_", s).strip("_-")
    return s[:MAX_NAME_LEN]


def is_recording() -> bool:
    return _RECORDING["name"] is not None


def start_recording(name: str) -> str:
    """Begin capturing tool calls into a new routine."""
    if is_recording():
        return (f"[Already recording routine '{_RECORDING['name']}'. "
                f"Call routine_record_stop() first.]")
    sname = sanitize_name(name)
    if not sname:
        return "[Routine name has no valid characters — use letters, numbers, _ or -.]"
    if os.path.exists(_routine_path(sname)):
        return (f"[Routine '{sname}' already exists. Delete it first "
                f"(delete_routine) or pick a new name.]")
    _RECORDING["name"] = sname
    _RECORDING["steps"] = []
    _RECORDING["started_ts"] = time.time()
    config.add_log(f"[routines] recording started: '{sname}'")
    return (f"Recording routine '{sname}'. Perform the steps now — each tool "
            f"call is captured — then call routine_record_stop().")


def maybe_capture(tool: str, args=None) -> bool:
    """Record one executed call. No-op unless a recording is open.

    Called from the dispatch.execute_tool capture hook. Returns True when the
    step was captured.
    """
    if not is_recording():
        return False
    if tool in _EXCLUDED_TOOLS:
        return False
    try:
        _RECORDING["steps"].append({"tool": tool, "args": dict(args or {})})
    except Exception:
        return False
    return True


def stop_recording() -> str:
    """End capture, persist the routine JSON, return step count + summary."""
    if not is_recording():
        return "[Not recording — call routine_record_start(name) first.]"
    name = _RECORDING["name"]
    steps = _RECORDING["steps"]
    data = {
        "name": name,
        "created": datetime.now(timezone.utc).isoformat(),
        "trusted": False,  # routine_record_stop always sets trusted=false
        "steps": steps,
    }
    try:
        os.makedirs(_routines_dir(), exist_ok=True)
        with open(_routine_path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        _RECORDING["name"] = None
        _RECORDING["steps"] = []
        _RECORDING["started_ts"] = None
        return f"[Failed to save routine '{name}': {e}]"
    _RECORDING["name"] = None
    _RECORDING["steps"] = []
    _RECORDING["started_ts"] = None
    config.add_log(f"[routines] saved '{name}' ({len(steps)} steps)")
    return (f"Saved routine '{name}': {len(steps)} step(s). "
            f"Replay it with run_routine('{name}'). "
            f"Use {{{{param}}}} placeholders in args for parameterized replays.")


def _load_routine(sname: str):
    path = _routine_path(sname)
    if not os.path.exists(path):
        return None, (f"[Routine '{sname}' not found. "
                      f"Call list_routines() to see saved routines.]")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return None, f"[Routine '{sname}' is unreadable: {e}]"
    return data, None


def _substitute(value, params: dict):
    """Recursively replace {{param}} placeholders in string values."""
    if isinstance(value, str):
        return _PARAM_RE.sub(lambda m: str(params.get(m.group(1), m.group(0))),
                             value)
    if isinstance(value, dict):
        return {k: _substitute(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, params) for v in value]
    return value


def run_routine_impl(name: str, params=None) -> str:
    """Replay a routine's steps via dispatch.execute_tool.

    {{param}} placeholders in string arg values are substituted from `params`.
    Every step replays with preauthorized=True EXCEPT destructive steps in an
    untrusted routine, which pass through the approval gate (Module 4).
    """
    params = params or {}
    sname = sanitize_name(name)
    if not sname:
        return "[Routine name has no valid characters.]"
    data, err = _load_routine(sname)
    if err:
        return err
    steps = data.get("steps", [])
    if not steps:
        return f"[Routine '{sname}' has no steps.]"
    trusted = bool(data.get("trusted", False))

    from aria import approval as _approval_mod  # lazy: no import cycle
    from aria.tools import dispatch as _dispatch_mod  # lazy: no import cycle

    lines = []
    for i, step in enumerate(steps, 1):
        tool = step.get("tool", "?")
        args = _substitute(step.get("args", {}), params)
        # Destructive steps in an untrusted routine go through approval.
        preauthorized = True
        try:
            if not trusted and _approval_mod.needs_approval(tool, args):
                preauthorized = False
        except Exception:
            preauthorized = True
        try:
            result, _ok = _dispatch_mod.execute_tool(tool, args,
                                                     preauthorized=preauthorized)
        except Exception as e:
            result = f"[Tool Error: {e}]"
        preview = config.redact(str(result)).replace("\n", " ")[:200]
        lines.append(f"{i}. {tool} -> {preview}")
    config.add_log(f"[routines] ran '{sname}' ({len(steps)} steps, trusted={trusted})")
    return (f"Routine '{sname}' replayed ({len(steps)} steps, "
            f"trusted={'yes' if trusted else 'no'}):\n" + "\n".join(lines))


def _save_trusted(sname: str, trusted: bool) -> str:
    data, err = _load_routine(sname)
    if err:
        return err
    data["trusted"] = bool(trusted)
    try:
        with open(_routine_path(sname), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        return f"[Failed to update routine '{sname}': {e}]"
    config.add_log(f"[routines] '{sname}' trusted={trusted}")
    state = "trusted" if trusted else "untrusted"
    extra = (" Destructive steps will now replay without approval."
             if trusted else
             " Destructive steps will pause for approval again.")
    return f"Routine '{sname}' is now {state}.{extra}"


# ---------------- Tool wrappers (builtins-style) ----------------
# Registry names (wired by coordinator): routine_record_start,
# routine_record_stop, run_routine, list_routines, delete_routine,
# describe_routine, trust_routine, untrust_routine.


def tool_routine_record_start(name: str) -> str:
    return start_recording(name)


def tool_routine_record_stop() -> str:
    return stop_recording()


def tool_run_routine(name: str, params_json: str = "{}") -> str:
    try:
        params = json.loads(params_json or "{}")
    except Exception as e:
        return f"[Invalid params_json: {e}]"
    if not isinstance(params, dict):
        return "[params_json must be a JSON object, e.g. '{\"who\": \"alek\"}'.]"
    return run_routine_impl(name, params)


def tool_list_routines() -> str:
    d = _routines_dir()
    if not os.path.isdir(d):
        return "[No routines saved yet.]"
    entries = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        entries.append(data)
    if not entries:
        return "[No routines saved yet.]"
    lines = []
    for data in entries:
        created = str(data.get("created", "?"))[:10]
        trusted = "yes" if data.get("trusted") else "no"
        lines.append(f"{data.get('name', '?')} — {len(data.get('steps', []))} "
                     f"step(s) — created {created} — trusted: {trusted}")
    return f"{len(lines)} routine(s):\n" + "\n".join(lines)


def tool_delete_routine(name: str) -> str:
    sname = sanitize_name(name)
    if not sname:
        return "[Routine name has no valid characters.]"
    path = _routine_path(sname)
    if not os.path.exists(path):
        return f"[Routine '{sname}' not found.]"
    try:
        os.remove(path)
    except Exception as e:
        return f"[Failed to delete routine '{sname}': {e}]"
    config.add_log(f"[routines] deleted '{sname}'")
    return f"Deleted routine '{sname}'."


def tool_describe_routine(name: str) -> str:
    sname = sanitize_name(name)
    if not sname:
        return "[Routine name has no valid characters.]"
    data, err = _load_routine(sname)
    if err:
        return err
    lines = [f"Routine '{sname}' — {len(data.get('steps', []))} step(s), "
             f"created {str(data.get('created', '?'))[:16]}, "
             f"trusted: {'yes' if data.get('trusted') else 'no'}"]
    for i, step in enumerate(data.get("steps", []), 1):
        argstr = ", ".join(f"{k}={v!r}" for k, v in
                           (step.get("args") or {}).items())
        lines.append(f"  {i}. {step.get('tool', '?')}({argstr})")
    return "\n".join(lines)


def tool_trust_routine(name: str) -> str:
    sname = sanitize_name(name)
    if not sname:
        return "[Routine name has no valid characters.]"
    return _save_trusted(sname, True)


def tool_untrust_routine(name: str) -> str:
    sname = sanitize_name(name)
    if not sname:
        return "[Routine name has no valid characters.]"
    return _save_trusted(sname, False)
