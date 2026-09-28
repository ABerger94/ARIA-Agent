"""
ARIA Agent Intelligence Subsystem.
Central brain, system prompt formulation, streaming LLM execution,
instant voice shortcuts, and proactive behaviors.
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
    record_decline
)

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
]
