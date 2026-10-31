"""ARIA ULTIMATE — Module 4: Approval / safety layer.

Modes: auto (today's behavior) / confirm-risky / confirm-all.
Persisted in approval.json at the install root (aria/data_dir.py,
V20 — pre-V20 it lived in ~/ARIA). Default: auto (zero behavior change
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
import secrets
import threading
import time
from datetime import datetime, timezone

from aria import config
from aria import data_dir

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
    # MCP server setup/connect: a model-chosen command is spawned by
    # stdio_client before any MCP handshake (aria/mcp.py), and setup
    # persists the server enabled, so startup autoconnect re-runs it.
    # Gating both names closes the unapproved command-execution path
    # (ChatGPT x ARIA loop finding, 2026-10-28); list/disconnect stay
    # ungated read-only / teardown paths.
    "mcp_setup",
    "mcp_connect",
    # Self-edit + user-tool reload are code execution: edit_own_code
    # can write a .py file into aria/tools/user_tools/, and
    # reload_user_tools exec_module()s it before validating it
    # (aria/tools/usertools.py), so an ungated pair runs arbitrary
    # Python in ARIA's process with no approval. Gating all three
    # names closes the plant + trigger paths together (ChatGPT x
    # ARIA loop finding, 2026-10-29); patch_own_code is gated
    # with edit because it writes the same allowlisted files.
    "edit_own_code",
    "patch_own_code",
    "reload_user_tools",
    # trust_routine is an authorization grant, not an action: a
    # trusted routine's destructive steps replay with
    # preauthorized=True, skipping the approval gate entirely
    # (aria/routines.py run_routine_impl), so an ungated
    # trust_routine lets one model turn mint its own bypass for
    # every later replay. Gating the grant means under
    # confirm-risky / confirm-all the model call pauses for the
    # user's banner tap, and the approve-token replay executes
    # exactly that grant. untrust_routine stays ungated: revoking
    # trust only ever restores per-step approval. (ChatGPT x ARIA
    # loop finding, 2026-10-29.)
    "trust_routine",
    # launch_app is a local program launch: tool_launch_app
    # os.startfile()s any existing path or Start Menu entry
    # (aria/tools/winctl.py), so an ungated launch_app lets a
    # model call run an existing executable, script, or shortcut
    # with the user's permissions — a parallel ungated path to
    # the gated open_app_or_url. Gating it (ChatGPT x ARIA loop
    # finding, 2026-10-30, queued since pass 4) also gates
    # routine steps for free: run_routine_impl consults these
    # sets per step. The Shell Home / dashboard launcher is a
    # separate user-click path (aria/app_launcher.py) and is
    # untouched by this gate.
    "launch_app",
})

# In-memory pending approvals: token -> {token, tool, args, desc, created_ts}.
# 10-minute expiry makes disk persistence unnecessary.
_PENDING = {}

# Approve-replay marker (thread-local): approve_token() sets this
# around its execute_tool replay to (tool, args-dict-copy), so a
# handler that must never ride a generic preauthorized scope
# (run_shell_command) can tell "this exact call was just
# confirmed on screen" apart from a routine/delegated replay.
_replay_local = threading.local()

# Resolution listeners (Durable Mission Layer, 2026-10-31):
# fn(token, tool, args, approved, result) fires after a pending
# entry resolves, on approve AND deny. approve_token remains the
# only execution route — listeners only OBSERVE outcomes so a
# durable caller (aria.missions) can checkpoint the result of a
# call it parked. A listener can never change the outcome.
_RESOLUTION_LISTENERS = []


def add_resolution_listener(fn) -> None:
    if fn not in _RESOLUTION_LISTENERS:
        _RESOLUTION_LISTENERS.append(fn)


def _notify_resolution(token, entry, approved, result) -> None:
    for fn in list(_RESOLUTION_LISTENERS):
        try:
            fn(token, entry.get("tool"), dict(entry.get("args")
               or {}), approved, result)
        except Exception as _e_silent:
            config.log_silent("_notify_resolution", _e_silent)


def in_approve_replay(tool, args) -> bool:
    """True while approve_token() is replaying exactly this call
    (same tool name, equal args dict) on this thread."""
    try:
        current = getattr(_replay_local, "current", None)
        if not current:
            return False
        marked_tool, marked_args = current
        return marked_tool == tool and marked_args == dict(args or {})
    except Exception:
        return False


# Approval-control names must never gate. Since 2026-10-28 the
# resolvers (approve / deny / set_approval_mode) are not model
# tools at all — see aria/tools/dispatch.py — so this set now
# serves the read-only views that remain, plus defence in depth:
# if a resolver name were ever re-registered by mistake, gating
# it would deadlock resolution (approve -> AWAITING_APPROVAL).
NEVER_GATED = frozenset({
    "approve",
    "deny",
    "list_pending_approvals",
    "set_approval_mode",
    "get_approval_mode",
})


def _approval_file() -> str:
    # Install root via aria.data_dir (V20); the first resolve
    # in a process migrates any real legacy ~/ARIA/approval.json
    # — his approval mode survives the move.
    return data_dir.path("approval.json")


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
    payload = {"mode": m, "updated": datetime.now(timezone.utc).isoformat()}
    # Atomic write (V20): a dying write must never leave a
    # corrupt approval.json behind for get_mode to fail-soft
    # into the wrong default.
    data_dir.write_json_atomic(_approval_file(), payload)
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
    # Discovered MCP tools cannot be enumerated in the static set: their
    # names are generated when a server connects, and the remote tool's
    # effects are unknown, so the canonical classifier gates them all.
    if tool not in DESTRUCTIVE_TOOLS:
        return config.is_discovered_mcp_tool(tool)
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
        _mcp = config.parse_discovered_mcp_tool(tool)
        if _mcp:
            _server, _remote_tool = _mcp
            return (f"call MCP tool '{_remote_tool}' on server "
                    f"'{_server}' (tool '{tool}')")
        if tool == "run_shell_command":
            return f"run shell command '{a.get('command', '')}' in '{a.get('cwd', '')}'"
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
        if tool == "launch_app":
            return f"launch app '{a.get('name', '')}'"
        if tool == "move_head_servos":
            return f"move head servos (pan={a.get('pan', 90)} tilt={a.get('tilt', 45)})"
    except Exception as _e_silent:
        config.log_silent("_describe_tool", _e_silent)
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
            f"are about to do and wait for them to tap Confirm (or Deny) on "
            f"the approval banner. You cannot approve or deny it yourself — "
            f"those are not tools you have; only the user's tap resolves it.")


def purge_expired() -> int:
    """Drop pending approvals older than 10 minutes. Returns count purged."""
    now = time.time()
    expired = [t for t, e in _PENDING.items()
               if now - e.get("created_ts", 0) > APPROVAL_EXPIRY_S]
    for t in expired:
        del _PENDING[t]
    return len(expired)


def approve_token(token: str) -> str:
    """Execute the stashed call with preauthorized=True. Returns its result.

    Every outcome is logged (Alek, 2026-10-27: "I click confirm
    and it didn't do anything"): the dashboard's click path
    discards this return value, so before this logging an
    approval whose replay errored — or whose effect simply
    wasn't visible on screen — left NO trace anywhere and was
    indistinguishable from a dead button. The OPS log now
    carries the verdict for every token: executed (with the
    result), failed, or unknown/expired.
    """
    purge_expired()
    t = str(token or "").strip()
    entry = _PENDING.pop(t, None)
    if entry is None:
        config.add_log(
            f"[approval] approve FAILED token={t}: unknown or expired")
        return (f"[Approval error: unknown or expired token '{t}'. "
                f"Call list_pending_approvals() to see open requests.]")
    config.add_log(f"[approval] approved token={t} tool={entry['tool']} — executing")
    _replay_local.current = (entry["tool"], dict(entry.get("args") or {}))
    try:
        from aria.tools import dispatch as _dispatch  # lazy: no import cycle
        result, _ok = _dispatch.execute_tool(
            entry["tool"], entry["args"], preauthorized=True)
        result = str(result)
        # A replay can come back WITHOUT executing — re-parked
        # behind a fresh approval, duplicate-blocked, safe-mode
        # refused — and logging that as "executed" made a dead
        # Confirm indistinguishable from a worked one (Alek,
        # 2026-10-31). The OPS verdict names what happened.
        if result.startswith("[AWAITING_APPROVAL") or \
                result.startswith("[Duplicate call blocked") or \
                result.startswith("[Safe mode"):
            config.add_log(
                f"[approval] token={t} did NOT execute: {result[:200]}")
        else:
            config.add_log(
                f"[approval] token={t} executed: {result[:200]}")
        _notify_resolution(t, entry, True, result)
        return result
    except Exception as e:
        config.add_log(f"[approval] token={t} execution ERROR: {e}")
        _notify_resolution(
            t, entry, True,
            f"[Approval error: failed to execute approved call: {e}]")
        return f"[Approval error: failed to execute approved call: {e}]"
    finally:
        _replay_local.current = None


def deny_token(token: str) -> str:
    """Drop a pending approval without executing it."""
    purge_expired()
    t = str(token or "").strip()
    entry = _PENDING.pop(t, None)
    if entry is None:
        return (f"[Approval error: unknown or expired token '{t}'. "
                f"Call list_pending_approvals() to see open requests.]")
    config.add_log(f"[approval] denied token={t} tool={entry['tool']} — dropped")
    result = (f"Denied and dropped: {entry['desc']} (token={t}). "
              f"It was not executed.")
    _notify_resolution(t, entry, False, result)
    return result


def pending_entries() -> list:
    """Snapshot of open approvals for UI surfaces, oldest first.

    Each entry: {token, tool, desc, age_s}. Expired entries are purged
    first, so a UI polling this never shows a request that can no longer
    be resolved. Read-only: resolving still goes through approve_token /
    deny_token, so every surface shares one execution path.
    """
    purge_expired()
    now = time.time()
    return [
        {"token": e["token"], "tool": e["tool"], "desc": e["desc"],
         "age_s": int(now - e.get("created_ts", now))}
        for _t, e in sorted(_PENDING.items(),
                            key=lambda kv: kv[1].get("created_ts", 0))
    ]


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
# Only list_pending_approvals and get_approval_mode are wired into
# the tool registry (aria/tools/dispatch.py). tool_approve,
# tool_deny and tool_set_approval_mode remain as module API for
# TRUSTED callers only — the dashboard banner and the Settings
# dialog call the underlying functions on a human click, and
# tests drive the wrappers directly. They are NOT model-callable
# (removed from the registry + schemas 2026-10-28, ChatGPT pass 2
# runner-up 2: the model could approve its own pending calls and
# set its own mode to auto).


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
