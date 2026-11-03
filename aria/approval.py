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


def _record_resolution(tool, desc, verdict, detail="") -> None:
    """Append one resolution record. Fail-silent: journaling an
    outcome must never break resolving one."""
    try:
        _RESOLUTIONS.append({
            "ts": time.time(),
            "tool": str(tool or ""),
            "desc": config.redact(str(desc or ""))[:160],
            "verdict": str(verdict or ""),
            "detail": config.redact(str(detail or ""))[:500],
        })
    except Exception as exc:
        config.log_silent("approval.record_resolution", exc)


def recent_resolutions(limit=5, within_s=1800) -> list:
    """Resolution records from the last `within_s` seconds,
    oldest first, capped at `limit` (the newest ones win)."""
    now = time.time()
    return [r for r in list(_RESOLUTIONS)
            if now - r["ts"] <= within_s][-limit:]


def approval_state_note() -> str:
    """Authoritative approval state for the model's system
    instruction: the live pending snapshot (tool + desc + age —
    never tokens) plus the recent resolution journal. Returns ""
    when nothing is pending and nothing recently resolved, so
    the prompt is unchanged for users who never hit approvals."""
    try:
        entries = pending_entries()
    except Exception:
        entries = []
    recs = recent_resolutions()
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
            entries.append({
                "token": e["token"], "tool": e["tool"], "args": args,
                "desc": e["desc"],
                "created_ts": e.get("created_ts", time.time()),
            })
        data_dir.write_json_atomic(_pending_file(),
                                   {"version": 1, "entries": entries})
    except Exception as exc:
        config.log_silent("approval.save_pending", exc)


def load_pending() -> int:
    """Reload persisted pending approvals into _PENDING.

    Runs once per process (lazy, on first approval access).
    Entries that expired while ARIA was off are NOT restored —
    each is journaled as an 'expired' resolution instead, so the
    model learns the wait ended rather than narrating a banner
    that will never come. Restored entries get a FRESH execution
    context from the live process (the parked call replays under
    today's project/cancel/budget, exactly like a new park);
    token, tool, args, desc and the original created_ts (so the
    10-minute expiry keeps its original deadline) carry over.
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
                    "it was not executed.")
                continue
            ctx = runtime.current()
            _PENDING[token] = {
                "token": token,
                "tool": str(e["tool"]),
                "args": dict(e.get("args") or {}),
                "desc": str(e.get("desc") or ""),
                "created_ts": created,
                "origin_context": ctx,
                "execution_context": {
                    'project': ctx.project, 'cancel': ctx.cancel,
                    'operation_id': runtime.operation_id(),
                    'budget_scope': ctx.budget_scope,
                    'spend_cap': ctx.spend_cap},
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
    from aria import runtime
    ctx = runtime.current()
    _PENDING[token] = {
        "token": token,
        "tool": tool,
        "args": dict(args or {}),
        "desc": desc,
        "created_ts": time.time(),
        "origin_context": ctx,
        "execution_context": {'project': ctx.project, 'cancel': ctx.cancel,
                              'operation_id': runtime.operation_id(),
                              'budget_scope': ctx.budget_scope, 'spend_cap': ctx.spend_cap},
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
                "No answer within 10 minutes; it was not executed.")
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
    _replay_local.current = (entry["tool"], dict(entry.get("args") or {}))
    try:
        from aria.tools import dispatch as _dispatch  # lazy: no import cycle
        from aria import runtime
        ctx = entry['execution_context']
        with runtime.execution('approval', project=ctx['project'], cancel=ctx['cancel'],
                               budget_scope=ctx['budget_scope'], spend_cap=ctx['spend_cap']):
            with runtime.operation(ctx['operation_id']):
                result, _ok = _dispatch.execute_tool(
                    entry["tool"], entry["args"], preauthorized=True)
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
                               "re-parked", result)
        elif result.startswith("[Duplicate call blocked") or \
                result.startswith("[Safe mode"):
            config.add_log(
                f"[approval] token={t} did NOT execute: {result[:200]}")
            _record_resolution(entry["tool"], entry["desc"],
                               "did NOT execute", result)
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
                               "errored", result)
        else:
            config.add_log(
                f"[approval] token={t} executed: {result[:200]}")
            _record_resolution(entry["tool"], entry["desc"],
                               "executed", result)
        _notify_resolution(t, entry, True, result)
        return result
    except Exception as e:
        config.add_log(f"[approval] token={t} execution ERROR: {e}")
        _record_resolution(entry["tool"], entry["desc"],
                           "errored", f"failed to execute: {e}")
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
                       "The user denied it; it was not executed.")
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
    _ensure_pending_loaded()
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
