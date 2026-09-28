"""
ARIA Multimodal Live WebSocket Integration.
Manages bidirectional real-time audio/video streaming via Gemini Live.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional, Callable, Any, Dict

from aria.config import GEMINI_API_KEY, get_gemini_key, get_setting, set_setting, add_log
from aria.memory import spine_append, log_conversation
from aria.tools.schemas import TOOLS_DECLARATION
from aria.tools.dispatch import execute_tool

try:
    from aria_live import GeminiLiveBridge
    LIVE_AVAILABLE = True
except Exception:
    GeminiLiveBridge = None
    LIVE_AVAILABLE = False

GEMINI_LIVE_MODE = bool(get_setting("gemini_live", False))
_LIVE_BRIDGE: Optional[Any] = None
_LIVE_LOCK = threading.Lock()

_STATE_CHANGE_HOOK: Optional[Callable[[str], None]] = None
_HUD_REDRAW_HOOK: Optional[Callable[[], None]] = None


def set_live_hooks(state_hook: Optional[Callable[[str], None]] = None,
                   hud_hook: Optional[Callable[[], None]] = None):
    global _STATE_CHANGE_HOOK, _HUD_REDRAW_HOOK
    _STATE_CHANGE_HOOK = state_hook
    _HUD_REDRAW_HOOK = hud_hook


def _on_live_transcript(text: str, is_final: bool):
    if is_final:
        add_log(f"Speech (Live): {text[:28]}...")
        log_conversation("A.R.I.A.", text)
        spine_append("model", text)
    if _HUD_REDRAW_HOOK:
        try:
            _HUD_REDRAW_HOOK()
        except Exception:
            pass


def _on_live_state_change(new_state: str):
    if _STATE_CHANGE_HOOK:
        try:
            _STATE_CHANGE_HOOK(new_state)
        except Exception:
            pass
    if _HUD_REDRAW_HOOK:
        try:
            _HUD_REDRAW_HOOK()
        except Exception:
            pass


def get_live_bridge(soul_text: str = "") -> Optional[Any]:
    global _LIVE_BRIDGE
    if not LIVE_AVAILABLE or not GEMINI_LIVE_MODE:
        return None
    with _LIVE_LOCK:
        if _LIVE_BRIDGE is None:
            try:
                tools = TOOLS_DECLARATION[0]["function_declarations"] if TOOLS_DECLARATION else []
                active_key = get_gemini_key() or GEMINI_API_KEY
                _LIVE_BRIDGE = GeminiLiveBridge(
                    api_key=active_key,
                    model="models/gemini-3.8-live",
                    voice="Aoede",
                    system_instruction=soul_text,
                    tool_declarations=tools,
                    tool_executor=lambda name, args: execute_tool(name, args, preauthorized=True)[0],
                    on_transcript=_on_live_transcript,
                    on_state_change=_on_live_state_change,
                    on_log=add_log
                )
                _LIVE_BRIDGE.start()
            except Exception as e:
                add_log(f"Live bridge init error: {e}")
                _LIVE_BRIDGE = None
        return _LIVE_BRIDGE


def set_gemini_live_mode(on: bool, soul_text: str = "") -> bool:
    global GEMINI_LIVE_MODE, _LIVE_BRIDGE
    GEMINI_LIVE_MODE = bool(on)
    set_setting("gemini_live", GEMINI_LIVE_MODE)
    if not GEMINI_LIVE_MODE and _LIVE_BRIDGE:
        try:
            _LIVE_BRIDGE.stop()
        except Exception:
            pass
        _LIVE_BRIDGE = None
    elif GEMINI_LIVE_MODE and not _LIVE_BRIDGE:
        get_live_bridge(soul_text)
    return GEMINI_LIVE_MODE
