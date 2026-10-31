"""
ARIA Tool Execution Dispatcher and Progressive Toolkit Management.
Coordinates tool invocation, argument repair, duplicate call suppression,
risky tool audit logging, HUD feedback transitions, and progressive schema payloads.
"""

import contextlib
import time
import re
from typing import Dict, List, Optional, Set, Tuple, Callable, Any

from aria.config import MAX_TOOL_OUTPUT, BRIDGE_TOKEN, GITHUB_USERNAME, redact
from aria.memory import spine_append as default_spine_append, add_log as default_add_log
from aria.tools.schemas import (
    TOOLKITS, get_toolkit_declarations, get_toolkits_prompt_block
)
from aria.tools.sandbox import (
    RISKY_TOOLS, call_signature, missing_required_args,
    stale_target_check, risky_description, truncate_output,
    tool_run_python
)
from aria.tools.skills import tool_run_skill
import aria.tools.builtins as builtins
import threading as _threading

_SCOPE = _threading.local()


def in_preauthorized_scope() -> bool:
    """True while this thread executes a tool under
    preauthorized=True (routine replay, approve-token replay,
    delegated task). Tools whose effects cannot be pre-described
    — phone control — refuse in this scope."""
    return bool(getattr(_SCOPE, "preauthorized", False))


def in_delegated_scope() -> bool:
    """True while this thread runs a tool for a delegated background
    task (aria/agent/taskexec.py wraps its runner in
    delegated_scope()). Host-lifecycle tools — restart_program,
    run_shell_command — refuse here: a background worker must not
    restart or shell out of the process it lives inside."""
    return bool(getattr(_SCOPE, "delegated", False))


@contextlib.contextmanager
def delegated_scope():
    """Mark tool execution on this thread as delegated-task work
    for the duration of the block (see in_delegated_scope)."""
    prev = getattr(_SCOPE, "delegated", False)
    _SCOPE.delegated = True
    try:
        yield
    finally:
        _SCOPE.delegated = prev

# Progressive Toolkit State
_LOADED_TOOLKITS: Set[str] = {"core"}

# Turn Context State
_TURN_CALLS: Dict[str, str] = {}
_LAST_USER_MESSAGE: str = ""
_LAST_OPEN_TARGET: str = ""
_TURN_NUDGED: bool = False
_LAST_TOOL_EXECUTED: Tuple[Optional[str], float] = (None, 0.0)

# Self-Repair State (Part 2 of the self-repair upgrade)
# Consecutive failures per tool-call signature within the current turn.
_TURN_FAILURES: Dict[str, int] = {}
_REPAIR_BUDGET = 2  # repair attempts per failing call before she must stop and report

# Pluggable Subsystem Hooks
_SPINE_HOOK: Callable[[str, dict], None] = default_spine_append
_HUD_HOOK: Optional[Callable[[str], Any]] = None
_LOG_HOOK: Callable[[str], None] = default_add_log
_HISTORY_HOOK: Optional[Callable[[dict], None]] = None

# Registry of Tools
_REGISTRY: Dict[str, Callable[[dict], str]] = {}


def set_spine_hook(fn: Callable[[str, dict], None]) -> None:
    global _SPINE_HOOK
    _SPINE_HOOK = fn


def set_hud_hook(fn: Callable[[str], Any]) -> None:
    global _HUD_HOOK
    _HUD_HOOK = fn


def set_log_hook(fn: Callable[[str], None]) -> None:
    global _LOG_HOOK
    _LOG_HOOK = fn


def set_history_hook(fn: Callable[[dict], None]) -> None:
    global _HISTORY_HOOK
    _HISTORY_HOOK = fn


def register_tool(name: str, handler: Callable[[dict], str]) -> None:
    """Register or override a tool execution handler."""
    _REGISTRY[name] = handler


def get_registered_tools() -> Dict[str, Callable[[dict], str]]:
    """Return dictionary of all registered tool handlers."""
    return dict(_REGISTRY)


# --- Progressive Toolkit Methods ---
def tool_load_toolkit(name: str) -> str:
    """Progressively unlock specialist toolkit."""
    tk_name = (name or "").strip().lower()
    if tk_name in TOOLKITS:
        if tk_name in _LOADED_TOOLKITS:
            return f"Toolkit '{tk_name}' is already loaded."
        _LOADED_TOOLKITS.add(tk_name)
        return (f"Toolkit '{tk_name}' loaded. You can now call: "
                f"{', '.join(TOOLKITS[tk_name]['tools'])}.")
    return (f"Unknown toolkit '{tk_name}'. Available: "
            f"{', '.join(sorted(TOOLKITS))}.")


def reset_toolkits() -> None:
    """Reset progressive toolkits back to default {'core'}."""
    global _LOADED_TOOLKITS
    _LOADED_TOOLKITS = {"core"}


# Keyword map for dynamic zero-turn toolkit auto-resolution
KEYWORD_TOOLKIT_MAP: Dict[str, List[str]] = {
    "spotify": [r"\bspotify\b", r"\bmusic\b", r"\bsongs?\b", r"\bplaylists?\b", r"\btracks?\b", r"\bdj\b", r"\bplay\b", r"\bpause\b", r"\bshuffle\b", r"\bmedia\b"],
    "windows": [r"\bwindows?\b", r"\bminimize\b", r"\bmaximize\b", r"\bfocus\b", r"\bclose\s+window\b", r"\bswitch\s+to\b"],
    "scheduler": [r"\breminds?\b", r"\breminders?\b", r"\btimers?\b", r"\bschedules?\b", r"\balarms?\b", r"\brecurring\b", r"\bnudge\b"],
    "memory_plus": [r"\bnotes?\b", r"\bjournal\b", r"\bdiary\b", r"\bbriefing\b", r"\bforget\b", r"\bremember\b"],
    "mtg": [r"\bmtg\b", r"\bmagic\b", r"\bcommander\b", r"\bedh\b", r"\bscryfall\b", r"\bcards?\b", r"\bdecks?\b", r"\bmana\b"],
    "vision": [r"\bphotos?\b", r"\bpictures?\b", r"\bcameras?\b", r"\bwebcam\b", r"\bservos?\b", r"\bhead\b", r"\bneck\b", r"\btracking\b"],
    "comms": [r"\bemai(?:ls?)\b", r"\bgmail\b", r"\bmail\b", r"\binbox\b"],
    "admin": [r"\bvolume\b", r"\bmute\b", r"\bunmute\b", r"\bbridge\b", r"\btokens?\b", r"\bkeys?\b", r"\bshell\b", r"\bterminal\b", r"\bcommand\s+line\b", r"\bbackup\b", r"\bmirror\b", r"\bsteam\b", r"\bgame\s+updates?\b"],
    "github": [r"\bgithub\b", r"\bgit\b", r"\brepos?(?:itory)?\b", r"\bcommit\b", r"\bpush\b"],
    "autonomy": [r"\bcode\s+helper\b", r"\bdev\s+agent\b", r"\bupdate\s+status\b", r"\bgoals?\b", r"\bautonom\w*", r"\bbackground\s*(?:task|job)?\b", r"\bworkers?\b", r"\bself[- ]heal\w*", r"\bdaemon\b", r"\bheartbeat\b", r"\bincidents?\b", r"\bhealth\s*(?:audit|check)?\b", r"\bdelegate\w*\b", r"\btask\s+(?:status|cancel)\b"],
    "routines": [r"\broutine\b", r"\bautomate\b", r"\bevery time i\b"],
    "files": [r"\bduplicates?\b", r"\bdisk\s*(?:usage|space)\b", r"\borganiz\w+\s+(?:my\s+)?(?:downl|docum|pict|phot|file|fold)"],
    "monitor": [r"\bscreen\s*watch\b", r"\bwatch\b.{0,20}\bscreen\b"],
    "core": [r"\bflights?\b", r"\bbrowser\b", r"\bweb\s+page\b"],
    "phone": [r"\bphone\b", r"\bnotify\b", r"\bnotifications?\b", r"\bphone\s+notifications?\b"],
    "user": [r"\bcustom\s*tools?\b"],
}

TOOL_TO_TOOLKIT: Dict[str, str] = {
    t_name: tk_name
    for tk_name, tk_data in TOOLKITS.items()
    for t_name in tk_data.get("tools", [])
}


def auto_resolve_toolkits(prompt: str) -> List[str]:
    """Scan user prompt against toolkit keywords and auto-load matched specialist toolkits."""
    if not prompt:
        return []
    p_low = prompt.lower()
    newly_loaded: List[str] = []
    for tk, patterns in KEYWORD_TOOLKIT_MAP.items():
        if tk in TOOLKITS and tk not in _LOADED_TOOLKITS:
            if any(re.search(pat, p_low) for pat in patterns):
                _LOADED_TOOLKITS.add(tk)
                newly_loaded.append(tk)
    return newly_loaded


def get_loaded_toolkits() -> Set[str]:
    """Return set of currently loaded toolkit names."""
    return set(_LOADED_TOOLKITS)


def get_active_declarations() -> List[Dict[str, Any]]:
    """Return API function declarations for currently loaded toolkits."""
    return get_toolkit_declarations(_LOADED_TOOLKITS)


def get_toolkits_prompt() -> str:
    """Return prompt text explaining toolkit availability."""
    return get_toolkits_prompt_block(TOOLKITS)


# --- Turn Context Management ---
def reset_turn_state(user_message: str = "") -> None:
    """Reset per-turn state caches."""
    global _TURN_CALLS, _TURN_NUDGED, _LAST_USER_MESSAGE
    _TURN_CALLS.clear()
    _TURN_FAILURES.clear()
    _TURN_NUDGED = False
    _LAST_USER_MESSAGE = user_message


def set_turn_context(user_message: str = "", last_open_target: str = "", turn_nudged: bool = False) -> None:
    """Set turn context variables for guard evaluations."""
    global _LAST_USER_MESSAGE, _LAST_OPEN_TARGET, _TURN_NUDGED
    if user_message:
        _LAST_USER_MESSAGE = user_message
    if last_open_target:
        _LAST_OPEN_TARGET = last_open_target
    _TURN_NUDGED = turn_nudged


def get_last_tool_executed() -> Tuple[Optional[str], float]:
    """Return (fn_name, timestamp) of last executed tool."""
    return _LAST_TOOL_EXECUTED


# --- Core Dispatch Execution ---


def _closest_tool(name: str) -> Optional[str]:
    """Fuzzy-match a hallucinated/typo'd tool name to the closest real tool.
    SUGGESTION ONLY — the match is returned to the caller as a hint and is
    never executed by the dispatcher (executing it would bypass argument
    validation, safe mode, and approval, which all check the requested
    name). Returns None when nothing is close enough."""
    try:
        import difflib
        cands = difflib.get_close_matches(name or "", list(_REGISTRY.keys()),
                                          n=1, cutoff=0.6)
        return cands[0] if cands else None
    except Exception:
        return None


def tool_names() -> List[str]:
    """All registered tool names (used by routine pre-flight checks)."""
    try:
        return sorted(_REGISTRY.keys())
    except Exception:
        return []


def _repair_or_exhaust(fn_name: str, args: dict, err_str: str) -> str:
    """Structured repair loop (Self-repair Part 2).

    Tracks consecutive failures per call signature within the turn. While the
    budget lasts, returns the error WITH a diagnosis and a repair instruction
    for the model. When exhausted, tells the model to stop retrying and
    report to the user. Never raises.
    """
    try:
        import json
        sig = f"{fn_name}|{json.dumps(args or {}, sort_keys=True, default=str)}"
    except Exception:
        sig = f"{fn_name}|?"
    n = _TURN_FAILURES.get(sig, 0) + 1
    _TURN_FAILURES[sig] = n
    err_short = err_str if len(err_str) <= 600 else err_str[:600] + "…[truncated]"
    try:
        from aria.agent.self_healing import diagnose_error
        diag = diagnose_error(fn_name, err_str, args or {})
        diagnosis = diag.get("diagnosis", "")
        suggestion = diag.get("recommended_action", "")
    except Exception:
        diagnosis, suggestion = "", ""
    # Merger Phase 1 (blueprint SS3.2): every tool failure also lands
    # in the classified failure record (event ring + incident DB) —
    # before this, a failure existed only inside this turn's prompt.
    # The record's user_message is the plain-language line Phase 1
    # composed FOR the user; keep it — the exhausted branch below
    # hands it to the model verbatim, because this return value is
    # the only route that message has into the brain loop's reply
    # (and from there to speech). Dropping it here left the user
    # with whatever the model improvised, or nothing.
    user_message = ""
    try:
        from aria.agent.failures import surface_failure
        rec = surface_failure(f"tool:{fn_name}", err_str, attempts=n,
                              max_attempts=_REPAIR_BUDGET + 1)
        if rec:
            user_message = rec.get("user_message") or ""
    except Exception:
        pass
    if n <= _REPAIR_BUDGET:
        parts = [f"[Tool Error: {err_short}]"]
        if diagnosis:
            parts.append(f"Diagnosis: {diagnosis}.")
        if suggestion:
            parts.append(f"Suggested fix: {suggestion}.")
        parts.append(
            f"[Repair attempt {n}/{_REPAIR_BUDGET} for '{fn_name}': "
            f"diagnose the root cause and fix your approach, or say you "
            f"cannot complete it. Do not repeat the identical call.]")
        return "\n".join(parts)
    report = (f"report the failure to the user: {user_message}"
              if user_message
              else "report the failure to the user plainly.")
    return (f"[Tool Error: {err_short}]\n"
            f"[Repair budget exhausted for '{fn_name}' after {_REPAIR_BUDGET} "
            f"attempts. Stop retrying this call and {report}]")


def execute_tool(fn_name: str, args: dict, preauthorized: bool = False) -> Tuple[str, bool]:
    """Dispatch one tool with full validation, sandboxing, and audit logging.
    Returns (result_text, False) — confirmation gating was removed; the bool is
    kept for call-site compatibility.
    """
    global _LAST_OPEN_TARGET, _TURN_NUDGED, _LAST_TOOL_EXECUTED
    
    # Auto-resolve parent toolkit if called directly
    if fn_name in TOOL_TO_TOOLKIT:
        parent_tk = TOOL_TO_TOOLKIT[fn_name]
        if parent_tk not in _LOADED_TOOLKITS:
            _LOADED_TOOLKITS.add(parent_tk)

    # 1. Log to spine
    if _SPINE_HOOK:
        try:
            _SPINE_HOOK("tool", {"name": fn_name, "args": str(args)[:300]})
        except Exception as _e_silent:
            _config_mod.log_silent("execute_tool", _e_silent)

    # 2. Duplicate-call blocking. One exemption: an approve-token
    # replay of exactly this call is not a duplicate — it is the
    # held call's resolution. A handler-side gate (run_shell_
    # command, browser_agent) completes this dispatch on its
    # first pass and caches its AWAITING result under this very
    # signature whenever the model's args already match the
    # stashed ones, so without the exemption the replay dies
    # here: the approval entry is popped, the verdict is
    # discarded by the click path, and nothing executes —
    # Alek's dead Confirm, 2026-10-31 ("I confirm, and nothing
    # happens"). The exemption is scoped to the replay marker
    # approval.approve_token sets for exactly this (tool, args)
    # on this thread; a plain preauthorized call (routine replay,
    # skill step) carries no marker and stays duplicate-guarded.
    sig = call_signature(fn_name, args)
    _is_approve_replay = False
    if preauthorized:
        try:
            from aria import approval as _approval_mod
            _is_approve_replay = bool(
                _approval_mod.in_approve_replay(fn_name, args or {}))
        except Exception:
            _is_approve_replay = False
    if sig in _TURN_CALLS and not _is_approve_replay:
        return (
            f"[Duplicate call blocked: {fn_name} already ran with these "
            f"arguments this turn. Earlier result: {_TURN_CALLS[sig][:600]}]",
            False
        )

    # 3. Argument validation & repair
    missing = missing_required_args(fn_name, args)
    if missing:
        return (
            f"[Argument error: {fn_name} requires {', '.join(missing)}. "
            f"Call it again with {'them' if len(missing) > 1 else 'it'} included.]",
            False
        )

    # 3b. ARIA ULTIMATE (Module 4) — approval gating. Destructive tools pause
    # for user approval unless preauthorized (approve-token replay, trusted
    # routine replay) or the mode is auto. Lazy import: no import cycle.
    # 3a. Safe mode: shell, GUI control, and email are hard-blocked.
    if not preauthorized:
        from aria import config as _config_mod
        if _config_mod.is_safe_mode() and _config_mod.is_safe_mode_blocked(fn_name):
            return (f"[Safe mode: '{fn_name}' is disabled. "
                    f"Disable safe mode to use shell, GUI control, or email.]",
                    False)
    if not preauthorized:
        from aria import approval as _approval_mod
        _approval_mod.purge_expired()
        if _approval_mod.needs_approval(fn_name, args or {}):
            return _approval_mod.request_approval(fn_name, args or {}), False

    # 4. Risky tools safety & audit
    if fn_name in RISKY_TOOLS:
        nudge, _TURN_NUDGED = stale_target_check(
            fn_name, args, _LAST_OPEN_TARGET, _LAST_USER_MESSAGE, _TURN_NUDGED
        )
        if nudge:
            return nudge, False
        if fn_name == "open_app_or_url":
            _LAST_OPEN_TARGET = (args or {}).get("target", "")

        # Record action in model history hook (audit trail)
        if _HISTORY_HOOK:
            try:
                desc = risky_description(fn_name, args, GITHUB_USERNAME)
                _HISTORY_HOOK({
                    "role": "model",
                    "parts": [{"text": "[Executed: " + redact(desc) + "]"}]
                })
            except Exception as _e_silent:
                _config_mod.log_silent("execute_tool", _e_silent)

    # 5. Visual HUD feedback
    prev_face = None
    if _HUD_HOOK:
        try:
            prev_face = _HUD_HOOK("coding")
        except Exception as _e_silent:
            _config_mod.log_silent("execute_tool", _e_silent)
    _LAST_TOOL_EXECUTED = (fn_name, time.time())

    # 6. Execute Handler
    # Fleet registry (aria/fleet.py, the Mark-LVI port): the call
    # is in flight from here to the return, so the Tasks tab's
    # Fleet section can show what she is ACTUALLY doing — the
    # funnel is the only place that knows.
    _fleet_token = None
    try:
        from aria import fleet as _fleet_mod
        _fleet_token = _fleet_mod.begin(
            fn_name, _fleet_mod.detail_for(fn_name, args or {}))
    except Exception:
        _fleet_token = None
    _SCOPE.preauthorized = bool(preauthorized)
    try:
        handler = _REGISTRY.get(fn_name)
        if handler:
            r = handler(args or {})
        else:
            # Unknown tool name: NEVER execute a fuzzy match directly.
            # Argument validation, safe mode, and approval above all ran
            # against the REQUESTED name, so running the closest real tool
            # here would bypass every one of those checks (a typo'd
            # destructive tool is not in DESTRUCTIVE_TOOLS, not in
            # SAFE_MODE_BLOCKED_TOOLS, and has no schema to validate
            # against). _closest_tool is suggestion-only: the model must
            # re-issue the corrected call through execute_tool so it gets
            # the full validation/policy path. (Security fix, 2026-10-27.)
            match = _closest_tool(fn_name)
            try:
                from aria.agent.failures import surface_failure
                surface_failure(f"tool:{fn_name}",
                                f"Unknown tool: {fn_name}",
                                unknown_tool=True)
            except Exception:
                pass
            if match:
                _LOG_HOOK(f"Unknown tool '{fn_name}' rejected; "
                          f"suggested '{match}' (not executed)")
                r = (f"Unknown tool: {fn_name}. Did you mean '{match}'? "
                     f"Call '{match}' directly if that is the tool you "
                     f"want — it was NOT run.")
            else:
                r = f"Unknown tool: {fn_name}"
    except Exception as e:
        try:
            from aria.agent.self_healing import attempt_auto_heal
            healed, healed_res, heal_msg = attempt_auto_heal(fn_name, args or {}, e, handler)
            if healed:
                r = f"{healed_res}\n[Autonomous Self-Healing: {heal_msg}]"
            else:
                r = _repair_or_exhaust(fn_name, args or {}, f"{type(e).__name__}: {e}")
        except Exception:
            r = f"[Tool Error: {e}]"
    finally:
        _SCOPE.preauthorized = False
        if _HUD_HOOK and prev_face:
            try:
                _HUD_HOOK(prev_face)
            except Exception as _e_silent:
                _config_mod.log_silent("execute_tool", _e_silent)

    # Autonomous Self-Healing on tool output returning recoverable failure
    if isinstance(r, str) and ("Traceback (most recent call last)" in r or "FileNotFoundError" in r or "No such file or directory" in r or "ModuleNotFoundError" in r or "unicodeescape" in r) and "[Autonomous Self-Healing:" not in r:
        try:
            from aria.agent.self_healing import attempt_auto_heal
            healed, healed_res, heal_msg = attempt_auto_heal(fn_name, args or {}, r, handler)
            if healed:
                r = f"{healed_res}\n[Autonomous Self-Healing: {heal_msg}]"
            else:
                # Self-repair Part 2: structured repair loop with a budget.
                r = _repair_or_exhaust(fn_name, args or {}, r)
        except Exception as _e_silent:
            _config_mod.log_silent("execute_tool", _e_silent)

    # 7. Truncation and caching
    r = truncate_output(str(r), MAX_TOOL_OUTPUT)
    _TURN_CALLS[sig] = r

    # 7b. ARIA ULTIMATE (Module 1) — routine capture. Records the call into the
    # open routine unless preauthorized (replays/approvals don't re-record).
    # Never let capture break dispatch.
    if not preauthorized:
        try:
            from aria import routines as _routines_mod
            _routines_mod.maybe_capture(fn_name, args or {})
        except Exception as _e_silent:
            _config_mod.log_silent("execute_tool", _e_silent)
    try:
        from aria import fleet as _fleet_mod
        _fleet_mod.finish(_fleet_token, _fleet_mod.result_ok(r))
    except Exception:
        pass
    return r, False


# --- Delegated background tasks (Merger Phase 3, blueprint SS2.1) ---
# EXPLICIT delegation only: these tools exist so the brain can hand a
# multi-step job to the queue when the user asks. Nothing here runs
# unless the model calls it in response to such a request.

def _tool_delegate_task(args: dict) -> str:
    from aria.agent import taskqueue
    goal = str((args or {}).get("goal", "") or "").strip()
    if not goal:
        return "[Argument error: delegate_task requires a goal.]"
    prio = str((args or {}).get("priority", "normal") or "normal").lower()
    priority = {"low": taskqueue.TaskPriority.LOW,
                "high": taskqueue.TaskPriority.HIGH}.get(
                    prio, taskqueue.TaskPriority.NORMAL)

    def _announce(text: str) -> None:
        # Delegated-task outcomes are the ONE failure/success class
        # announced by voice (blueprint SS3.2 loudness rule). Best
        # effort: the outcome is also in the event ring + registry.
        try:
            import aria.speech as _speech_mod
            _speech_mod.speak(text)
        except Exception:
            pass

    tid = taskqueue.get_queue().submit(goal, priority=priority,
                                       speak=_announce)
    return (f"Delegated as task {tid}: {goal} — it runs in the "
            f"background and I'll announce the outcome when it finishes.")


def _tool_task_status(args: dict) -> str:
    from aria.agent import taskqueue
    tid = str((args or {}).get("task_id", "") or "").strip()
    st = taskqueue.get_queue().get_status(tid) if tid else None
    if not st:
        return f"No delegated task with id '{tid}'."
    out = f"Task {st['task_id']}: {st['status']} — {st['goal']}"
    if st["status"] == "running":
        out += f" (running {st['elapsed_s']}s of {st['budget_s']}s budget)"
    if st.get("result"):
        out += f"\nResult: {st['result']}"
    if st.get("error"):
        out += f"\nError: {st['error']}"
    return out


def _tool_memory_review(args: dict) -> str:
    """List or settle queued memory contradictions (Gap fill 4).
    Consolidation never overwrites a stored fact on a conflict —
    it queues the claim here for the user to settle."""
    from aria.agent import consolidation
    args = args or {}
    cid = str(args.get("contradiction_id", "") or "").strip()
    keep = str(args.get("keep", "") or "").strip().lower()
    if not cid:
        pending = consolidation.pending_contradictions()
        if not pending:
            return "No memory contradictions are waiting for review."
        lines = ["Memory contradictions waiting for your call:"]
        for e in pending:
            lines.append(
                f"- [{e['id']}] {e['key']}: stored = "
                f"\"{e['old_value']}\" vs new claim = \"{e['new_value']}\" "
                f"(say keep new or keep old with the id)")
        return "\n".join(lines)
    if keep not in ("new", "old"):
        return "Tell me which to keep: 'new' (the distilled claim) or 'old' (the stored fact)."
    entry = consolidation.resolve_contradiction(cid, keep)
    if entry is None:
        return f"No contradiction with id '{cid}'."
    kept = entry["new_value"] if keep == "new" else entry["old_value"]
    return (f"Settled '{entry['key']}': keeping \"{kept}\". "
            "The other value was archived to the retired list.")


def _tool_task_resume(args: dict) -> str:
    """Resume a blocked/failed delegated task from its journal
    checkpoint (Gap fill 3): finished steps are skipped, the
    current step re-runs. The journal is the source of truth —
    a task that finished (or never existed) cannot resume."""
    from aria.agent import taskqueue, taskstore
    tid = str((args or {}).get("task_id", "") or "").strip()
    rec = taskstore.get(tid) if tid else None
    if rec is None:
        return (f"No journal record for task '{tid}' — only tasks "
                "that were journaled can resume.")
    try:
        new_id = taskqueue.get_queue().submit(
            rec.get("goal", ""), resume_from=tid)
    except ValueError as e:
        return f"Can't resume task {tid}: {e}"
    done = len(rec.get("completed") or [])
    return (f"Resumed task {tid} as new task {new_id} — "
            f"{done} completed step(s) will be skipped.")


def _tool_task_cancel(args: dict) -> str:
    from aria.agent import taskqueue
    tid = str((args or {}).get("task_id", "") or "").strip()
    if tid and taskqueue.get_queue().cancel(tid):
        return f"Cancellation requested for task {tid}."
    return (f"Task '{tid}' can't be cancelled "
            f"(unknown id or already finished).")


# ---------------- Initialize Default Built-in Tool Registry ----------------


from aria import scheduler
from aria import vision
from aria import hardware
from aria import spotify
from aria import hud

# ARIA ULTIMATE modules — top-level imports are stdlib + aria.config only,
# so these are cycle-safe here (same position as the scheduler/vision imports).
from aria import approval as approval_mod
from aria import browser_agent as browser_agent_mod
from aria import research as research_mod
from aria import routines as routines_mod
from aria import triggers as triggers_mod
from aria import phone_node as phone_mod
from aria import screenwatch as screenwatch_mod
from aria import persona as persona_mod
from aria import pricecheck as pricecheck_mod
from aria import inbox as inbox_mod
from aria import memory as memory_mod
from aria.tools import fileops as fileops_mod
from aria.tools import fileproc as fileproc_mod
from aria.tools import selfedit as selfedit_mod
from aria.tools import winctl as winctl_mod
from aria.tools import triage as triage_mod
from aria.tools import usertools as usertools_mod


def _tool_restart_program(args: dict) -> str:
    """restart_program: the user told her to restart her program
    ("restart your program" / "restart yourself" / "restart the
    app"). Direct commands only: a routine replay rides
    preauthorized scope, a delegated worker rides delegated scope,
    and neither may relaunch the host process. The brain owns the
    relaunch itself (availability check, arbiter, speech drain) —
    lazy import: brain imports dispatch, never the reverse at
    module load."""
    if in_preauthorized_scope():
        return ("[restart_program refused: a restart is a direct command "
                "only — never inside a routine or an approved replay. "
                "Ask me directly and I will restart.]")
    if in_delegated_scope():
        return ("[restart_program refused: a delegated task cannot "
                "restart the program it is running inside.]")
    from aria.agent import brain as _brain_mod
    return _brain_mod.request_program_restart()


def _init_default_registry():
    _REGISTRY["web_search"] = lambda a: builtins.tool_web_search(a.get("query", ""))
    _REGISTRY["research"] = lambda a: research_mod.tool_research(a)
    _REGISTRY["event_trigger"] = lambda a: triggers_mod.tool_event_trigger(a)
    _REGISTRY["browser_agent"] = lambda a: browser_agent_mod.tool_browser_agent(a)
    _REGISTRY["run_python_code"] = lambda a: tool_run_python(
        a.get("code", ""), log_callback=_LOG_HOOK
    )
    _REGISTRY["save_memory"] = lambda a: (
        memory_mod.memory_save(a.get("category", "general"), a.get("key", ""), a.get("value", "")),
        f"Memory saved: {a.get('key')}"
    )[1]
    _REGISTRY["search_memory"] = lambda a: memory_mod.memory_search_semantic(a.get("query", ""))
    _REGISTRY["forget_memory"] = lambda a: builtins.memory_forget(a.get("query", ""))
    _REGISTRY["journal_write"] = lambda a: builtins.journal_write(a.get("entry", ""))
    _REGISTRY["gui_click"] = lambda a: builtins.tool_gui_click(int(a.get("x", 0)), int(a.get("y", 0)))
    _REGISTRY["gui_type"] = lambda a: builtins.tool_gui_type(a.get("text", ""))
    _REGISTRY["open_app_or_url"] = lambda a: builtins.tool_open_app_or_url(a.get("target", ""))
    _REGISTRY["github_push_file"] = lambda a: builtins.tool_github_push(
        a.get("repo", ""), a.get("filepath", ""), a.get("content", ""), a.get("message", "")
    )
    _REGISTRY["github_create_repo"] = lambda a: builtins.tool_github_create_repo(
        a.get("name", ""), a.get("description", ""), bool(a.get("private", True))
    )
    _REGISTRY["write_file"] = lambda a: builtins.tool_write_file(a.get("filename", "file.txt"), a.get("content", ""))
    _REGISTRY["edit_own_code"] = lambda a: selfedit_mod.tool_edit_own_code(a.get("path", ""), a.get("content", ""))
    _REGISTRY["patch_own_code"] = lambda a: selfedit_mod.tool_patch_own_code(a.get("path", ""), a.get("old_text", ""), a.get("new_text", ""))
    _REGISTRY["read_file"] = lambda a: builtins.tool_read_file(a.get("filename", ""))
    _REGISTRY["list_workspace"] = lambda a: builtins.tool_list_files()
    _REGISTRY["fetch_url"] = lambda a: builtins.tool_fetch_url(a.get("url", ""))
    _REGISTRY["clipboard_read"] = lambda a: builtins.tool_clipboard_read()
    _REGISTRY["clipboard_write"] = lambda a: builtins.tool_clipboard_write(a.get("text", ""))
    _REGISTRY["list_windows"] = lambda a: builtins.tool_list_windows()
    _REGISTRY["focus_window"] = lambda a: builtins.tool_focus_window(a.get("title", ""))
    _REGISTRY["minimize_window"] = lambda a: builtins.tool_minimize_window(a.get("title", ""))
    _REGISTRY["close_window"] = lambda a: builtins.tool_close_window(a.get("title", ""))
    _REGISTRY["media_key"] = lambda a: builtins.tool_media_key(a.get("action", "") or a.get("key", "play_pause"))
    _REGISTRY["media_control"] = lambda a: builtins.tool_media_control(
        a.get("action", "status"), a.get("target", "auto"),
        str(a.get("value", "")))
    _REGISTRY["mtg_card"] = lambda a: builtins.tool_mtg_card(a.get("card_name", ""))
    _REGISTRY["watch_price"] = lambda a: builtins.tool_watch_price(
        a.get("url", ""), a.get("target_price", ""), a.get("label", "item")
    )
    _REGISTRY["list_price_watches"] = lambda a: builtins.tool_list_price_watches()
    _REGISTRY["unwatch_price"] = lambda a: builtins.tool_unwatch_price(int(a.get("watch_id", 0)))
    _REGISTRY["take_note"] = lambda a: builtins.note_take(a.get("text", ""))
    _REGISTRY["read_notes"] = lambda a: builtins.note_read(a.get("date", "today"))
    _REGISTRY["find_file"] = lambda a: builtins.tool_find_file(a.get("name", ""), a.get("ext", ""))
    _REGISTRY["volume"] = lambda a: builtins.tool_volume(a.get("action", "status"), int(a.get("level", 50)))
    _REGISTRY["mtg_advice"] = lambda a: builtins.tool_mtg_advice(a.get("deck", ""), a.get("card_name", ""))
    _REGISTRY["bridge_token"] = lambda a: f"Your bridge token is: {BRIDGE_TOKEN}. Enter it on the phone bridge login page."

    # Inbox tools (phone bridge uploads)
    _REGISTRY["inbox_list"] = lambda a: (
        "\n".join(f"- {f['name']} ({f['size_h']}, {f['when']})"
                  for f in inbox_mod.list_inbox())
        or "Inbox is empty."
    )
    _REGISTRY["inbox_describe"] = lambda a: inbox_mod.describe_inbox_image(a.get("name", ""))
    _REGISTRY["inbox_read"] = lambda a: builtins.tool_inbox_read(a.get("name", ""))
    _REGISTRY["manage_autonomous_goal"] = lambda a: builtins.tool_manage_autonomous_goal(
        action=a.get("action", "list"), title=a.get("title", ""), description=a.get("description", ""),
        goal_id=int(a.get("goal_id", 0)), interval_s=int(a.get("interval_s", 0)), priority=int(a.get("priority", 5))
    )
    _REGISTRY["manage_background_job"] = lambda a: builtins.tool_manage_background_job(
        action=a.get("action", "list"), command=a.get("command", ""), name=a.get("name", ""), job_id=int(a.get("job_id", 0))
    )
    _REGISTRY["system_health_audit"] = lambda a: builtins.tool_system_health_audit()
    _REGISTRY["delegate_task"] = lambda a: _tool_delegate_task(a)
    _REGISTRY["memory_review"] = lambda a: _tool_memory_review(a)
    _REGISTRY["task_status"] = lambda a: _tool_task_status(a)
    _REGISTRY["task_resume"] = lambda a: _tool_task_resume(a)
    _REGISTRY["task_cancel"] = lambda a: _tool_task_cancel(a)
    _REGISTRY["restart_program"] = lambda a: _tool_restart_program(a)
    # ---- Gap fills (2026-10-27): notify / backup / shell / small tools ----
    # Local imports: these modules are stdlib-only at top level, but
    # keeping them out of dispatch's module imports keeps the funnel
    # loadable even if a gap module is ever broken.
    from aria import notify as _notify_mod
    from aria import backup_mirror as _backup_mirror_mod
    from aria.tools import shelltool as _shelltool_mod
    from aria.tools import gaptools as _gaptools_mod
    _REGISTRY["notify_phone"] = lambda a: _notify_mod.tool_notify_phone(a)
    _REGISTRY["set_backup_mirror"] = lambda a: _backup_mirror_mod.tool_set_backup_mirror(a)
    _REGISTRY["run_shell_command"] = lambda a: _shelltool_mod.tool_run_shell_command(a)
    _REGISTRY["flight_finder"] = lambda a: _gaptools_mod.tool_flight_finder(a)
    _REGISTRY["game_updater"] = lambda a: _gaptools_mod.tool_game_updater(a)
    _REGISTRY["browser_control"] = lambda a: _gaptools_mod.tool_browser_control(a)
    _REGISTRY["os_reminder"] = lambda a: _gaptools_mod.tool_os_reminder(a)
    _REGISTRY["update_status"] = lambda a: _gaptools_mod.tool_update_status(a)
    _REGISTRY["code_helper"] = lambda a: _gaptools_mod.tool_code_helper(a)
    _REGISTRY["dev_agent"] = lambda a: _gaptools_mod.tool_dev_agent(a)
    _REGISTRY["self_heal_diagnose"] = lambda a: builtins.tool_self_heal_diagnose(
        error_text=a.get("error_text", ""), context=a.get("context", "")
    )
    _REGISTRY["load_toolkit"] = lambda a: tool_load_toolkit(a.get("toolkit", ""))
    _REGISTRY["run_skill"] = lambda a: tool_run_skill(
        a.get("skill_name", ""),
        a.get("objective", ""),
        tool_executor=lambda name, args: execute_tool(name, args, preauthorized=True)[0],
        state_callback=_HUD_HOOK,
        log_callback=_LOG_HOOK
    )
    _REGISTRY["gmail_setup"] = lambda a: builtins.tool_gmail_setup(a.get("gmail_user", ""), a.get("app_password", ""))
    _REGISTRY["send_email"] = lambda a: builtins.tool_send_email(a.get("to", ""), a.get("subject", ""), a.get("body", ""))
    _REGISTRY["read_email"] = lambda a: builtins.tool_read_email(a.get("query", ""), a.get("limit", 10), bool(a.get("unread_only", False)), a.get("uid", ""))

    # Scheduler tools
    _REGISTRY["morning_briefing"] = lambda a: scheduler.tool_briefing()
    _REGISTRY["set_reminder"] = lambda a: scheduler.tool_set_reminder(int(a.get("delay_seconds", 60)), a.get("message", ""))
    _REGISTRY["set_reminder_on_return"] = lambda a: scheduler.tool_set_reminder_on_return(a.get("message", ""))
    _REGISTRY["acknowledge_reminders"] = lambda a: scheduler.tool_acknowledge_reminders()
    _REGISTRY["set_recurring_task"] = lambda a: scheduler.tool_set_recurring(int(a.get("interval_seconds", 3600)), a.get("prompt", ""))
    _REGISTRY["set_timer"] = lambda a: scheduler.tool_set_timer(a.get("duration_text", "10m"), a.get("label", "timer"))
    _REGISTRY["list_scheduled_tasks"] = lambda a: scheduler.tool_list_scheduled()
    _REGISTRY["cancel_scheduled_task"] = lambda a: scheduler.tool_cancel_scheduled(int(a.get("task_id", 0)))
    _REGISTRY["break_reminders"] = lambda a: scheduler.tool_break_reminders(a.get("enabled", True))
    _REGISTRY["calendar_setup"] = lambda a: builtins.tool_calendar_setup(a.get("ical_url", ""))
    _REGISTRY["check_calendar"] = lambda a: builtins.tool_check_calendar(a.get("days", 1))

    # MCP client tools — ARIA connects to MCP servers and uses their tools
    _REGISTRY["mcp_setup"] = lambda a: builtins.tool_mcp_setup(
        a.get("name", ""), a.get("command", ""), a.get("args", ""),
        a.get("url", ""), a.get("env", ""), a.get("headers", ""),
        a.get("transport", ""))
    _REGISTRY["mcp_connect"] = lambda a: builtins.tool_mcp_connect(a.get("name", ""))
    _REGISTRY["mcp_disconnect"] = lambda a: builtins.tool_mcp_disconnect(a.get("name", ""))
    _REGISTRY["mcp_list_servers"] = lambda a: builtins.tool_mcp_list_servers()
    _REGISTRY["mcp_remove_server"] = lambda a: builtins.tool_mcp_remove_server(a.get("name", ""))

    # Vision tools
    _REGISTRY["take_photo"] = lambda a: vision.tool_take_photo(a.get("name", ""))
    _REGISTRY["describe_camera"] = lambda a: vision.tool_describe_camera(a.get("question", ""))
    _REGISTRY["vision_look"] = lambda a: vision.tool_vision_look(a.get("question", ""), a.get("image_path", ""))
    _REGISTRY["read_screen"] = lambda a: vision.tool_read_screen(a.get("question", ""))
    _REGISTRY["take_screenshot"] = lambda a: vision.tool_screenshot(a.get("name", ""))
    _REGISTRY["face_tracking"] = lambda a: vision.tool_face_tracking(a.get("on", True))

    # Hardware tools
    _REGISTRY["move_head_servos"] = lambda a: hardware.tool_move_head_servos(int(a.get("pan", 90)), int(a.get("tilt", 45)))
    _REGISTRY["drive_wheels"] = lambda a: hardware.tool_drive(int(a.get("left", 0)), int(a.get("right", 0)), float(a.get("seconds", 0)))
    _REGISTRY["body_stop"] = lambda a: hardware.tool_body_stop()

    # Spotify tools
    _REGISTRY["spotify"] = lambda a: spotify.tool_spotify(a.get("action", "play_pause"), a.get("query", ""))
    _REGISTRY["dj"] = lambda a: spotify.tool_dj(a.get("request", ""))

    # HUD commands tools
    _REGISTRY["show_commands"] = lambda a: hud.tool_show_commands()
    _REGISTRY["hide_commands"] = lambda a: hud.tool_hide_commands()

    # ---- ARIA ULTIMATE (Module 4): approval / safety layer ----
    # approve / deny / set_approval_mode are deliberately NOT
    # registered (ChatGPT pass 2 runner-up 2, fixed 2026-10-28):
    # as model-callable tools they let the model grant its own
    # approvals and relax its own policy — the only "gate" was a
    # prompt instruction not to. Resolutions now happen ONLY
    # out-of-band, where a model turn cannot reach: the dashboard
    # confirm banner and the Settings dialog call aria.approval's
    # functions directly on a human click. The model keeps the
    # two read-only views so it can tell the user what is waiting
    # and which mode is active.
    _REGISTRY["list_pending_approvals"] = lambda a: approval_mod.tool_list_pending_approvals()
    _REGISTRY["get_approval_mode"] = lambda a: approval_mod.tool_get_approval_mode()

    # ---- ARIA ULTIMATE (Module 1): routines ----
    _REGISTRY["routine_record_start"] = lambda a: routines_mod.tool_routine_record_start(a.get("name", ""))
    _REGISTRY["routine_record_stop"] = lambda a: routines_mod.tool_routine_record_stop()
    _REGISTRY["run_routine"] = lambda a: routines_mod.tool_run_routine(a.get("name", ""), a.get("params_json", "{}"))
    _REGISTRY["list_routines"] = lambda a: routines_mod.tool_list_routines()
    _REGISTRY["delete_routine"] = lambda a: routines_mod.tool_delete_routine(a.get("name", ""))
    _REGISTRY["describe_routine"] = lambda a: routines_mod.tool_describe_routine(a.get("name", ""))
    _REGISTRY["trust_routine"] = lambda a: routines_mod.tool_trust_routine(a.get("name", ""))
    _REGISTRY["untrust_routine"] = lambda a: routines_mod.tool_untrust_routine(a.get("name", ""))
    _REGISTRY["export_routine_pack"] = lambda a: routines_mod.tool_export_routine_pack(a.get("names_csv", ""), a.get("dest_path", ""))
    _REGISTRY["import_routine_pack"] = lambda a: routines_mod.tool_import_routine_pack(a.get("pack_path", ""))

    # ---- Phone node (Batch F): the paired Android phone ----
    _REGISTRY["phone_status"] = lambda a: phone_mod.tool_phone_status()
    _REGISTRY["phone_list_apps"] = lambda a: phone_mod.tool_phone_list_apps()
    _REGISTRY["phone_open_app"] = lambda a: phone_mod.tool_phone_open_app(a.get("label", ""), a.get("package", ""))
    _REGISTRY["phone_read_screen"] = lambda a: phone_mod.tool_phone_read_screen()
    _REGISTRY["phone_notifications"] = lambda a: phone_mod.tool_phone_notifications(a.get("limit", 10))
    _REGISTRY["phone_tap"] = lambda a: phone_mod.tool_phone_tap(a.get("ref", ""), a.get("x"), a.get("y"))
    _REGISTRY["phone_swipe"] = lambda a: phone_mod.tool_phone_swipe(a.get("x1"), a.get("y1"), a.get("x2"), a.get("y2"), a.get("duration_ms", ""))
    _REGISTRY["phone_type"] = lambda a: phone_mod.tool_phone_type(a.get("text", ""), a.get("ref", ""))
    _REGISTRY["phone_key"] = lambda a: phone_mod.tool_phone_key(a.get("key", ""))

    # ---- ARIA ULTIMATE (Module 2): file commander ----
    _REGISTRY["file_organize"] = lambda a: fileops_mod.tool_file_organize(
        a.get("directory", ""), bool(a.get("dry_run", True)))
    _REGISTRY["file_find_advanced"] = lambda a: fileops_mod.tool_file_find_advanced(
        a.get("directory", ""), a.get("pattern", ""), float(a.get("min_size_mb", 0) or 0),
        float(a.get("max_age_days", 0) or 0), a.get("content_contains", ""))
    _REGISTRY["file_duplicates"] = lambda a: fileops_mod.tool_file_duplicates(a.get("directory", ""))
    _REGISTRY["disk_usage"] = lambda a: fileops_mod.tool_disk_usage(
        a.get("directory", ""), int(a.get("top_n", 20) or 20))

    # ---- Merger Phase 5: the file organ ----
    _REGISTRY["process_file"] = lambda a: fileproc_mod.tool_process_file(
        a.get("path", ""), a.get("question", ""))

    # ---- ARIA ULTIMATE (Module 3): window/app control expansion ----
    _REGISTRY["window_snap"] = lambda a: winctl_mod.tool_window_snap(a.get("title", ""), a.get("position", ""))
    _REGISTRY["launch_app"] = lambda a: winctl_mod.tool_launch_app(a.get("name", ""))

    # ---- ARIA ULTIMATE (Module 6): inbox triage ----
    _REGISTRY["triage_email"] = lambda a: triage_mod.tool_triage_email(int(a.get("limit", 20) or 20))

    # ---- ARIA ULTIMATE (Module 7): screen watcher ----
    _REGISTRY["watch_screen"] = lambda a: screenwatch_mod.tool_watch_screen(
        a.get("name", ""), a.get("question", ""), int(a.get("interval_s", 300) or 300))
    _REGISTRY["unwatch_screen"] = lambda a: screenwatch_mod.tool_unwatch_screen(a.get("name", ""))
    _REGISTRY["list_screen_watches"] = lambda a: screenwatch_mod.tool_list_screen_watches()

    # ---- ARIA ULTIMATE (Module 9): persona engine ----
    _REGISTRY["set_persona"] = lambda a: persona_mod.tool_set_persona(a.get("name", ""))
    _REGISTRY["list_personas"] = lambda a: persona_mod.tool_list_personas()
    _REGISTRY["get_persona"] = lambda a: persona_mod.tool_get_persona()

    # ---- ARIA ULTIMATE (Module 8): user tool SDK ----
    _REGISTRY["reload_user_tools"] = lambda a: usertools_mod.tool_reload_user_tools()

    # ---- ARIA ULTIMATE: price-drop checker (Module 5 producer gap) ----
    _REGISTRY["check_price_watches"] = lambda a: pricecheck_mod.tool_check_price_watches()

    # ---- ARIA ULTIMATE: boot-time wiring (never break boot on failure) ----
    try:
        from aria.tools.schemas import register_dynamic_tool_declaration
        usertools_mod.load_user_tools_into_registry()
        for _uname, _uhandler, _udecl in usertools_mod.get_user_tools():
            register_dynamic_tool_declaration(_uname, _udecl, toolkit="user")
    except Exception as _e_silent:
        _config_mod.log_silent("_init_default_registry", _e_silent)
    try:
        pricecheck_mod.ensure_pricecheck_task()
    except Exception as _e_silent:
        _config_mod.log_silent("_init_default_registry", _e_silent)


_init_default_registry()

load_toolkit = tool_load_toolkit
