# Ported from Mark-LVI-ARIA actions/run_shell.py (commit
# 61a47b0e) — adapted to Agent conventions: aria.approval's
# token/banner gate instead of core.confirm, aria.outcome
# instead of core.outcome, and dispatch's preauthorized scope
# instead of trusted_scope. See LICENSES/NOTICE.md.
"""
aria/tools/shelltool.py — run a terminal command, behind the gate.

WHY THIS EXISTS
    Until now she had no shell at all. A shell command is not a
    file for a code runner and not one of the specific features
    that wrap their own subprocess calls; this tool is that
    command line.

THE GATE — AND THE ONE RULE THAT MAKES THIS TOOL DIFFERENT
    A shell command is arbitrary code with the user's full
    permissions. In the confirm modes (confirm-risky and
    confirm-all) every invocation shows the command and runs
    only on CONFIRM: the exact command and working directory
    are parked behind aria.approval's on-screen approval, and
    nothing executes until a human presses CONFIRM (or
    approves the token). In auto mode a live call runs at
    once — Alek's ruling, 2026-10-31: "make auto mean auto".
    In EVERY mode it never runs in routines/delegation (the
    rule below), and while safe mode is on the auto lane is
    closed: the call parks for a CONFIRM exactly as in the
    confirm modes, so safe mode never becomes an unattended-
    execution hole (this tool is deliberately absent from
    SAFE_MODE_BLOCKED_TOOLS — the gate is its protection).

    Deliberate rule: this tool NEVER rides a preauthorized
    scope. Every other gated action may run at once inside a
    routine replay — the trust was the user's answer, given in
    advance, for an inspectable step list. A shell step's step
    list is not inspectable in that sense: the command string
    is the whole program, and "the routine was trusted" is not
    an answer to "should THIS command run now". So this tool
    checks the scope itself and refuses inside one: a shell
    command never executes from a routine replay or a
    delegated task, trusted or not. It must be asked for again
    as a live call and confirmed in the moment. The refusal is
    a failure outcome, which halts a replay on that step rather
    than letting it walk past a step that did not run.

    The handler runs its own gate rather than relying on
    dispatch's needs_approval path: run_shell_command is not
    in DESTRUCTIVE_TOOLS, so dispatch would never hold it,
    yet a command must still park in the confirm modes. The
    handler consults the mode from the same source dispatch
    uses — approval.get_mode() — never a second store; an
    unreadable mode reads as not-auto and the call parks
    (fail-safe: the pre-ruling behaviour). In the confirm
    modes the way in is the approve-replay marker
    approval.approve_token sets while it replays the stashed,
    normalized call.

EXECUTION
    subprocess.run(command, shell=True) with output captured
    as UTF-8 (undecodable bytes replaced, never fatal). The
    result reports the exit code, then stdout, then stderr,
    labelled; the combined output is truncated to its LAST
    6000 characters — the tail is where a failing command
    explains itself — with a plain "[output truncated]" note
    when it was cut. A timeout stops the command and is
    reported as a timeout, not dressed up as output. There is
    no stdin: an interactive command cannot be answered, so it
    runs until it finishes or the timeout stops it.

Top-level imports are stdlib only: aria.approval,
aria.tools.dispatch and aria.outcome are imported lazily
inside the handler, so tests can stub the `aria` package.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# The result keeps the tail of the combined output: when a
# command floods, the last lines are the error, the summary,
# the verdict — the head is the part that scrolled away in a
# real terminal too.
OUTPUT_LIMIT = 6000

TIMEOUT_DEFAULT = 60
TIMEOUT_MIN = 5
TIMEOUT_MAX = 300


def get_base_dir() -> Path:
    """The app folder (repo root), the default working directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


# ── Lazy module / outcome seams ────────────────────────────────────

def _module(name):
    """importlib.import_module that respects sys.modules stubs
    and never raises: a missing/broken sibling reads as None."""
    import importlib
    import sys as _sys
    try:
        return importlib.import_module(name)
    except Exception:
        return _sys.modules.get(name)


def _render_facts(facts) -> str:
    parts = []
    for k, v in (facts or {}).items():
        if v is None:
            continue
        if isinstance(v, bool):
            text = "true" if v else "false"
        else:
            text = str(v).replace('"', "'").replace("\n", " ").strip()
        parts.append(f'{k}="{text}"' if (" " in text or not text)
                     else f"{k}={text}")
    return (" " + " ".join(parts)) if parts else ""


def _failed(reason, **facts) -> str:
    mod = _module("aria.outcome")
    if mod is not None:
        return mod.failed("run_shell_command", reason, **facts)
    return (f"status=failed action=run_shell_command"
            f"{_render_facts(facts)}"
            f' reason="{reason}"'
            " -- This did NOT happen. Tell the user so in English, "
            "with the reason. Do not claim any part of it succeeded.")


def _ask(question, **facts) -> str:
    mod = _module("aria.outcome")
    if mod is not None:
        return mod.ask("run_shell_command", question, **facts)
    return (f"status=ask action=run_shell_command"
            f"{_render_facts(facts)}"
            f' question="{question}"'
            " -- NOTHING was done yet. Put this question to the "
            "user in their own language and wait for their answer "
            "before calling again.")


# ── Args normalization ─────────────────────────────────────────────

def _normalize(args) -> dict:
    """The one canonical shape for a shell call:
    {command, cwd, timeout_seconds} — command stripped; cwd the
    absolute resolved directory, or '' when none was given (the
    app folder is chosen at execution / banner time, so the
    normalized form is stable); timeout an int clamped to
    5..300 (default 60). Idempotent: normalizing a normalized
    dict returns the same dict, which is what lets
    approval.approve_token's replay of the stashed normalized
    args match approval.in_approve_replay exactly."""
    params = args or {}
    command = str(params.get("command") or "").strip()
    raw_cwd = str(params.get("cwd") or "").strip()
    if raw_cwd:
        try:
            cwd = str(Path(raw_cwd).expanduser().resolve())
        except Exception:
            cwd = raw_cwd
    else:
        cwd = ""
    try:
        timeout = int(float(params.get("timeout_seconds")
                            or TIMEOUT_DEFAULT))
    except (TypeError, ValueError):
        timeout = TIMEOUT_DEFAULT
    timeout = max(TIMEOUT_MIN, min(TIMEOUT_MAX, timeout))
    return {"command": command, "cwd": cwd,
            "timeout_seconds": timeout}


# ── Execution (pure: no gate in here) ──────────────────────────────

def _execute(command: str, cwd: str, timeout: int) -> str:
    """Run the command and render the result. Separate from the
    handler so tests can exercise execution (timeout, exit code,
    truncation, cwd) without the confirmation layer in the way."""
    try:
        result = subprocess.run(
            command, shell=True, cwd=cwd or None,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return _failed(
            f"the command timed out after {timeout}s and was stopped",
            command=command)
    except Exception as e:
        return _failed(
            f"the command could not be started: {e}",
            command=command)

    sections = []
    if result.stdout:
        sections.append(f"--- stdout ---\n{result.stdout.rstrip()}")
    if result.stderr:
        sections.append(f"--- stderr ---\n{result.stderr.rstrip()}")
    body = "\n".join(sections) if sections else "(no output)"
    if len(body) > OUTPUT_LIMIT:
        body = "[output truncated]\n" + body[-OUTPUT_LIMIT:]
    return f"Exit code: {result.returncode}\n{body}"


def _safe_mode_on() -> bool:
    """aria.config.is_safe_mode(), read lazily and fail-soft.

    This read only closes the handler's auto lane; it is not
    the safe-mode enforcement itself (dispatch checks safe
    mode on its own path, with a hard import, before any
    handler runs). An unreadable config therefore reads as
    not-safe-mode here rather than inventing a block the
    enforcement layer did not make."""
    config = _module("aria.config")
    try:
        fn = getattr(config, "is_safe_mode", None) if config else None
        return bool(fn()) if callable(fn) else False
    except Exception:
        return False


# ── The handler ────────────────────────────────────────────────────

def tool_run_shell_command(args=None) -> str:
    normalized = _normalize(args)
    command = normalized["command"]
    timeout = normalized["timeout_seconds"]
    raw_cwd = normalized["cwd"]

    # Validate the working directory BEFORE any gate: a bad cwd
    # is a plain error, never a confirmation banner.
    if raw_cwd:
        if not Path(raw_cwd).is_dir():
            return _failed(
                f"the working directory does not exist: {raw_cwd}",
                command=command)
        cwd = raw_cwd
    else:
        cwd = str(get_base_dir())

    if not command:
        return _failed("no command was given")

    approval = _module("aria.approval")
    dispatch = _module("aria.tools.dispatch")

    # (a) The approve replay: approve_token stashed exactly these
    # normalized args and set the marker while replaying them —
    # this call was confirmed on screen, so it runs.
    try:
        in_replay = (approval is not None and bool(
            approval.in_approve_replay("run_shell_command", normalized)))
    except Exception:
        in_replay = False
    if in_replay:
        return _execute(command, cwd, timeout)

    # (b) Never in a routine replay or a delegated task.
    def _scope(fn_name):
        try:
            fn = getattr(dispatch, fn_name, None) if dispatch else None
            return bool(fn()) if callable(fn) else False
        except Exception:
            return False

    if _scope("in_preauthorized_scope") or _scope("in_delegated_scope"):
        return _failed(
            "shell commands never run inside a routine replay or "
            "delegated task — every command needs a fresh on-screen "
            "confirmation as a live call. Nothing was run",
            command=command)

    # (c) One confirmation at a time.
    try:
        pending = approval.pending_entries() if approval is not None else []
    except Exception:
        pending = []
    if pending:
        return _ask(
            "there is already a confirmation waiting on screen — "
            "the user has to answer that one first",
            command=command)

    # (d) Auto mode: a live call executes now (Alek's ruling,
    # 2026-10-31 — auto means auto: no pending entry, no
    # banner). The mode is approval's own get_mode(), the same
    # source dispatch's needs_approval consults; anything
    # unreadable reads as not-auto and the call parks below —
    # the pre-ruling behaviour. Safe mode closes this lane:
    # while it is on, the call parks for a human CONFIRM
    # exactly as in the confirm modes, because this tool is
    # deliberately not in SAFE_MODE_BLOCKED_TOOLS (its gate is
    # the protection) and auto must never become safe mode's
    # unattended-execution hole. The replay (a) and scope (b)
    # rules above are untouched: auto changes what a LIVE call
    # does, never who may ride a preauthorized scope.
    try:
        mode = (str(approval.get_mode() or "").strip().lower()
                if approval is not None else "")
    except Exception:
        mode = ""
    if mode == "auto" and not _safe_mode_on():
        return _execute(command, cwd, timeout)

    # (e) Park it behind the banner for a human CONFIRM.
    if approval is None:
        return _failed(
            "the approval layer is unavailable, so the command "
            "cannot be confirmed on screen. Nothing was run",
            command=command)
    desc = (f"Run shell command\n"
            f"Command: {command}\n"
            f"Working directory: {cwd}\n"
            f"Timeout: {timeout}s\n"
            f"This runs in the system shell with the signed-in "
            f"user's full permissions, exactly as if typed into a "
            f"terminal. It runs only if the user presses CONFIRM "
            f"(or approves the token).")
    return approval.request_approval(
        "run_shell_command", normalized, desc=desc)


# Alias matching the LVI action's handler name.
run_shell_command = tool_run_shell_command
