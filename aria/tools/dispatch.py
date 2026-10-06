"""
ARIA Tool Execution Dispatcher and Progressive Toolkit Management.
Coordinates tool invocation, argument repair, duplicate call suppression,
risky tool audit logging, HUD feedback transitions, and progressive schema payloads.
"""

import time
import re
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


# Keyword map for dynamic zero-turn toolkit auto-resolution
KEYWORD_TOOLKIT_MAP: Dict[str, List[str]] = {
    "spotify": [r"\bspotify\b", r"\bmusic\b", r"\bsongs?\b", r"\bplaylists?\b", r"\btracks?\b", r"\bdj\b", r"\bplay\b", r"\bpause\b", r"\bshuffle\b", r"\bmedia\b"],
    "windows": [r"\bwindows?\b", r"\bminimize\b", r"\bmaximize\b", r"\bfocus\b", r"\bclose\s+window\b", r"\bswitch\s+to\b"],
    "scheduler": [r"\breminds?\b", r"\breminders?\b", r"\btimers?\b", r"\bschedules?\b", r"\balarms?\b", r"\brecurring\b", r"\bnudge\b"],
    "memory_plus": [r"\bnotes?\b", r"\bjournal\b", r"\bdiary\b", r"\bbriefing\b", r"\bforget\b", r"\bremember\b"],
    "mtg": [r"\bmtg\b", r"\bmagic\b", r"\bcommander\b", r"\bedh\b", r"\bscryfall\b", r"\bcards?\b", r"\bdecks?\b", r"\bmana\b"],
    "vision": [r"\bphotos?\b", r"\bpictures?\b", r"\bcameras?\b", r"\bwebcam\b", r"\bservos?\b", r"\bhead\b", r"\bneck\b", r"\btracking\b"],
    "comms": [r"\bemai(?:ls?)\b", r"\bgmail\b", r"\bmail\b", r"\binbox\b"],
    "admin": [r"\bvolume\b", r"\bmute\b", r"\bunmute\b", r"\bbridge\b", r"\btokens?\b", r"\bkeys?\b"],
    "github": [r"\bgithub\b", r"\bgit\b", r"\brepos?(?:itory)?\b", r"\bcommit\b", r"\bpush\b"],
    "autonomy": [r"\bgoals?\b", r"\bautonom\w*", r"\bbackground\s*(?:task|job)?\b", r"\bworkers?\b", r"\bself[- ]heal\w*", r"\bdaemon\b", r"\bheartbeat\b", r"\bincidents?\b", r"\bhealth\s*(?:audit|check)?\b"],
    "routines": [r"\broutine\b", r"\bautomate\b", r"\bevery time i\b"],
    "files": [r"\bduplicates?\b", r"\bdisk\s*(?:usage|space)\b", r"\borganiz\w+\s+(?:my\s+)?(?:downl|docum|pict|phot|file|fold)"],
    "monitor": [r"\bscreen\s*watch\b", r"\bwatch\b.{0,20}\bscreen\b"],
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

    # 3b. ARIA ULTIMATE (Module 4) — approval gating. Destructive tools pause
    # for user approval unless preauthorized (approve-token replay, trusted
    # routine replay) or the mode is auto. Lazy import: no import cycle.
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
        try:
            from aria.agent.self_healing import attempt_auto_heal
            healed, healed_res, heal_msg = attempt_auto_heal(fn_name, args or {}, e, handler)
            if healed:
                r = f"{healed_res}\n[Autonomous Self-Healing: {heal_msg}]"
            else:
                r = f"[Tool Error: {e}]"
        except Exception:
            r = f"[Tool Error: {e}]"
    finally:
        if _HUD_HOOK and prev_face:
            try:
                _HUD_HOOK(prev_face)
            except Exception:
                pass

    # Autonomous Self-Healing on tool output returning recoverable failure
    if isinstance(r, str) and ("Traceback (most recent call last)" in r or "FileNotFoundError" in r or "No such file or directory" in r or "ModuleNotFoundError" in r or "unicodeescape" in r) and "[Autonomous Self-Healing:" not in r:
        try:
            from aria.agent.self_healing import attempt_auto_heal
            healed, healed_res, heal_msg = attempt_auto_heal(fn_name, args or {}, r, handler)
            if healed:
                r = f"{healed_res}\n[Autonomous Self-Healing: {heal_msg}]"
        except Exception:
            pass

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
        except Exception:
            pass
    return r, False


# ---------------- Initialize Default Built-in Tool Registry ----------------


from aria import scheduler
from aria import vision
from aria import hardware
from aria import spotify
from aria import hud

# ARIA ULTIMATE modules — top-level imports are stdlib + aria.config only,
# so these are cycle-safe here (same position as the scheduler/vision imports).
from aria import approval as approval_mod
from aria import routines as routines_mod
from aria import screenwatch as screenwatch_mod
from aria import persona as persona_mod
from aria import pricecheck as pricecheck_mod
from aria.tools import fileops as fileops_mod
from aria.tools import winctl as winctl_mod
from aria.tools import triage as triage_mod
from aria.tools import usertools as usertools_mod


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
    _REGISTRY["media_key"] = lambda a: builtins.tool_media_key(a.get("action", "") or a.get("key", "play_pause"))
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
    _REGISTRY["gemini_keys"] = lambda a: builtins.tool_gemini_keys(a.get("action", "status"), a.get("key", ""))

    # Inbox tools (phone bridge uploads)
    _REGISTRY["inbox_list"] = lambda a: builtins.tool_inbox_list()
    _REGISTRY["inbox_describe"] = lambda a: builtins.tool_inbox_describe(a.get("name", ""))
    _REGISTRY["inbox_read"] = lambda a: builtins.tool_inbox_read(a.get("name", ""))
    _REGISTRY["manage_autonomous_goal"] = lambda a: builtins.tool_manage_autonomous_goal(
        action=a.get("action", "list"), title=a.get("title", ""), description=a.get("description", ""),
        goal_id=int(a.get("goal_id", 0)), interval_s=int(a.get("interval_s", 0)), priority=int(a.get("priority", 5))
    )
    _REGISTRY["manage_background_job"] = lambda a: builtins.tool_manage_background_job(
        action=a.get("action", "list"), command=a.get("command", ""), name=a.get("name", ""), job_id=int(a.get("job_id", 0))
    )
    _REGISTRY["system_health_audit"] = lambda a: builtins.tool_system_health_audit()
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
        a.get("url", ""), a.get("env", ""), a.get("headers", ""))
    _REGISTRY["mcp_connect"] = lambda a: builtins.tool_mcp_connect(a.get("name", ""))
    _REGISTRY["mcp_disconnect"] = lambda a: builtins.tool_mcp_disconnect(a.get("name", ""))
    _REGISTRY["mcp_list_servers"] = lambda a: builtins.tool_mcp_list_servers()
    _REGISTRY["mcp_remove_server"] = lambda a: builtins.tool_mcp_remove_server(a.get("name", ""))

    # Vision tools
    _REGISTRY["take_photo"] = lambda a: vision.tool_take_photo(a.get("name", ""))
    _REGISTRY["describe_camera"] = lambda a: vision.tool_describe_camera(a.get("question", ""))
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
    _REGISTRY["approve"] = lambda a: approval_mod.tool_approve(a.get("token", ""))
    _REGISTRY["deny"] = lambda a: approval_mod.tool_deny(a.get("token", ""))
    _REGISTRY["list_pending_approvals"] = lambda a: approval_mod.tool_list_pending_approvals()
    _REGISTRY["set_approval_mode"] = lambda a: approval_mod.tool_set_approval_mode(a.get("mode", ""))
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

    # ---- ARIA ULTIMATE (Module 2): file commander ----
    _REGISTRY["file_organize"] = lambda a: fileops_mod.tool_file_organize(
        a.get("directory", ""), bool(a.get("dry_run", True)))
    _REGISTRY["file_find_advanced"] = lambda a: fileops_mod.tool_file_find_advanced(
        a.get("directory", ""), a.get("pattern", ""), float(a.get("min_size_mb", 0) or 0),
        float(a.get("max_age_days", 0) or 0), a.get("content_contains", ""))
    _REGISTRY["file_duplicates"] = lambda a: fileops_mod.tool_file_duplicates(a.get("directory", ""))
    _REGISTRY["disk_usage"] = lambda a: fileops_mod.tool_disk_usage(
        a.get("directory", ""), int(a.get("top_n", 20) or 20))

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
    except Exception:
        pass
    try:
        pricecheck_mod.ensure_pricecheck_task()
    except Exception:
        pass


_init_default_registry()

load_toolkit = tool_load_toolkit
