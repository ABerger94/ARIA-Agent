"""
ARIA Tool Execution Dispatcher and Progressive Toolkit Management.
Coordinates tool invocation, argument repair, duplicate call suppression,
risky tool audit logging, HUD feedback transitions, and progressive schema payloads.
"""

import time
from typing import Dict, List, Optional, Set, Tuple, Callable, Any

from aria.config import MAX_TOOL_OUTPUT, BRIDGE_TOKEN, GITHUB_USERNAME, redact
from aria.memory import spine_append as default_spine_append, add_log as default_add_log
from aria.tools.schemas import (
    TOOLKITS, LOAD_TOOLKIT_DECLARATION, get_tool_decls_by_name,
    get_toolkit_declarations, get_toolkits_prompt_block
)
from aria.tools.sandbox import (
    RISKY_TOOLS, call_signature, missing_required_args,
    stale_target_check, risky_description, truncate_output,
    tool_run_python
)
from aria.tools.skills import tool_run_skill
import aria.tools.builtins as builtins

# Progressive Toolkit State
_LOADED_TOOLKITS: Set[str] = {"core"}

# Turn Context State
_TURN_CALLS: Dict[str, str] = {}
_LAST_USER_MESSAGE: str = ""
_LAST_OPEN_TARGET: str = ""
_TURN_NUDGED: bool = False
_LAST_TOOL_EXECUTED: Tuple[Optional[str], float] = (None, 0.0)

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
def execute_tool(fn_name: str, args: dict, preauthorized: bool = False) -> Tuple[str, bool]:
    """Dispatch one tool with full validation, sandboxing, and audit logging.
    Returns (result_text, needs_confirm_bool).
    """
    global _LAST_OPEN_TARGET, _TURN_NUDGED, _LAST_TOOL_EXECUTED
    
    # 1. Log to spine
    if _SPINE_HOOK:
        try:
            _SPINE_HOOK("tool", {"name": fn_name, "args": str(args)[:300]})
        except Exception:
            pass

    # 2. Duplicate-call blocking
    sig = call_signature(fn_name, args)
    if sig in _TURN_CALLS:
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

    # 4. Risky tools safety & audit
    if fn_name in RISKY_TOOLS:
        nudge, _TURN_NUDGED = stale_target_check(
            fn_name, args, _LAST_OPEN_TARGET, _LAST_USER_MESSAGE, _TURN_NUDGED
        )
        if nudge:
            return nudge, False
        if fn_name == "open_app_or_url":
            _LAST_OPEN_TARGET = (args or {}).get("target", "")

        # Record action in model history hook
        if _HISTORY_HOOK:
            try:
                desc = risky_description(fn_name, args, GITHUB_USERNAME)
                _HISTORY_HOOK({
                    "role": "model",
                    "parts": [{"text": "[Executed: " + redact(desc) + "]"}]
                })
            except Exception:
                pass

    # 5. Visual HUD feedback
    prev_face = None
    if _HUD_HOOK:
        try:
            prev_face = _HUD_HOOK("coding")
        except Exception:
            pass
    _LAST_TOOL_EXECUTED = (fn_name, time.time())

    # 6. Execute Handler
    try:
        handler = _REGISTRY.get(fn_name)
        if handler:
            r = handler(args or {})
        else:
            r = f"Unknown tool: {fn_name}"
    except Exception as e:
        r = f"[Tool Error: {e}]"
    finally:
        if _HUD_HOOK and prev_face:
            try:
                _HUD_HOOK(prev_face)
            except Exception:
                pass

    # 7. Truncation and caching
    r = truncate_output(str(r), MAX_TOOL_OUTPUT)
    _TURN_CALLS[sig] = r
    return r, False


# ---------------- Initialize Default Built-in Tool Registry ----------------


from aria import scheduler
from aria import vision
from aria import hardware
from aria import spotify
from aria import hud


def _init_default_registry():
    _REGISTRY["web_search"] = lambda a: builtins.tool_web_search(a.get("query", ""))
    _REGISTRY["run_python_code"] = lambda a: tool_run_python(
        a.get("code", ""), log_callback=_LOG_HOOK
    )
    _REGISTRY["save_memory"] = lambda a: (
        builtins.memory_save(a.get("category", "general"), a.get("key", ""), a.get("value", "")),
        f"Memory saved: {a.get('key')}"
    )[1]
    _REGISTRY["search_memory"] = lambda a: builtins.memory_search_semantic(a.get("query", ""))
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
    _REGISTRY["read_file"] = lambda a: builtins.tool_read_file(a.get("filename", ""))
    _REGISTRY["list_workspace"] = lambda a: builtins.tool_list_files()
    _REGISTRY["fetch_url"] = lambda a: builtins.tool_fetch_url(a.get("url", ""))
    _REGISTRY["clipboard_read"] = lambda a: builtins.tool_clipboard_read()
    _REGISTRY["clipboard_write"] = lambda a: builtins.tool_clipboard_write(a.get("text", ""))
    _REGISTRY["list_windows"] = lambda a: builtins.tool_list_windows()
    _REGISTRY["focus_window"] = lambda a: builtins.tool_focus_window(a.get("title", ""))
    _REGISTRY["minimize_window"] = lambda a: builtins.tool_minimize_window(a.get("title", ""))
    _REGISTRY["close_window"] = lambda a: builtins.tool_close_window(a.get("title", ""))
    _REGISTRY["media_key"] = lambda a: builtins.tool_media_key(a.get("action", ""))
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
    _REGISTRY["bridge_token"] = lambda a: f"Your bridge token is: {BRIDGE_TOKEN}. Enter it once on the phone bridge page."
    _REGISTRY["gemini_keys"] = lambda a: builtins.tool_gemini_keys(a.get("action", "status"), a.get("key", ""))
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

    # Scheduler tools
    _REGISTRY["morning_briefing"] = lambda a: scheduler.tool_briefing()
    _REGISTRY["set_reminder"] = lambda a: scheduler.tool_set_reminder(int(a.get("delay_seconds", 60)), a.get("message", ""))
    _REGISTRY["set_recurring_task"] = lambda a: scheduler.tool_set_recurring(int(a.get("interval_seconds", 3600)), a.get("prompt", ""))
    _REGISTRY["set_timer"] = lambda a: scheduler.tool_set_timer(a.get("duration_text", "10m"), a.get("label", "timer"))
    _REGISTRY["list_scheduled_tasks"] = lambda a: scheduler.tool_list_scheduled()
    _REGISTRY["cancel_scheduled_task"] = lambda a: scheduler.tool_cancel_scheduled(int(a.get("task_id", 0)))
    _REGISTRY["break_reminders"] = lambda a: scheduler.tool_break_reminders(a.get("enabled", True))

    # Vision tools
    _REGISTRY["take_photo"] = lambda a: vision.tool_take_photo(a.get("name", ""))
    _REGISTRY["read_screen"] = lambda a: vision.tool_read_screen(a.get("question", ""))
    _REGISTRY["take_screenshot"] = lambda a: vision.tool_screenshot(a.get("name", ""))
    _REGISTRY["face_tracking"] = lambda a: vision.tool_face_tracking(a.get("on", True))

    # Hardware tools
    _REGISTRY["move_head_servos"] = lambda a: hardware.tool_move_head_servos(int(a.get("pan", 90)), int(a.get("tilt", 45)))

    # Spotify tools
    _REGISTRY["spotify"] = lambda a: spotify.tool_spotify(a.get("action", "play_pause"), a.get("query", ""))
    _REGISTRY["dj"] = lambda a: spotify.tool_dj(a.get("request", ""))

    # HUD commands tools
    _REGISTRY["show_commands"] = lambda a: hud.tool_show_commands()
    _REGISTRY["hide_commands"] = lambda a: hud.tool_hide_commands()


_init_default_registry()

load_toolkit = tool_load_toolkit
