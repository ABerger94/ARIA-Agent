"""
ARIA Agent Intelligence Subsystem.
Central brain, system prompt formulation, streaming LLM execution,
instant voice shortcuts, proactive behaviors, autonomous self-healing,
and persistent worker threads.
"""

import sys as _sys

from aria.agent.brain import (
    run_agent,
    build_system_instruction
)

# Direct reference to the fully-loaded brain module, captured
# once here so the delegation below never touches the import
# machinery at call time (see __getattr__).
_brain_ref = _sys.modules.get("aria.agent.brain")

# Live brain state — BUSY_PROCESSING, LAST_ACTIVITY,
# CONVERSATION_HISTORY — is deliberately NOT from-imported here.
# A from-import snapshots the value at package import, but brain
# REBINDS all three as she runs (busy on/off around a turn, the
# activity stamp, history compaction), so package readers saw the
# import-time values forever: main's scheduler / heartbeat /
# consolidation callbacks read busy as permanently False, the
# activity stamp diverged between brain and package (main writes
# it too), and history-hook appends went to a stale list after a
# compaction. PEP 562 delegation instead: attribute access on the
# package returns brain's CURRENT binding. Writers must write to
# aria.agent.brain directly (a package-level assignment would
# shadow this delegation) — main.py does.
_LIVE_STATE_NAMES = frozenset(
    {"BUSY_PROCESSING", "LAST_ACTIVITY", "CONVERSATION_HISTORY"})


def __getattr__(name):
    # HARDENED (2026-10-27, after a RecursionError-at-startup
    # report on Alek's laptop): the first cut of this delegation
    # ran `from aria.agent import brain` on EVERY delegated read —
    # import machinery (fromlist handling, import lock, package
    # attribute probes) inside an attribute hook that startup
    # threads call constantly is a reentrancy hazard. This version
    # only reads the module reference captured at import above:
    # no imports, no locks, and any probe that lands before the
    # reference exists (or for an unknown name) fails clean with
    # AttributeError instead of reaching into a half-built package.
    if name in _LIVE_STATE_NAMES:
        _brain = globals().get("_brain_ref")
        if _brain is None:
            raise AttributeError(
                f"module 'aria.agent' has no attribute {name!r}")
        return getattr(_brain, name)
    raise AttributeError(
        f"module 'aria.agent' has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | _LIVE_STATE_NAMES)
from aria.agent.shortcuts import check_voice_shortcut, normalize_shortcut
from aria.agent.proactive import (
    proactive_say,
    proactive_heartbeat_loop,
    idle_consolidation_loop,
    mood_state,
    mood_word,
    mood_note_interaction,
    looks_like_decline,
    record_decline,
    goal_create,
    goal_list,
    goal_cancel,
    goal_complete,
    evaluate_autonomous_goals
)
from aria.agent.self_healing import (
    diagnose_error,
    attempt_auto_heal,
    record_incident,
    get_recent_incidents,
    tool_self_heal_diagnose
)
from aria.agent.workers import (
    start_background_job,
    list_background_jobs,
    cancel_background_job,
    get_background_job_log,
    system_health_audit,
    start_all_workers,
    pop_pending_downloads,
    pop_system_alerts,
    pop_job_notifications
)
import aria.agent.proactive as proactive
import aria.agent.self_healing as self_healing
import aria.agent.workers as workers

__all__ = [
    "run_agent",
    "build_system_instruction",
    "CONVERSATION_HISTORY",
    "BUSY_PROCESSING",
    "LAST_ACTIVITY",
    "check_voice_shortcut",
    "normalize_shortcut",
    "proactive_say",
    "proactive_heartbeat_loop",
    "idle_consolidation_loop",
    "mood_state",
    "mood_word",
    "mood_note_interaction",
    "looks_like_decline",
    "record_decline",
    "goal_create",
    "goal_list",
    "goal_cancel",
    "goal_complete",
    "evaluate_autonomous_goals",
    "diagnose_error",
    "attempt_auto_heal",
    "record_incident",
    "get_recent_incidents",
    "tool_self_heal_diagnose",
    "start_background_job",
    "list_background_jobs",
    "cancel_background_job",
    "get_background_job_log",
    "system_health_audit",
    "start_all_workers",
    "pop_pending_downloads",
    "pop_system_alerts",
    "pop_job_notifications",
    "proactive",
    "self_healing",
    "workers"
]
