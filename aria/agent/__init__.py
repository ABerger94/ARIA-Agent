"""
ARIA Agent Intelligence Subsystem.
Central brain, system prompt formulation, streaming LLM execution,
instant voice shortcuts, proactive behaviors, autonomous self-healing,
and persistent worker threads.
"""

from aria.agent.brain import (
    run_agent,
    build_system_instruction
)

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
    if name in _LIVE_STATE_NAMES:
        from aria.agent import brain as _brain
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
