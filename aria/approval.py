"""ARIA ULTIMATE — Module 4: Approval / safety layer.

Modes: auto (today's behavior) / confirm-risky / confirm-all.
Persisted in approval.json at the install root (aria/data_dir.py,
V20 — pre-V20 it lived in ~/ARIA). Default: confirm-risky.

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

import copy
import json
import os
import secrets
import threading
import time
from collections import deque
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
    "desktop_control",
    "run_python_code",
    "write_file",
    "gui_click",
    "gui_type",
    "close_window",
    "send_email",
    # Phone input acts on whatever is on the phone's screen at
    # that moment — a tap can buy, send, or delete — so it gates
    # like the desktop GUI tools, not like a read (ChatGPT x ARIA
    # loop finding, pass 25). phone_open_app stays ungated: opening
    # a named app is a discrete, describable action, while input
    # acts on unknown screen state. phone_list_apps / phone_status
    # / phone_read_screen / phone_notifications stay read-only.
    "phone_tap",
    "phone_swipe",
    "phone_type",
    "phone_key",
    "github_push_file",
    "github_create_repo",
    "drive_wheels",
    "file_organize",  # only when dry_run=false; see needs_approval()
    "manage_background_job",  # only when action=start; see needs_approval()
    "game_updater",  # only when action=update; see needs_approval()
    "open_app_or_url",
    "move_head_servos",
    # Face tracking drives the head servos (vision.py
    # face_track_loop calls send_servo_command directly), so it
    # is a physical actuation like move_head_servos — gated under
    # confirm-risky / confirm-all, and safe-mode-blocked in
    # config.SAFE_MODE_BLOCKED_TOOLS (pass 26 B2#1).
    "face_tracking",
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
    # export_routine_pack writes an arbitrary caller-chosen path:
    # export_pack makedirs()s the destination's parent and opens
    # ZipFile in 'w' mode, silently truncating whatever file was
    # already there. Ungated, one model call could clobber an
    # unrelated file with a routine pack (ChatGPT x ARIA loop
    # finding, pass 12 runner-up, fixed pass 13). Gating it means
    # under confirm-risky / confirm-all the call pauses with a
    # description naming the exact destination, and the dispatch
    # gate freezes that destination into the pending entry, so
    # the approve-token replay writes exactly the approved file.
    # Auto mode and trusted-routine replays are unchanged: they
    # skip the gate by design (preauthorized=True).
    "export_routine_pack",
    # set_backup_mirror persists a model-chosen mirror folder and
    # immediately copies the memory DB, settings, and chat history
    # there by basename, overwriting existing files (aria/
    # backup_mirror.py tool_set_backup_mirror -> mirror_backups).
    # aria_keys.json is deliberately excluded from the copy, but
    # the mirrored stores are personal data, and a synced folder
    # (Drive/OneDrive/NAS) is a route off the machine — so an
    # ungated set lets one model call exfiltrate them with no
    # banner. Gating it (ChatGPT x ARIA loop finding, pass 14)
    # follows the export_routine_pack precedent. The no-argument
    # status report stays ungated under confirm-risky: see
    # needs_approval() below (confirm-all still gates everything,
    # as it does for every tool).
    "set_backup_mirror",
    # skill_activate (Skills v1) promotes a scanned skill draft to
    # active: from then on its instructions ride the prompt catalog
    # and ARIA follows them on request. That is an authorization
    # grant in the trust_routine shape, so it gates the same way:
    # under confirm-risky / confirm-all a direct model call parks
    # for the user's approval and the approve-token replay performs
    # the activation; the gate flow in aria/tools/skill_gate.py
    # parks the same call itself after a passing scan. Auto mode is
    # unchanged (needs_approval returns False there). The handler
    # additionally enforces the passing-scan invariant, so even a
    # replayed activation cannot land an unscanned draft.
    "skill_activate",
})

# Pending approvals: token -> {token, tool, args, desc, created_ts}.
# The live store is in memory, but a serializable subset is
# persisted to approval_pending.json on every mutation and
# reloaded on first access in a new process (pass 18, 2026-11-03):
# before this, a restart silently destroyed every pending token
# while the conversation still promised the user a banner tap —
# one of the ingredients in the 2026-11-03 phantom-approval
# incident. Entries whose args cannot JSON round-trip stay
# memory-only (they simply don't survive a restart, as before).
_PENDING = {}
_pending_loaded = False

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


# Resolution journal (ChatGPT x ARIA loop pass 17, 2026-11-03):
# every way a pending entry can END — executed, blocked, errored,
# denied, expired — is recorded here with its outcome. Until this
# existed, a banner-resolved approval's result went exactly two
# places: a 200-char OPS log line, and origin.calls — a dict with
# NO reader anywhere in the codebase. The model was never told
# what its own parked call did, so after a Confirm it either went
# silent (Alek's 2026-11-03 05:14 git pull: executed, exit 0,
# never reported) or invented state from stale history (05:27:
# a "Pending approval" table for a token that never existed).
# brain.build_system_instruction renders recent entries from
# this journal — plus the live pending snapshot — as an
# authoritative system-generated state block every turn, so the
# model's picture of approvals comes from the store, not from
# conversation memory. In-memory by design (like _PENDING): a
# restart clears it, and the pending snapshot then reads "none",
# which is itself the truth after a restart.
_RESOLUTIONS = deque(maxlen=50)


def _entry_project(entry) -> object:
    """The project an approval entry belongs to, or None.

    Live entries carry it in execution_context; restored entries
    carry the persisted 'project' key."""
    if not isinstance(entry, dict):
        return None
    ctx = entry.get("execution_context") or {}
    return ctx.get("project") or entry.get("project")


def _record_resolution(tool, desc, verdict, detail="", project=None) -> None:
    """Append one resolution record. Fail-silent: journaling an
    outcome must never break resolving one.

    Pass 25: the record carries its origin project, so the
    model-facing state note can show a turn only its own
    project's resolutions. When no project is given, the current
    project at record time is used."""
    try:
        if project is None:
            try:
                from aria import workspaces
                project = workspaces.current()
            except Exception:
                project = "default"
        _RESOLUTIONS.append({
            "ts": time.time(),
            "tool": str(tool or ""),
            "desc": config.redact(str(desc or ""))[:160],
            "verdict": str(verdict or ""),
            "detail": config.redact(str(detail or ""))[:500],
            "project": str(project or "default"),
        })
    except Exception as exc:
        config.log_silent("approval.record_resolution", exc)


def recent_resolutions(limit=5, within_s=1800, project=None) -> list:
    """Resolution records from the last `within_s` seconds,
    oldest first, capped at `limit` (the newest ones win).
    Pass a project to see only that project's resolutions."""
    now = time.time()
    return [r for r in list(_RESOLUTIONS)
            if now - r["ts"] <= within_s
            and (project is None or r.get("project") == project)][-limit:]


def approval_state_note(project=None) -> str:
    """Authoritative approval state for the model's system
    instruction: the live pending snapshot (tool + desc + age —
    never tokens) plus the recent resolution journal. Returns ""
    when nothing is pending and nothing recently resolved, so
    the prompt is unchanged for users who never hit approvals.

    Pass 25: scoped to one project (the current one by default).
    A turn bound to project B must not see project A's pending
    tools or resolutions."""
    try:
        from aria import workspaces
        project = project or workspaces.current()
    except Exception:
        project = None
    try:
        entries = [e for e in pending_entries()
                   if project is None or e.get("project") == project]
    except Exception:
        entries = []
    recs = recent_resolutions(project=project)
    if not entries and not recs:
        return ""
    lines = []
    if entries:
        lines.append(
            "Pending approvals right now (waiting for the user's "
            "banner tap — only their tap resolves them):")
        for e in entries:
            lines.append(
                f"- {e['tool']}: {e['desc']} "
                f"(waiting {e['age_s']}s)")
    else:
        lines.append("Pending approvals right now: none.")
    if recs:
        lines.append("Recent approval resolutions (oldest first):")
        for r in recs:
            when = datetime.fromtimestamp(r["ts"]).strftime("%I:%M %p")
            detail = f" — result: {r['detail']}" if r["detail"] else ""
            lines.append(
                f"- {when}: {r['tool']} {r['verdict']}: "
                f"{r['desc']}{detail}")
    return "\n".join(lines)


def _notify_resolution(token, entry, approved, result) -> None:
    from aria import workspaces
    ctx = entry.get('execution_context')
    project = ctx['project'] if ctx else workspaces.current()
    for fn in list(_RESOLUTION_LISTENERS):
        try:
            with workspaces.use(project):
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


def in_approve_replay_tool(tool) -> bool:
    """True while approve_token() is replaying a call of this tool
    on this thread, whatever its args. The marker is set only for
    the call approve_token is replaying right now, so the name is
    exact in practice — prefer in_approve_replay(tool, args) when
    the raw args are available (phone_node's guard falls back to
    this when a caller drives the tool function directly)."""
    try:
        current = getattr(_replay_local, "current", None)
        return bool(current) and current[0] == tool
    except Exception:
        return False


# Desktop GUI approvals bind to the foreground window (pass 25):
# a gui_click is coordinates and a gui_type is a char count, so
# without a binding the approval is for "whatever is on screen
# whenever the user gets around to tapping Confirm". The binding
# is captured at park time and re-checked at replay; a changed
# window refuses the replay instead of clicking into it.
_GUI_BINDING_TOOLS = frozenset({"gui_click", "gui_type"})


def _gui_binding():
    """The current foreground window (winctl.foreground_window),
    or None when it cannot be read. Fail-open: a platform with no
    window concept simply has no binding to check."""
    try:
        from aria.tools import winctl as _winctl
        return _winctl.foreground_window()
    except Exception:
        return None


def _gui_binding_matches(bound, current) -> bool:
    """True when the replay-time window is the parked one.

    Title + process is the identity (an hwnd can be reused after
    the window closes); a title-less window falls back to hwnd.
    Either side unreadable never matches — a known binding that
    cannot be re-read refuses rather than guessing.
    """
    if not bound or not current:
        return False
    if bound.get("title") or bound.get("process"):
        return ((bound.get("title") or "") == (current.get("title") or "")
                and (bound.get("process") or "")
                == (current.get("process") or ""))
    return (bound.get("hwnd") is not None
            and bound.get("hwnd") == current.get("hwnd"))


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
    gate, or a pending request could never be resolved — unless the
    name belongs to a user-defined (or discovered MCP) tool, which
    runs arbitrary code: a user tool named "approve" is a user tool
    first and a resolver name second, so it gates like any other
    user tool (pass 26 B1#1). The genuine resolvers are not user
    tools, so they stay exempt.
    """
    if tool in NEVER_GATED and not (
            config.is_user_tool(tool)
            or config.is_discovered_mcp_tool(tool)):
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
    # User-defined tools are the same shape (arbitrary code in ARIA's
    # process, dynamic names) and gate the same way (pass 25).
    if tool not in DESTRUCTIVE_TOOLS:
        return (config.is_discovered_mcp_tool(tool)
                or config.is_user_tool(tool))
    if tool == "file_organize":
        # Only an explicit boolean True — or an omitted dry_run, which
        # defaults to the preview — is a preview. Any other present
        # value (null, a string, a list, a number) must pause: _as_bool
        # maps null and unknown strings to the preview default, while
        # the dispatcher used to coerce those same values to False
        # (execute), so the two seams disagreed and the move ran
        # ungated. The handler rejects such values outright, so a
        # paused-then-approved malformed call still moves nothing.
        a = args or {}
        if "dry_run" not in a:
            return False
        return a["dry_run"] is not True
    if tool == "manage_background_job":
        # list/logs/cancel are read-only; only start runs shell commands.
        return str((args or {}).get("action", "list")).strip().lower() == "start"
    if tool == "game_updater":
        # list/status read local Steam manifests; only update
        # launches steam://validate URIs — downloads and disk
        # writes Steam performs on its own schedule (pass 18;
        # the launch used to bypass every gate entirely).
        return str((args or {}).get("action", "list")).strip().lower() == "update"
    if tool == "set_backup_mirror":
        # No path and no clear = a read-only status report of the
        # current mirror folder; only set/clear change anything
        # (persisting the folder and immediately copying the
        # mirrored stores there), so only those gate.
        return bool(str((args or {}).get("path") or "").strip()) or \
            _as_bool((args or {}).get("clear"), default=False)
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
            # The first line alone can hide the payload (a comment
            # or a blank line) — show the first three non-blank
            # lines, capped, so the approver sees what runs.
            lines = [ln.strip() for ln in code.strip().splitlines()
                     if ln.strip()][:3]
            preview = " / ".join(lines)[:300]
            return f"run Python code ({len(code)} chars): {preview}"
        if tool == "write_file":
            return (f"write file '{a.get('filename', 'file.txt')}' "
                    f"({len(str(a.get('content', '')))} chars)")
        if tool == "gui_click":
            return f"click at screen coordinates ({a.get('x', 0)}, {a.get('y', 0)})"
        if tool == "gui_type":
            return f"type text ({len(str(a.get('text', '')))} chars)"
        if tool == "phone_tap":
            # The tap target is what the approver is deciding on —
            # a ref names the screen element, coordinates name the
            # spot. Never show a bare tool name for a phone tap.
            ref = str(a.get("ref", "") or "").strip()
            if ref:
                return f"tap phone element '{ref}'"
            return (f"tap phone at coordinates ({a.get('x', '?')}, "
                    f"{a.get('y', '?')})")
        if tool == "phone_swipe":
            return (f"swipe phone ({a.get('x1', '?')}, {a.get('y1', '?')}) "
                    f"→ ({a.get('x2', '?')}, {a.get('y2', '?')})")
        if tool == "phone_type":
            # The text is what gets typed into whatever field is
            # focused — show it, capped, like send_email's body.
            text = str(a.get("text", "") or "").replace("\n", " ").strip()
            ref = str(a.get("ref", "") or "").strip()
            where = f" into phone element '{ref}'" if ref else ""
            return (f"type on phone{where} "
                    f"({len(text)} chars): '{text[:200]}'")
        if tool == "phone_key":
            return f"press phone key '{a.get('key', '')}'"
        if tool == "close_window":
            return f"close window '{a.get('title', '')}'"
        if tool == "send_email":
            # The body is what actually gets sent — the old banner
            # showed only to + subject, so a benign subject could
            # carry any text at all.
            body = str(a.get("body", "")).replace("\n", " ").strip()
            preview = body[:200]
            desc = (f"send email to '{a.get('to', '')}' "
                    f"re: '{a.get('subject', '')}'")
            if preview:
                desc += f" — body: '{preview}'"
            return desc
        if tool == "mcp_setup":
            # A server setup can carry credentials (env/headers)
            # and a command to execute — never a bare tool name.
            name = str(a.get("name", "") or "").strip()
            transport = str(a.get("transport", "") or "").strip().lower()
            target = str(a.get("command", "") or a.get("url", "") or "").strip()
            extras = []
            for key, label in (("env", "env vars"), ("headers", "headers")):
                val = a.get(key)
                if val:
                    extras.append(
                        f"{len(val)} {label}" if isinstance(val, dict)
                        else label)
            extra = f" ({', '.join(extras)})" if extras else ""
            return (f"set up MCP server '{name}' "
                    f"({transport or 'stdio'})"
                    f"{' — ' + target if target else ''}{extra}")
        if tool == "edit_own_code":
            return (f"rewrite ARIA's own file '{a.get('path', '')}' "
                    f"({len(str(a.get('content', '')))} chars)")
        if tool == "patch_own_code":
            old = str(a.get("old_text", "")).strip().splitlines()
            new = str(a.get("new_text", "")).strip().splitlines()
            old1 = old[0][:80] if old else ""
            new1 = new[0][:80] if new else ""
            return (f"patch ARIA's own file '{a.get('path', '')}': "
                    f"replace '{old1}' with '{new1}'")
        if tool == "desktop_control":
            args = a.get("arguments") or {}
            keys = ", ".join(str(k) for k in args) \
                if isinstance(args, dict) else ""
            return (f"run desktop action '{a.get('action', '')}' "
                    f"on node '{a.get('node', '')}'"
                    + (f" (args: {keys})" if keys else ""))
        if tool == "github_push_file":
            return f"push file '{a.get('filepath', '')}' to repo '{a.get('repo', '')}'"
        if tool == "github_create_repo":
            return f"create GitHub repo '{a.get('name', '')}'"
        if tool == "drive_wheels":
            return (f"drive wheels (left={a.get('left', 0)} right={a.get('right', 0)} "
                    f"for {a.get('seconds', 0)}s)")
        if tool == "file_organize":
            return f"organize files in '{a.get('directory', '')}' (EXECUTES the move plan)"
        if tool == "game_updater":
            game = str(a.get("game", "") or "").strip()
            target = f"game '{game}'" if game else "all update-flagged games"
            return (f"queue a Steam update/validate for {target} "
                    f"(launches steam:// URIs; downloads may follow)")
        if tool == "open_app_or_url":
            return f"open '{a.get('target', '')}'"
        if tool == "launch_app":
            return f"launch app '{a.get('name', '')}'"
        if tool == "export_routine_pack":
            # The pending entry freezes the resolved destination (see
            # dispatch's approval gate + routines.freeze_export_args),
            # so this description names the exact file the tap will
            # write — never a placeholder the replay re-derives.
            names = str(a.get("names_csv", "") or "").strip() or "all routines"
            dest = str(a.get("dest_path", "") or "").strip() or \
                "(default routines pack in the install root)"
            overwrite = _as_bool(a.get("overwrite"), default=False)
            if overwrite:
                fate = ("REPLACING the existing file there"
                        if os.path.exists(dest) else
                        "creating it (no file there yet)")
            else:
                fate = ("refused if a file already exists there — "
                        "overwrite was not approved")
            return (f"export routine pack ({names}) to '{dest}' "
                    f"({fate})")
        if tool == "set_backup_mirror":
            if _as_bool(a.get("clear"), default=False):
                return ("stop backup mirroring (clear the mirror "
                        "folder setting)")
            dest = str(a.get("path", "") or "").strip()
            return (f"mirror backups to '{dest}' — the memory DB, "
                    f"settings, and chat history will be copied "
                    f"there now (overwriting same-named files) "
                    f"and on every later store write")
        if tool == "move_head_servos":
            return f"move head servos (pan={a.get('pan', 90)} tilt={a.get('tilt', 45)})"
        if config.is_user_tool(tool):
            # A user tool runs arbitrary code from
            # aria/tools/user_tools/ — the banner names the file's
            # own description, so the approver sees what the code
            # claims to do, never a bare tool name.
            desc = ""
            try:
                from aria.tools import usertools as _ut
                decl = _ut.get_user_tool_decl(tool)
                desc = str((decl or {}).get("description", "") or "")
            except Exception:
                desc = ""
            if desc:
                return f"run user-defined tool '{tool}' — {desc}"
            return (f"run user-defined tool '{tool}' (arbitrary code "
                    f"from aria/tools/user_tools/)")
    except Exception as _e_silent:
        config.log_silent("_describe_tool", _e_silent)
    return f"execute tool '{tool}'"


def _pending_file() -> str:
    return data_dir.path("approval_pending.json")


def _save_pending() -> None:
    """Persist the serializable subset of _PENDING (fail-silent:
    a failed save must never break the approval itself). Called
    only when the set actually changed — park, resolve, expiry —
    never on the dashboard's poll cadence."""
    try:
        entries = []
        for e in _PENDING.values():
            args = e.get("args") or {}
            try:
                json.dumps(args)
            except (TypeError, ValueError):
                continue  # cannot round-trip; stays memory-only
            # The execution context is part of the approval's
            # identity, not ambient state: a restored entry must
            # replay under the project/budget it was parked in,
            # never under whatever project is active when the
            # store is first loaded after a restart (ChatGPT x
            # ARIA loop finding, 2026-11-03 — restored approvals
            # used to inherit runtime.current(), so confirming
            # project A's parked call after switching to project
            # B executed it in B, including a mission plan for
            # B's mission #N instead of A's). Entries with no
            # recorded context are written without a project and
            # load_pending() expires them rather than guessing.
            ec = e.get("execution_context") or {}
            project = ec.get("project")
            if project is None and e.get("origin_context") is not None:
                project = getattr(e["origin_context"], "project", None)
            entry = {
                "token": e["token"], "tool": e["tool"], "args": args,
                "desc": e["desc"],
                "created_ts": e.get("created_ts", time.time()),
            }
            if e.get("gui_binding"):
                # The window binding is part of the approval's
                # identity (see approve_token): a restored entry
                # must still refuse when the window has changed.
                entry["gui_binding"] = dict(e["gui_binding"])
            if project is not None:
                entry["project"] = project
                entry["operation_id"] = ec.get("operation_id", "")
                entry["budget_scope"] = ec.get("budget_scope", "")
                entry["spend_cap"] = ec.get("spend_cap")
                # The mission envelope is part of the approval's
                # identity (pass 26 B1#2): a restored entry must
                # replay under the envelope it was parked in, not
                # under whatever (if any) is active at approve
                # time. Only JSON-serializable envelopes persist;
                # anything else stays memory-only, as with args.
                env = ec.get("envelope")
                if env is not None:
                    try:
                        entry["envelope"] = json.loads(json.dumps(env))
                    except (TypeError, ValueError):
                        pass
            entries.append(entry)
        data_dir.write_json_atomic(_pending_file(),
                                   {"version": 2, "entries": entries})
    except Exception as exc:
        config.log_silent("approval.save_pending", exc)


def load_pending() -> int:
    """Reload persisted pending approvals into _PENDING.

    Runs once per process (lazy, on first approval access).
    Entries that expired while ARIA was off are NOT restored —
    each is journaled as an 'expired' resolution instead, so the
    model learns the wait ended rather than narrating a banner
    that will never come. Restored entries replay under their
    RECORDED execution context (project, budget scope/cap,
    operation id) — exactly like an in-memory entry, which
    approve_token() already executes under its parked project.
    Only the cancel event is fresh (it cannot survive a
    process boundary). Token, tool, args, desc and the original
    created_ts (so the 10-minute expiry keeps its original
    deadline) carry over. Entries with no recorded project
    (written before this format existed) and entries whose
    project no longer exists are NOT restored — each is
    journaled as 'expired' with the reason, so no call ever
    executes in a project its approver did not park it in.
    Returns the number restored."""
    global _pending_loaded
    if _pending_loaded:
        return 0
    _pending_loaded = True
    try:
        with open(_pending_file(), encoding="utf-8") as f:
            raw = (json.load(f) or {}).get("entries") or []
    except Exception:
        return 0
    from aria import runtime
    loaded = 0
    now = time.time()
    for e in raw:
        try:
            token = str(e["token"])
            if token in _PENDING:
                continue
            created = float(e.get("created_ts") or now)
            if now - created > APPROVAL_EXPIRY_S:
                _record_resolution(
                    e.get("tool"), e.get("desc"), "expired",
                    "Expired while ARIA was not running; "
                    "it was not executed.",
                    project=e.get("project"))
                continue
            project = e.get("project")
            if not project:
                _record_resolution(
                    e.get("tool"), e.get("desc"), "expired",
                    "Not restored: parked before the origin "
                    "project was recorded, so it cannot be "
                    "proven to belong to the current project; "
                    "it was not executed.",
                    project=e.get("project"))
                continue
            try:
                from aria import workspaces
                workspaces.get(project)
            except Exception:
                _record_resolution(
                    e.get("tool"), e.get("desc"), "expired",
                    "Not restored: its origin project no "
                    "longer exists; it was not executed.",
                    project=e.get("project"))
                continue
            ctx = runtime.ExecutionContext(project=project)
            exec_ctx = {
                'project': project,
                'cancel': threading.Event(),
                'operation_id': e.get("operation_id") or "",
                'budget_scope': e.get("budget_scope") or "",
                'spend_cap': e.get("spend_cap")}
            # An old-format entry carries no envelope: it restores
            # WITHOUT one — never filled in from a latest or
            # default envelope (pass 26 B1#2).
            if isinstance(e.get("envelope"), dict):
                exec_ctx['envelope'] = copy.deepcopy(e["envelope"])
            _PENDING[token] = {
                "token": token,
                "tool": str(e["tool"]),
                "args": dict(e.get("args") or {}),
                "desc": str(e.get("desc") or ""),
                "created_ts": created,
                "gui_binding": (dict(e["gui_binding"])
                               if isinstance(e.get("gui_binding"), dict)
                               else None),
                "origin_context": ctx,
                "execution_context": exec_ctx,
            }
            loaded += 1
        except Exception as exc:
            config.log_silent("approval.load_pending_entry", exc)
    if raw:
        _save_pending()  # the file now reflects only the survivors
    if loaded:
        config.add_log(
            f"[approval] restored {loaded} pending approval(s) "
            f"parked before the restart")
    return loaded


def _ensure_pending_loaded() -> None:
    if not _pending_loaded:
        load_pending()


def request_approval(tool: str, args=None, desc=None) -> str:
    """Stash a call and return the AWAITING_APPROVAL string (does NOT execute)."""
    _ensure_pending_loaded()
    purge_expired()
    token = secrets.token_hex(4)  # 8 hex chars
    while token in _PENDING:  # paranoia; effectively impossible
        token = secrets.token_hex(4)
    if desc is None:
        desc = _describe_tool(tool, args)
    desc = config.redact(str(desc))
    # Bind a GUI approval to the foreground window it was parked
    # against: the banner names the window, and the replay refuses
    # when the user has switched away (see approve_token).
    gui_binding = None
    if tool in _GUI_BINDING_TOOLS:
        gui_binding = _gui_binding()
        if gui_binding and gui_binding.get("title"):
            desc += f" in window '{gui_binding['title']}'"
    from aria import runtime
    ctx = runtime.current()
    # The mission envelope is part of the approval's identity
    # (pass 26 B1#2): snapshot it — a later mutation of the
    # caller's envelope dict must not change the parked call,
    # and the approve-token replay must run under it, not under
    # whatever envelope (if any) is active when the user taps.
    ec = {'project': ctx.project, 'cancel': ctx.cancel,
          'operation_id': runtime.operation_id(),
          'budget_scope': ctx.budget_scope, 'spend_cap': ctx.spend_cap}
    if ctx.envelope is not None:
        ec['envelope'] = copy.deepcopy(ctx.envelope)
    _PENDING[token] = {
        "token": token,
        "tool": tool,
        "args": dict(args or {}),
        "desc": desc,
        "created_ts": time.time(),
        "gui_binding": gui_binding,
        "origin_context": ctx,
        "execution_context": ec,
    }
    _save_pending()
    # Surfaces in the OPS Log tab via add_log.
    config.add_log(f"[approval] AWAITING_APPROVAL token={token} tool={tool}: {desc}")
    return (f"[AWAITING_APPROVAL token={token}] {desc}. Tell the user what you "
            f"are about to do and wait for them to tap Confirm (or Deny) on "
            f"the approval banner. You cannot approve or deny it yourself — "
            f"those are not tools you have; only the user's tap resolves it. "
            f"It expires in {APPROVAL_EXPIRY_S} seconds if unresolved. If the "
            f"user later asks what is pending, call list_pending_approvals "
            f"and answer only from its result — never from memory of this "
            f"message.")


def purge_expired() -> int:
    """Drop pending approvals older than 10 minutes. Returns count purged.

    Each drop is journaled as an 'expired' resolution (pass 17):
    before this, an expired approval vanished silently, and
    'expired' was indistinguishable from 'denied' or 'executed'
    to everyone — user and model alike."""
    now = time.time()
    expired = [t for t, e in _PENDING.items()
               if now - e.get("created_ts", 0) > APPROVAL_EXPIRY_S]
    for t in expired:
        entry = _PENDING.pop(t, None)
        if entry is not None:
            _record_resolution(
                entry.get("tool"), entry.get("desc"), "expired",
                "No answer within 10 minutes; it was not executed.",
                project=_entry_project(entry))
    if expired:
        _save_pending()
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
    _ensure_pending_loaded()
    purge_expired()
    t = str(token or "").strip()
    entry = _PENDING.pop(t, None)
    if entry is None:
        config.add_log(
            f"[approval] approve FAILED token={t}: unknown or expired")
        return (f"[Approval error: unknown or expired token '{t}'. "
                f"Call list_pending_approvals() to see open requests.]")
    _save_pending()
    config.add_log(f"[approval] approved token={t} tool={entry['tool']} — executing")
    # A GUI approval is bound to the foreground window it was
    # parked against: if the user has switched windows since, the
    # click / typed text would land somewhere the approval never
    # covered. Refuse instead of executing — the model re-proposes
    # against the current screen. A binding that was unknown at
    # park time (no window concept on the platform) has nothing
    # to check and proceeds as before.
    bound = entry.get("gui_binding")
    if entry["tool"] in _GUI_BINDING_TOOLS and bound:
        current = _gui_binding()
        if not _gui_binding_matches(bound, current):
            if current is None:
                why = ("the foreground window could not be read at "
                       "replay time")
            else:
                why = (f"the foreground window changed since this was "
                       f"approved — it was "
                       f"'{bound.get('title') or bound.get('process') or 'untitled'}' "
                       f"and is now "
                       f"'{current.get('title') or current.get('process') or 'untitled'}'")
            reason = (f"[Approval refused: {why}, so the "
                      f"{entry['tool']} was NOT executed. Ask again "
                      f"with the current screen.]")
            config.add_log(
                f"[approval] token={t} refused: {why}")
            _record_resolution(entry["tool"], entry["desc"], "refused",
                               reason, project=_entry_project(entry))
            return reason
    _replay_local.current = (entry["tool"], dict(entry.get("args") or {}))
    try:
        from aria.tools import dispatch as _dispatch  # lazy: no import cycle
        from aria import runtime
        ctx = entry['execution_context']
        with runtime.execution('approval', project=ctx['project'], cancel=ctx['cancel'],
                               budget_scope=ctx['budget_scope'], spend_cap=ctx['spend_cap'],
                               envelope=ctx.get('envelope')):
            with runtime.operation(ctx['operation_id']):
                result, _ok = _dispatch.execute_tool(
                    entry["tool"], entry["args"], preauthorized=True)
                # Pass 25 (P2#5a): the dispatch capture hook runs
                # only for non-preauthorized calls and the parked
                # call never reached it, so an approval-gated action
                # executed via replay was lost from any open
                # recording. Capture it here, when it actually ran —
                # not when it re-parked behind a fresh approval, was
                # duplicate-blocked, or was safe-mode refused (those
                # never executed; a re-parked call is captured when
                # ITS token is approved). Inside this block the
                # workspace is the parked call's project, so the
                # recording's project check sees the right one.
                if not (str(result).startswith("[AWAITING_APPROVAL") or
                        str(result).startswith("[Duplicate call blocked") or
                        str(result).startswith("[Safe mode")):
                    try:
                        from aria import routines as _routines_mod
                        _routines_mod.maybe_capture(
                            entry["tool"], entry["args"])
                    except Exception:
                        pass
        result = str(result)
        origin = entry.get('origin_context')
        if origin is not None:
            import hashlib
            signature = entry['tool'] + ':' + hashlib.md5(
                json.dumps(entry['args'] or {}, sort_keys=True, default=str).encode(),
                usedforsecurity=False).hexdigest()
            with origin.lock:
                origin.calls[signature] = result
        # A replay can come back WITHOUT executing — re-parked
        # behind a fresh approval, duplicate-blocked, safe-mode
        # refused — and logging that as "executed" made a dead
        # Confirm indistinguishable from a worked one (Alek,
        # 2026-10-31). The OPS verdict names what happened.
        if result.startswith("[AWAITING_APPROVAL"):
            # The replay parked again behind a handler-side gate
            # (e.g. run_shell_command's own confirm): this token is
            # spent, but the call is not dead — a NEW token is now
            # pending and the banner will offer it. Journaling this
            # as a plain "did NOT execute" would tell the model the
            # action died when it is actually waiting again.
            config.add_log(
                f"[approval] token={t} did NOT execute: {result[:200]}")
            _record_resolution(entry["tool"], entry["desc"],
                               "re-parked", result,
                               project=_entry_project(entry))
            # Pass 25 (P2#7): a routine paused on this token is
            # still paused — the pause follows the new token so
            # approving THAT one resumes the routine.
            try:
                import re as _re
                _m = _re.search(r"token=([0-9a-f]{8})", result)
                if _m:
                    from aria import routines as _routines_mod
                    _routines_mod.repark_pause(t, _m.group(1))
            except Exception:
                pass
        elif result.startswith("[Duplicate call blocked") or \
                result.startswith("[Safe mode"):
            config.add_log(
                f"[approval] token={t} did NOT execute: {result[:200]}")
            _record_resolution(entry["tool"], entry["desc"],
                               "did NOT execute", result,
                               project=_entry_project(entry))
        elif result.startswith("[Tool Error:") or \
                result.startswith("[Error:"):
            # The handler ran and reported failure (dispatch turns
            # a handler exception into "[Tool Error: ...]"). The
            # OPS line below still says "executed" — it has always
            # meant only "the replay returned" — but the journal
            # the model reads must not call a failure a success.
            config.add_log(
                f"[approval] token={t} executed: {result[:200]}")
            _record_resolution(entry["tool"], entry["desc"],
                               "errored", result,
                               project=_entry_project(entry))
        else:
            config.add_log(
                f"[approval] token={t} executed: {result[:200]}")
            _record_resolution(entry["tool"], entry["desc"],
                               "executed", result,
                               project=_entry_project(entry))
            # Pass 25 (P2#7): the step just executed may be one a
            # routine was paused on — resume the routine from the
            # next step now, instead of leaving its remaining steps
            # stranded until someone re-runs the whole routine.
            try:
                from aria import routines as _routines_mod
                resume_text = _routines_mod.maybe_resume_for_token(t)
                if resume_text:
                    config.add_log(
                        f"[approval] token={t} resumed routine: "
                        f"{resume_text[:200]}")
                    result = result + "\n" + resume_text
            except Exception:
                pass
        _notify_resolution(t, entry, True, result)
        return result
    except Exception as e:
        config.add_log(f"[approval] token={t} execution ERROR: {e}")
        _record_resolution(entry["tool"], entry["desc"],
                           "errored", f"failed to execute: {e}",
                           project=_entry_project(entry))
        _notify_resolution(
            t, entry, True,
            f"[Approval error: failed to execute approved call: {e}]")
        return f"[Approval error: failed to execute approved call: {e}]"
    finally:
        _replay_local.current = None


def deny_token(token: str) -> str:
    """Drop a pending approval without executing it."""
    _ensure_pending_loaded()
    purge_expired()
    t = str(token or "").strip()
    entry = _PENDING.pop(t, None)
    if entry is None:
        return (f"[Approval error: unknown or expired token '{t}'. "
                f"Call list_pending_approvals() to see open requests.]")
    _save_pending()
    config.add_log(f"[approval] denied token={t} tool={entry['tool']} — dropped")
    _record_resolution(entry["tool"], entry["desc"], "denied",
                       "The user denied it; it was not executed.",
                       project=_entry_project(entry))
    result = (f"Denied and dropped: {entry['desc']} (token={t}). "
              f"It was not executed.")
    _notify_resolution(t, entry, False, result)
    return result


def pending_entries() -> list:
    """Snapshot of open approvals for UI surfaces, oldest first.

    Each entry: {token, tool, desc, age_s, gui_binding, project}.
    gui_binding is the foreground window the approval was parked
    against (gui_click / gui_type only; None when the platform
    has no window concept). Expired entries are purged first, so a
    UI polling this never shows a request that can no longer
    be resolved. Read-only: resolving still goes through approve_token /
    deny_token, so every surface shares one execution path.
    """
    _ensure_pending_loaded()
    purge_expired()
    now = time.time()
    return [
        {"token": e["token"], "tool": e["tool"], "desc": e["desc"],
         "age_s": int(now - e.get("created_ts", now)),
         "gui_binding": e.get("gui_binding"),
         "project": _entry_project(e)}
        for _t, e in sorted(_PENDING.items(),
                            key=lambda kv: kv[1].get("created_ts", 0))
    ]


def list_pending() -> str:
    """Human-readable list of open approval requests."""
    _ensure_pending_loaded()
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
