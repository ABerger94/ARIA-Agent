"""ARIA ULTIMATE — Module 1: Routines.

Named, replayable multi-step automations. "Watch me do this, save it as X."

Storage: <install root>/routines/<name>.json (aria/data_dir.py, V20) ->
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
# Preauthorized calls are skipped so that trusted-routine replays
# don't re-record themselves while a recording happens to be open.
# The exception is an approval replay: approve_token() captures the
# replayed call itself, explicitly, when it actually executed (pass
# 25) — the parked call never reached this hook, and the replay
# runs preauthorized, so without that call the approved action
# would be lost from the recording.
#
# --- BEGIN SNIPPET (paste verbatim, at function-body indentation) ---
#     # Module 1 — Routine capture (ARIA ULTIMATE)
#     if not preauthorized:
#         from aria import routines as _routines_mod
#         _routines_mod.maybe_capture(fn_name, args or {})
# --- END SNIPPET ---
# ============================================================================

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict

from aria import config
from aria import data_dir

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
    "restart_program",
    "approve",
    "deny",
    "set_approval_mode",
    "save_memory",
})

# Module-level recording state. Set/checked via functions only.
# `project` is the workspace the recording started in (pass 25):
# a recording captures only calls made in that project — without
# it, a project switch mid-recording silently mixed another
# project's actions into this routine.
_RECORDING = {"name": None, "steps": [], "started_ts": None,
              "project": None}

_NAME_BAD_CHARS = re.compile(r"[^a-z0-9_-]")
_PARAM_RE = re.compile(r"\{\{(\w+)\}\}")


def _routines_dir() -> str:
    # Install root via aria.data_dir (V20); first resolve
    # migrates the legacy ~/ARIA/routines tree copy-if-missing.
    return data_dir.subdir("routines")


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
    try:
        from aria import workspaces
        _RECORDING["project"] = workspaces.current()
    except Exception:
        _RECORDING["project"] = None
    config.add_log(f"[routines] recording started: '{sname}'")
    return (f"Recording routine '{sname}'. Perform the steps now — each tool "
            f"call is captured — then call routine_record_stop().")


def maybe_capture(tool: str, args=None) -> bool:
    """Record one executed call. No-op unless a recording is open.

    Called from the dispatch.execute_tool capture hook (live calls)
    and from approval.approve_token (approved replays, which the
    hook never sees). A call from another project than the one the
    recording started in is not captured. Returns True when the
    step was captured.
    """
    if not is_recording():
        return False
    if tool in _EXCLUDED_TOOLS:
        return False
    try:
        from aria import workspaces
        project = _RECORDING.get("project")
        if project is not None and workspaces.current() != project:
            return False
    except Exception:
        pass
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
        "project": _RECORDING.get("project"),
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
        _RECORDING["project"] = None
        return f"[Failed to save routine '{name}': {e}]"
    _RECORDING["name"] = None
    _RECORDING["steps"] = []
    _RECORDING["started_ts"] = None
    _RECORDING["project"] = None
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


def _preflight_steps(steps, known_tools):
    """Self-repair Part 4: pre-flight check for routine replay.

    Returns the sorted list of tool names in `steps` that are not in
    `known_tools`. Empty list means clear to replay. Never raises.
    """
    try:
        known = set(known_tools or [])
        return sorted({str(s.get("tool", "?")) for s in (steps or [])
                       if str(s.get("tool", "?")) not in known})
    except Exception:
        return []


def _wrapper_nested_tool(tool, args):
    """Nested gated tool behind a wrapper step, or None.

    browser_control, code_helper and dev_agent are in neither policy
    set, but their handlers re-enter the dispatcher for a gated
    effect (open_app_or_url / write_file) and ride the outer call's
    preauthorized scope (aria/tools/gaptools.py: _open_url_via_dispatch
    / _write_file_via_dispatch). Checking policy against the outer
    name alone therefore lets an untrusted routine replay launch or
    write with no approval, and lets a routine replay a launch past
    safe mode even though open_app_or_url is safe-mode-blocked.
    Only effect actions count: browser_control get_text and
    code_helper explain/fix have no nested effect. (ChatGPT x ARIA
    loop finding, 2026-10-30.)
    """
    try:
        action = str((args or {}).get("action") or "").strip().lower()
    except Exception:
        return None
    if tool == "browser_control":
        return "open_app_or_url" if action == "go_to" else None
    if tool == "code_helper":
        return "write_file" if action == "write" else None
    if tool == "dev_agent":
        return "write_file"
    return None


def run_routine_impl(name: str, params=None, start_at: int = 1) -> str:
    """Replay a routine's steps via dispatch.execute_tool.

    `start_at` (1-based) resumes a paused routine from a step:
    the approval auto-resume restarts at the step AFTER the one
    that just executed, while resume_routine() restarts AT the
    paused step (it never ran — denied, expired, or re-parked —
    so skipping it would silently drop it).

    {{param}} placeholders in string arg values are substituted from `params`.
    Every step replays with preauthorized=True EXCEPT destructive steps in an
    untrusted routine, which pass through the approval gate (Module 4).
    Three guards apply on top of that preauthorization, because a
    preauthorized replay otherwise skips the dispatcher's own checks:
    (1) safe mode is a hard block, not an approval — a step whose tool
    is safe-mode-blocked (the static set, or a discovered MCP tool by
    classification) is skipped while safe mode is on,
    even for trusted routines and even in auto mode (where
    needs_approval() is False and the step would otherwise replay
    preauthorized past the check); (2) a step that pauses for
    approval stops the routine — later steps do NOT run before the
    user has confirmed this one; (3) a wrapper step's nested effect
    is checked too — an untrusted routine's browser_control go_to /
    code_helper write / dev_agent save replays WITHOUT preauthorization
    so the nested launch/write pauses for approval like a direct
    call, and a nested safe-mode-blocked launch is skipped in safe
    mode even for trusted routines.
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
    try:
        start_at = int(start_at or 1)
    except (TypeError, ValueError):
        start_at = 1
    if start_at < 1:
        start_at = 1
    if start_at > len(steps):
        return (f"[Routine '{sname}' has {len(steps)} step(s) — "
                f"cannot resume at step {start_at}.]")
    # A fresh run supersedes any stale pause for this routine; a
    # resume keeps its own (it is cleared when the resume pauses
    # again or finishes — see the pause handling below).
    if start_at == 1:
        _clear_paused_for_name(sname)
    trusted = bool(data.get("trusted", False))

    from aria import approval as _approval_mod  # lazy: no import cycle
    from aria.tools import dispatch as _dispatch_mod  # lazy: no import cycle

    # Self-repair Part 4: pre-flight. Validate every step's tool exists before
    # replaying anything — fail fast with a clear message instead of dying
    # midway through a multi-step routine.
    try:
        known = set(_dispatch_mod.tool_names())
    except Exception:
        known = set()
    if known:
        missing = _preflight_steps(steps, known)
    else:
        missing = []
    if missing:
        config.add_log(f"[routines] pre-flight FAILED for '{sname}': unknown tools {missing}")
        return (f"[Routine '{sname}' aborted pre-flight: unknown tool(s): "
                f"{', '.join(missing)}. Fix the routine before replaying.]")

    lines = []
    paused_at = None
    failed_at = None
    for i, step in enumerate(steps, 1):
        if i < start_at:
            continue
        tool = step.get("tool", "?")
        args = _substitute(step.get("args", {}), params)
        # Safe mode is a hard block, not an approval: it applies to
        # replays too. The dispatcher skips its safe-mode check for
        # preauthorized calls, and routine steps replay preauthorized
        # (trusted always; untrusted in auto mode, where
        # needs_approval() is False) — so the check lives here, per
        # step, before dispatch. A blocked step is skipped, not run.
        try:
            _safe = config.is_safe_mode()
        except Exception:
            _safe = False
        if _safe and config.is_safe_mode_blocked(tool):
            lines.append(f"{i}. {tool} -> [Safe mode: '{tool}' is disabled. "
                         f"Disable safe mode to use shell, GUI control, "
                         f"or email.]")
            continue
        # Wrapper-effect steps (see _wrapper_nested_tool): the outer
        # name is ungated, but the nested effect is gated — so the
        # nested name gets the same safe-mode treatment, and an
        # untrusted replay must not carry preauthorization into it.
        nested = _wrapper_nested_tool(tool, args)
        if _safe and nested is not None and config.is_safe_mode_blocked(nested):
            lines.append(f"{i}. {tool} -> [Safe mode: '{nested}' is disabled. "
                         f"Disable safe mode to use shell, GUI control, "
                         f"or email.]")
            continue
        # Destructive steps in an untrusted routine go through approval.
        # A wrapper step counts as destructive for this purpose when
        # it carries a nested gated effect: without this, the nested
        # launch/write rides the outer call's preauthorized scope
        # past both gates (the outer name is in neither policy set).
        preauthorized = True
        try:
            _needs = _approval_mod.needs_approval(tool, args)
        except Exception:
            _needs = False
        if not trusted and (_needs or nested is not None):
            preauthorized = False
        try:
            result, _ok = _dispatch_mod.execute_tool(tool, args,
                                                     preauthorized=preauthorized)
        except Exception as e:
            result = f"[Tool Error: {e}]"
        preview = config.redact(str(result)).replace("\n", " ")[:200]
        lines.append(f"{i}. {tool} -> {preview}")
        # A step that paused for approval stops the routine: later
        # steps must not run before the user has confirmed this one.
        if str(result).startswith("[AWAITING_APPROVAL"):
            paused_at = i
            lines.append(f"Routine paused at step {i}: awaiting user "
                         f"approval — later steps not run.")
            _record_pause(sname, i, result, params, data)
            break
        # Pass 25: an explicitly failed step stops the routine too.
        # The old loop only stopped for approval, so a failure was
        # logged and the next step ran on top of it — a routine
        # whose first step failed still executed every later step.
        # (Steps are never marked optional, so every failure stops.)
        from aria.outcome import FAILED, normalize
        if normalize(result).status == FAILED:
            lines.append(f"Routine stopped at step {i}: '{tool}' "
                         f"failed — later steps not run.")
            failed_at = i
            break
    config.add_log(f"[routines] ran '{sname}' ({len(steps)} steps, trusted={trusted})")
    if paused_at is not None:
        return (f"Routine '{sname}' PAUSED at step {paused_at} awaiting "
                f"approval (trusted={'yes' if trusted else 'no'}): "
                f"approving the pending step resumes the routine "
                f"automatically; resume_routine('{sname}') retries the "
                f"paused step later.\n"
                + "\n".join(lines))
    _clear_paused_for_name(sname)
    if failed_at is not None:
        return (f"Routine '{sname}' STOPPED at step {failed_at} "
                f"(step failed, trusted={'yes' if trusted else 'no'}):\n"
                + "\n".join(lines))
    resumed = f"resumed from step {start_at}, " if start_at > 1 else ""
    return (f"Routine '{sname}' replayed ({resumed}{len(steps)} steps, "
            f"trusted={'yes' if trusted else 'no'}):\n" + "\n".join(lines))


# ── Paused routines (pass 25, P2#7) ─────────────────────────────
# A routine that pauses for approval used to leave nothing behind
# but a string: the remaining steps never ran unless the user
# re-ran the whole routine (re-executing the steps that already
# ran). The pause is now a persisted record keyed by the approval
# token the step is waiting on, so approving the step resumes the
# routine from the next step, and resume_routine() retries the
# paused step later.

_PAUSED: Dict[str, Dict[str, Any]] = {}
_paused_loaded = False


def _paused_file() -> str:
    return data_dir.path("routine_paused.json")


def _load_paused() -> None:
    global _paused_loaded
    if _paused_loaded:
        return
    _paused_loaded = True
    try:
        with open(_paused_file(), encoding="utf-8") as f:
            raw = json.load(f) or {}
        entries = raw.get("paused") or {}
        if isinstance(entries, dict):
            _PAUSED.update({str(k): v for k, v in entries.items()
                            if isinstance(v, dict)})
    except Exception:
        pass


def _save_paused() -> None:
    try:
        data_dir.write_json_atomic(
            _paused_file(), {"version": 1, "paused": _PAUSED})
    except Exception:
        pass


def _record_pause(sname, paused_at, result, params, data) -> None:
    """Persist the pause for the token the step is waiting on.
    No token in the string (a handler-side re-park already
    returned a plain message) means no record — there is nothing
    to resume from."""
    m = re.search(r"token=([0-9a-f]{8})", str(result))
    if not m:
        return
    _load_paused()
    try:
        saved_params = json.loads(json.dumps(params or {}, default=str))
    except Exception:
        saved_params = {}
    try:
        from aria import workspaces
        project = workspaces.current()
    except Exception:
        project = None
    # The policy the pausing run had (pass 26 B1#2): a resume
    # that reopens a bare execution drops the mission's cap and
    # envelope, so the remaining steps run unenveloped/uncapped.
    # Capture it here, from the pausing execution itself, and
    # JSON-round-trip it — the record is persisted to disk, so
    # anything unserializable is stringified, never allowed to
    # break the save.
    resume_context = None
    try:
        from aria import runtime as _runtime_mod
        import copy as _copy_mod
        _ctx = _runtime_mod.current()
        _env = _ctx.envelope
        try:
            _env_copy = _copy_mod.deepcopy(_env) \
                if _env is not None else None
        except Exception:
            _env_copy = dict(_env) if isinstance(_env, dict) else None
        resume_context = {
            "project": _ctx.project,
            "budget_scope": _ctx.budget_scope,
            "spend_cap": _ctx.spend_cap,
            "envelope": _env_copy,
        }
        resume_context = json.loads(
            json.dumps(resume_context, default=str))
    except Exception:
        resume_context = None
    _PAUSED[m.group(1)] = {
        "token": m.group(1),
        "name": sname,
        "paused_at": paused_at,
        "next_step": paused_at + 1,
        "params": saved_params,
        "revision": _routine_revision(data),
        "project": project,
        "resume_context": resume_context,
        "paused_ts": time.time(),
    }
    _save_paused()


def _clear_paused_for_name(sname) -> None:
    _load_paused()
    doomed = [t for t, r in _PAUSED.items() if r.get("name") == sname]
    if doomed:
        for t in doomed:
            _PAUSED.pop(t, None)
        _save_paused()


def clear_paused_for_token(token: str) -> None:
    _load_paused()
    if _PAUSED.pop(str(token or ""), None) is not None:
        _save_paused()


def repark_pause(old_token: str, new_token: str) -> None:
    """The approved step parked AGAIN behind a fresh approval
    (e.g. a handler-side gate): the pause follows the new token,
    still waiting on the same step."""
    _load_paused()
    record = _PAUSED.pop(str(old_token or ""), None)
    if record is None:
        return
    record["token"] = str(new_token or "")
    _PAUSED[str(new_token or "")] = record
    _save_paused()


def _check_resume(record) -> str:
    """The routine must still exist and be the revision that
    paused — a re-recorded or edited routine is a different
    routine, and resuming it would run steps nobody paused on.
    Returns an error string, or '' when the resume may proceed."""
    data, err = _load_routine(record.get("name", ""))
    if err or not data:
        return (f"[Routine '{record.get('name', '')}' no longer exists — "
                f"the paused run cannot resume.]")
    if _routine_revision(data) != record.get("revision"):
        return (f"[Routine '{record.get('name', '')}' changed since it "
                f"paused — re-run it from the start instead of resuming.]")
    return ""


def _resume_execution(record):
    """Open the execution a resumed routine runs under.

    Records written since pass 26 carry resume_context — the
    pausing execution's project, budget_scope, spend_cap, and
    envelope — and the resume reopens under exactly those values,
    so the remaining steps observe the same cap and envelope the
    routine paused under (pass 26 B1#2). Records that predate the
    field resume exactly as before (project only): explicit
    values always win over the runtime.execution() inheritance
    from the approving execution, and a missing field falls back
    to that inheritance rather than failing the resume."""
    from aria import runtime
    ctx = record.get("resume_context") or {}
    if not ctx:
        return runtime.execution("routine-resume",
                                 project=record.get("project"))
    return runtime.execution(
        "routine-resume",
        project=ctx.get("project") or record.get("project"),
        budget_scope=ctx.get("budget_scope") or None,
        spend_cap=ctx.get("spend_cap"),
        envelope=ctx.get("envelope"))


def maybe_resume_for_token(token: str) -> str:
    """Resume the routine paused on this token, from the step AFTER
    the one that just executed. Called by approve_token's executed
    branch — the call site is the proof the step ran. Returns ''
    when no routine is paused on the token."""
    _load_paused()
    record = _PAUSED.get(str(token or ""))
    if record is None:
        return ""
    problem = _check_resume(record)
    _PAUSED.pop(str(token or ""), None)
    _save_paused()
    if problem:
        config.add_log(f"[routines] resume refused: {problem}")
        return problem
    try:
        with _resume_execution(record):
            return run_routine_impl(
                record["name"], record.get("params") or {},
                start_at=record.get("next_step", 1))
    except Exception as e:
        return f"[Routine resume failed: {e}]"


def resume_routine(name=None) -> str:
    """Resume a paused routine from its paused step (the step never
    ran — denied, expired, or re-parked — so skipping it would
    silently drop it). Refuses while the step's approval is still
    pending: approve or deny that first."""
    _load_paused()
    record = None
    if name:
        sname = sanitize_name(name)
        for r in _PAUSED.values():
            if r.get("name") == sname:
                record = r
                break
        if record is None:
            return f"[No paused run of routine '{sname}'.]"
    else:
        if len(_PAUSED) == 1:
            record = next(iter(_PAUSED.values()))
        elif not _PAUSED:
            return "[No routine is paused.]"
        else:
            names = sorted({r.get("name", "?") for r in _PAUSED.values()})
            return (f"[Several routines are paused ({', '.join(names)}) — "
                    f"pass the routine name to resume_routine().]")
    try:
        from aria import approval as _approval_mod
        pending = {e.get("token") for e in
                   _approval_mod.pending_entries()}
    except Exception:
        pending = set()
    if record.get("token") in pending:
        return (f"[Routine '{record.get('name')}' is paused at step "
                f"{record.get('paused_at')} awaiting approval — approve "
                f"or deny that step first; approving resumes it.]")
    problem = _check_resume(record)
    _PAUSED.pop(record.get("token", ""), None)
    _save_paused()
    if problem:
        return problem
    try:
        with _resume_execution(record):
            return run_routine_impl(
                record["name"], record.get("params") or {},
                start_at=record.get("paused_at", 1))
    except Exception as e:
        return f"[Routine resume failed: {e}]"


def _routine_revision(data) -> str:
    """Content hash of what the routine DOES (name + steps).

    The trust decision is about these steps; a re-recorded or
    edited routine with the same name is a different routine.
    """
    payload = json.dumps({"name": data.get("name"),
                          "steps": data.get("steps", [])},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def freeze_trust_args(args) -> dict:
    """Bind a trust_routine approval to the routine revision as it
    stands NOW (pass 25, the skill-activation freeze precedent):
    the dispatcher calls this before stashing the pending approval,
    so an approve-token replay can only trust that exact revision.
    A routine edited (or re-recorded) between park and tap cannot
    ride the original token. Freezing only rewrites the stashed
    args; no file is created or changed."""
    frozen = dict(args or {})
    sname = sanitize_name(frozen.get("name", ""))
    if not sname:
        return frozen
    data, err = _load_routine(sname)
    if err or not data:
        # Fail closed (pass 26 B1#3): a trust request for a routine
        # that does not exist must be refused, not parked
        # revisionless — otherwise "request trust, create the
        # routine later, confirm" trusts whatever exists at tap
        # time. Callers that park catch this and refuse to park.
        raise ValueError(
            f"Routine '{sname}' does not exist — trust was not "
            f"requested. Create the routine first, then ask again.")
    frozen["revision"] = _routine_revision(data)
    return frozen


def _save_trusted(sname: str, trusted: bool, revision=None) -> str:
    data, err = _load_routine(sname)
    if err:
        return err
    if revision is not None and _routine_revision(data) != revision:
        return (f"[Routine '{sname}' changed since the trust approval "
                f"was requested — re-review it before trusting. "
                f"Trust was NOT granted.]")
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
# routine_record_stop, run_routine, resume_routine, list_routines,
# delete_routine, describe_routine, trust_routine, untrust_routine.


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


def tool_resume_routine(name: str = "") -> str:
    return resume_routine(name or None)


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
    _clear_paused_for_name(sname)
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


def tool_trust_routine(name: str, revision=None) -> str:
    sname = sanitize_name(name)
    if not sname:
        return "[Routine name has no valid characters.]"
    return _save_trusted(sname, True, revision=revision)


def tool_untrust_routine(name: str) -> str:
    sname = sanitize_name(name)
    if not sname:
        return "[Routine name has no valid characters.]"
    return _save_trusted(sname, False)


# ---------------------------------------------------------------- routine packs
# A pack is a zip: manifest.json + one <name>.json per routine. Shareable:
# export a pack, send the file, import it on another machine.
PACK_FORMAT = "aria-routine-pack"
PACK_VERSION = 1


def _pack_manifest(names):
    return {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "exported": datetime.now(timezone.utc).isoformat(),
        "routines": sorted(names),
    }


def _as_bool(value, default=False) -> bool:
    """Lenient bool for model-supplied args (True/False or 'true'/'false')."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y", "on"):
        return True
    if s in ("false", "0", "no", "n", "off"):
        return False
    return default


def default_export_path() -> str:
    """The timestamped default pack path (install root), resolved now."""
    return os.path.join(
        data_dir.root(),
        f"routines-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.zip")


def freeze_export_args(args) -> dict:
    """Resolve an export call's destination ONCE, for the approval gate.

    The dispatcher calls this before stashing the pending approval,
    so the description names the exact file and the approve-token
    replay executes exactly that file: an empty dest_path is replaced
    with the default path computed here (not re-derived at replay
    time, when the timestamp would pick a different name), and a
    relative path is made absolute against the process cwd. The
    overwrite flag is normalised to a real bool at the same time.
    No file or directory is created here — freezing only rewrites
    the stashed args.
    """
    frozen = dict(args or {})
    dest = str(frozen.get("dest_path", "") or "").strip()
    frozen["dest_path"] = os.path.abspath(dest) if dest else default_export_path()
    frozen["overwrite"] = _as_bool(frozen.get("overwrite"), default=False)
    return frozen


def _write_pack_zip(path, wanted, mode) -> None:
    import zipfile
    with zipfile.ZipFile(path, mode, zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json",
                   json.dumps(_pack_manifest([s for s, _ in wanted]), indent=2))
        for sname, p in wanted:
            z.write(p, f"{sname}.json")


def export_pack(names, dest_path: str, overwrite: bool = False) -> str:
    """Zip the named routines + manifest to dest_path. Returns dest_path.

    Default is exclusive creation: if the destination already exists
    the export is REFUSED and the existing bytes are untouched.
    Pass overwrite=True to replace it — the replacement is built in
    a temp file in the same directory first and moved into place
    with os.replace, so a failed build never leaves a truncated
    destination behind. Nothing is created when no routine matches.
    """
    wanted = []
    for n in names or []:
        sname = sanitize_name(n)
        if not sname:
            continue
        p = _routine_path(sname)
        if os.path.exists(p):
            wanted.append((sname, p))
    if not wanted:
        return "[No matching routines to export.]"
    dest = os.path.abspath(dest_path)
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    if not overwrite:
        try:
            _write_pack_zip(dest, wanted, "x")
        except FileExistsError:
            return (f"[Export refused: destination already exists: {dest} "
                    f"— pass overwrite=true to replace it.]")
        return f"Exported {len(wanted)} routine(s) to {dest}"
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix=".routines-pack-", suffix=".tmp",
                               dir=parent)
    try:
        os.close(fd)
        _write_pack_zip(tmp, wanted, "w")
        os.replace(tmp, dest)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return f"Exported {len(wanted)} routine(s) to {dest}"


def _valid_routine_data(data) -> bool:
    return (isinstance(data, dict)
            and isinstance(data.get("steps"), list)
            and bool(sanitize_name(data.get("name"))))


def import_pack(pack_path: str) -> str:
    """Validate and import a routine pack. Never overwrites existing routines."""
    import zipfile
    if not os.path.exists(pack_path):
        return f"[Pack not found: {pack_path}]"
    try:
        z = zipfile.ZipFile(pack_path)
    except Exception as e:
        return f"[Not a valid pack file: {e}]"
    try:
        try:
            manifest = json.loads(z.read("manifest.json"))
        except KeyError:
            return "[Pack has no manifest.json — refusing to import.]"
        if manifest.get("format") != PACK_FORMAT:
            return "[Not an ARIA routine pack — refusing to import.]"
        imported, skipped, bad = [], [], []
        os.makedirs(_routines_dir(), exist_ok=True)
        for info in z.infolist():
            if info.is_dir() or info.filename == "manifest.json":
                continue
            if not info.filename.endswith(".json") or "/" in info.filename:
                bad.append(info.filename)
                continue
            sname = sanitize_name(info.filename[:-5])
            if not sname:
                bad.append(info.filename)
                continue
            try:
                data = json.loads(z.read(info.filename))
            except Exception:
                bad.append(info.filename)
                continue
            if not _valid_routine_data(data):
                bad.append(info.filename)
                continue
            dest = _routine_path(sname)
            if os.path.exists(dest):
                skipped.append(sname)
                continue
            data["name"] = sname
            # Imported routines are untrusted until explicitly trusted.
            data["trusted"] = False
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            imported.append(sname)
    finally:
        z.close()
    parts = []
    if imported:
        parts.append(f"imported: {', '.join(imported)}")
    if skipped:
        parts.append(f"skipped (already exist): {', '.join(skipped)}")
    if bad:
        parts.append(f"rejected: {', '.join(bad)}")
    return "; ".join(parts) or "[Pack contained no routines.]"


def tool_export_routine_pack(names_csv: str = "", dest_path: str = "",
                             overwrite=False) -> str:
    """Export routines to a shareable pack zip. names_csv empty = all.

    overwrite defaults to False: an existing destination is refused,
    never silently truncated (pass 13 export fix). Under confirm
    modes the dispatcher freezes the resolved destination into the
    pending approval before this runs, so an approved replay writes
    exactly the file the user saw named.
    """
    if names_csv.strip():
        names = [n.strip() for n in names_csv.split(",") if n.strip()]
    else:
        try:
            names = [f[:-5] for f in sorted(os.listdir(_routines_dir()))
                     if f.endswith(".json")]
        except Exception:
            names = []
    if not dest_path.strip():
        dest_path = default_export_path()
    return export_pack(names, dest_path.strip(),
                       overwrite=_as_bool(overwrite, default=False))


def tool_import_routine_pack(pack_path: str) -> str:
    """Import a routine pack zip. Never overwrites; imports are untrusted."""
    return import_pack((pack_path or "").strip())
