"""ARIA ULTIMATE — Module 4: Approval / safety layer.

Modes: auto (today's behavior) / confirm-risky / confirm-all.
Persisted in ~/ARIA/approval.json. Default: auto (zero behavior change
until the user opts in).

Top-level imports: stdlib + aria.config only. dispatch/brain are imported
lazily inside functions — never at module level (no import cycles).
"""

# ============================================================================
# COORDINATOR INTEGRATION — EXACT HOOK SNIPPET FOR dispatch.execute_tool
# ----------------------------------------------------------------------------
# File: aria/tools/dispatch.py, inside execute_tool().
# Insert AFTER the argument-validation block (step 3, the
# `missing_required_args` early-return) and BEFORE execution begins
# (step 6, "# 6. Execute Handler"). The import is lazy and lives inside the
# function body: approval.py never imports dispatch at module level, and
# dispatch never imports approval at module level, so no import cycle.
#
# --- BEGIN SNIPPET (paste verbatim, at function-body indentation) ---
#     # Module 4 — Approval gating (ARIA ULTIMATE)
#     if not preauthorized:
#         from aria import approval as _approval_mod
#         _approval_mod.purge_expired()
#         if _approval_mod.needs_approval(fn_name, args or {}):
#             return _approval_mod.request_approval(fn_name, args or {}), False
# --- END SNIPPET ---
#
# Notes:
#   * execute_tool already takes `preauthorized: bool = False`.
#   * approve_token() below replays the stashed call with preauthorized=True.
#   * run_routine() replays trusted routines with preauthorized=True, and
#     untrusted-routine destructive steps with preauthorized=False so they
#     hit this gate.
# ============================================================================

import json
import os
import secrets
import time
from datetime import datetime, timezone

from aria import config

MODES = ("auto", "confirm-risky", "confirm-all")
DEFAULT_MODE = "confirm-risky"
APPROVAL_EXPIRY_S = 600  # pending approvals die after 10 minutes

# Tools that can change the world outside the agent. file_organize is only
# destructive when dry_run=false (its default is dry_run=true = plan only).
# manage_background_job is only destructive on action=start (shell=True).
DESTRUCTIVE_TOOLS = frozenset({
    "run_python_code",
    "write_file",
    "gui_click",
    "gui_type",
    "close_window",
    "send_email",
    "github_push_file",
    "github_create_repo",
    "drive_wheels",
    "file_organize",  # only when dry_run=false; see needs_approval()
    "manage_background_job",  # only when action=start; see needs_approval()
    "open_app_or_url",
    "move_head_servos",
})

# In-memory pending approvals: token -> {token, tool, args, desc, created_ts}.
# 10-minute expiry makes disk persistence unnecessary.
_PENDING = {}


# Approval-control tools must never gate: gating them would make it impossible
# to resolve a pending request (approve -> AWAITING_APPROVAL -> deadlock).
# They change no world state themselves.
NEVER_GATED = frozenset({
    "approve",
    "deny",
    "list_pending_approvals",
    "set_approval_mode",
    "get_approval_mode",
})


def _approval_file() -> str:
    return os.path.join(os.path.expanduser("~"), "ARIA", "approval.json")


def get_mode() -> str:
    """Current approval mode; 'confirm-risky' on any error or first run."""
    try:
        with open(_approval_file(), encoding="utf-8") as f:
            mode = (json.load(f) or {}).get("mode", DEFAULT_MODE)
        mode = str(mode).strip().lower()
        return mode if mode in MODES else DEFAULT_MODE
    except Exception:
        return DEFAULT_MODE


def set_mode(mode: str) -> str:
    """Persist a new approval mode. Returns a human-readable confirmation."""
    m = str(mode or "").strip().lower()
    if m not in MODES:
        return (f"[Approval mode must be one of: {', '.join(MODES)}. "
                f"Current mode is still '{get_mode()}.']")
    os.makedirs(os.path.dirname(_approval_file()), exist_ok=True)
    payload = {"mode": m, "updated": datetime.now(timezone.utc).isoformat()}
    with open(_approval_file(), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    config.add_log(f"[approval] mode set to '{m}'")
    if m == "auto":
        return ("Approval mode set to 'auto'. All tools execute immediately "
                "(today's behavior).")
    if m == "confirm-risky":
        return ("Approval mode set to 'confirm-risky'. Destructive tools will "
                "pause and ask before executing.")
    return ("Approval mode set to 'confirm-all'. Every tool call will pause "
            "and ask before executing.")


def _as_bool(value, default=True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y", "on"):
        return True
    if s in ("false", "0", "no", "n", "off"):
        return False
    return default


def needs_approval(tool: str, args=None) -> bool:
    """True if this call must pause for user approval under the current mode.

    The dispatch hook checks `preauthorized` first; this function only answers
    the mode/set question. Approval-control tools (approve/deny/...) never
    gate, or a pending request could never be resolved.
    """
    if tool in NEVER_GATED:
        return False
    mode = get_mode()
    if mode == "auto":
        return False
    if mode == "confirm-all":
        return True
    # confirm-risky: only the destructive set, and file_organize only when
    # it will actually move files (dry_run=false; default is dry_run=true).
    if tool not in DESTRUCTIVE_TOOLS:
        return False
    if tool == "file_organize":
        return not _as_bool((args or {}).get("dry_run"), default=True)
    if tool == "manage_background_job":
        # list/logs/cancel are read-only; only start runs shell commands.
        return str((args or {}).get("action", "list")).strip().lower() == "start"
    return True


def _describe_tool(tool: str, args) -> str:
    """One-line human description of what the held call would do."""
    a = args or {}
    try:
        if tool == "run_python_code":
            code = str(a.get("code", ""))
            first = code.strip().splitlines()[0] if code.strip() else ""
            return f"run Python code ({len(code)} chars): {first[:100]}"
        if tool == "write_file":
            return (f"write file '{a.get('filename', 'file.txt')}' "
                    f"({len(str(a.get('content', '')))} chars)")
        if tool == "gui_click":
            return f"click at screen coordinates ({a.get('x', 0)}, {a.get('y', 0)})"
        if tool == "gui_type":
            return f"type text ({len(str(a.get('text', '')))} chars)"
        if tool == "close_window":
            return f"close window '{a.get('title', '')}'"
        if tool == "send_email":
            return f"send email to '{a.get('to', '')}' re: '{a.get('subject', '')}'"
        if tool == "github_push_file":
            return f"push file '{a.get('filepath', '')}' to repo '{a.get('repo', '')}'"
        if tool == "github_create_repo":
            return f"create GitHub repo '{a.get('name', '')}'"
        if tool == "drive_wheels":
            return (f"drive wheels (left={a.get('left', 0)} right={a.get('right', 0)} "
                    f"for {a.get('seconds', 0)}s)")
        if tool == "file_organize":
            return f"organize files in '{a.get('directory', '')}' (EXECUTES the move plan)"
        if tool == "open_app_or_url":
            return f"open '{a.get('target', '')}'"
        if tool == "move_head_servos":
            return f"move head servos (pan={a.get('pan', 90)} tilt={a.get('tilt', 45)})"
    except Exception:
        pass
    return f"execute tool '{tool}'"


def request_approval(tool: str, args=None, desc=None) -> str:
    """Stash a call and return the AWAITING_APPROVAL string (does NOT execute)."""
    purge_expired()
    token = secrets.token_hex(4)  # 8 hex chars
    while token in _PENDING:  # paranoia; effectively impossible
        token = secrets.token_hex(4)
    if desc is None:
        desc = _describe_tool(tool, args)
    desc = config.redact(str(desc))
    _PENDING[token] = {
        "token": token,
        "tool": tool,
        "args": dict(args or {}),
        "desc": desc,
        "created_ts": time.time(),
    }
    # Surfaces in the OPS Log tab via add_log.
    config.add_log(f"[approval] AWAITING_APPROVAL token={token} tool={tool}: {desc}")
    return (f"[AWAITING_APPROVAL token={token}] {desc}. Tell the user what you "
            f"are about to do and wait. Call approve(token) when they say yes, "
            f"deny(token) to cancel.")


def purge_expired() -> int:
    """Drop pending approvals older than 10 minutes. Returns count purged."""
    now = time.time()
    expired = [t for t, e in _PENDING.items()
               if now - e.get("created_ts", 0) > APPROVAL_EXPIRY_S]
    for t in expired:
        del _PENDING[t]
    return len(expired)


def approve_token(token: str) -> str:
    """Execute the stashed call with preauthorized=True. Returns its result."""
    purge_expired()
    t = str(token or "").strip()
    entry = _PENDING.pop(t, None)
    if entry is None:
        return (f"[Approval error: unknown or expired token '{t}'. "
                f"Call list_pending_approvals() to see open requests.]")
    config.add_log(f"[approval] approved token={t} tool={entry['tool']} — executing")
    try:
        from aria.tools import dispatch as _dispatch  # lazy: no import cycle
        result, _ok = _dispatch.execute_tool(
            entry["tool"], entry["args"], preauthorized=True)
        return str(result)
    except Exception as e:
        return f"[Approval error: failed to execute approved call: {e}]"


def deny_token(token: str) -> str:
    """Drop a pending approval without executing it."""
    purge_expired()
    t = str(token or "").strip()
    entry = _PENDING.pop(t, None)
    if entry is None:
        return (f"[Approval error: unknown or expired token '{t}'. "
                f"Call list_pending_approvals() to see open requests.]")
    config.add_log(f"[approval] denied token={t} tool={entry['tool']} — dropped")
    return f"Denied and dropped: {entry['desc']} (token={t}). It was not executed."


def list_pending() -> str:
    """Human-readable list of open approval requests."""
    purge_expired()
    if not _PENDING:
        return "[No pending approvals.]"
    now = time.time()
    lines = []
    for t, e in sorted(_PENDING.items(),
                       key=lambda kv: kv[1].get("created_ts", 0)):
        age = int(now - e.get("created_ts", now))
        lines.append(f"token={t} tool={e['tool']} age={age}s: {e['desc']}")
    return f"{len(lines)} pending approval(s):\n" + "\n".join(lines)


# ---------------- Tool wrappers (builtins-style) ----------------
# Registry names (wired by coordinator): approve, deny, list_pending_approvals,
# set_approval_mode, get_approval_mode.


def tool_approve(token: str) -> str:
    return approve_token(token)


def tool_deny(token: str) -> str:
    return deny_token(token)


def tool_list_pending_approvals() -> str:
    return list_pending()


def tool_set_approval_mode(mode: str) -> str:
    return set_mode(mode)


def tool_get_approval_mode() -> str:
    return f"Approval mode: {get_mode()}"
