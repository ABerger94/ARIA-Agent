"""
ARIA Agent Intelligence Subsystem.
Central brain, system prompt formulation, streaming LLM execution,
instant voice shortcuts, proactive behaviors, autonomous self-healing,
and persistent worker threads.
"""

from aria.agent.brain import (
    run_agent,
    gemini_call,
    gemini_text,
    build_system_instruction,
    CONVERSATION_HISTORY,
    BUSY_PROCESSING,
    LAST_ACTIVITY
)
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
    "gemini_call",
    "gemini_text",
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
