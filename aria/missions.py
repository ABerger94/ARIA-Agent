"""Durable missions with reviewed plans, isolated execution and action receipts.

The approved v1 plan/envelope and shared approval store remain the authority.
State lives in the current project's SQLite database. A single-flight worker
continues approved missions across project switches. Safe mode is checked
before preauthorized execution.

A receipt is committed before the mission checkpoint. Recorded effects are
not repeated after a restart or verifier rejection. An interrupted request
without a receipt is uncertain and requires reconciliation; only explicitly
replay-safe reads may retry automatically. Started/ask/unverified results do
not prove completion. A mission's model spend cap is enforced by the model
usage ledger, with owner-configured prices. Pause and cancellation prevent
new steps and preserve receipts for work already in flight.

Alek's original plan-gate design: 2026-10-31. No model self-approval path is
introduced; record_receipt is a trusted UI operation, never a model tool.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from aria import config as _config
from aria.config import add_log
from aria import memory as _memory
from aria import runtime, workspaces, action_journal
from aria.outcome import normalize, FAILED, UNVERIFIED, STARTED, ASK
from aria.tool_policy import retry_safe

MAX_STEP_RETRIES = 2
MAX_VERIFY_ATTEMPTS = 2
_OUTPUT_CAP = 4000

TERMINAL = frozenset(
    {"done", "done_unverified", "failed", "cancelled"})
# Statuses the executor may pick up and work.
RUNNABLE = frozenset({"planned", "running", "verifying"})

_TOKEN_RE = re.compile(r"token=([0-9a-f]+)")

# Extra irreversible names beyond approval.DESTRUCTIVE_TOOLS:
# handler-gated execution surfaces whose handlers park them
# behind approval in non-auto modes (shelltool / browser_agent).
_EXTRA_IRREVERSIBLE = frozenset({"run_shell_command"})

_MISSION_TOOLS = frozenset(
    {"mission_create", "mission_list", "mission_pause",
     "mission_cancel"})

_worker_lock = threading.Lock()
_worker_alive = False
_listener_ready = False
_VERIFIER = None  # tests / future: fn(goal, steps) -> bool | None


def set_verifier(fn):
    """Inject a verification judge (or None for the default
    deterministic evidence check)."""
    global _VERIFIER
    _VERIFIER = fn


# ------------------------------------------------------------------
# Storage (aria.memory's DB, DB_LOCK discipline, module-attr paths
# so test harnesses that re-point memory.DB_PATH are honored).
# ------------------------------------------------------------------

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_tables() -> None:
    with _memory.DB_LOCK:
        conn = sqlite3.connect(_memory.db_path())
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS missions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT NOT NULL,
                created TEXT NOT NULL,
                deadline TEXT,
                status TEXT NOT NULL,
                envelope TEXT NOT NULL DEFAULT '{}',
                plan_approved INTEGER NOT NULL DEFAULT 0,
                plan_token TEXT,
                verify_attempts INTEGER NOT NULL DEFAULT 0,
                summary TEXT
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS mission_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL,
                idx INTEGER NOT NULL,
                tool TEXT NOT NULL,
                args TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                output TEXT,
                retries INTEGER NOT NULL DEFAULT 0,
                approval_token TEXT
            )""")
            # Pass 27 #25: a step waiting on a delegated task or
            # background job stores the durable child handle
            # (kind + id + project) so a later tick can settle it
            # from the child's terminal outcome.
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(mission_steps)")}
            if "child_handle" not in cols:
                conn.execute("ALTER TABLE mission_steps "
                             "ADD COLUMN child_handle TEXT")
            conn.commit()
        finally:
            conn.close()


def _exec(sql: str, params=()) -> int:
    with _memory.DB_LOCK:
        conn = sqlite3.connect(_memory.db_path())
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid or 0
        finally:
            conn.close()


def _query(sql: str, params=()) -> List[tuple]:
    with _memory.DB_LOCK:
        conn = sqlite3.connect(_memory.db_path())
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


def _mission_row(mid: int) -> Optional[Dict[str, Any]]:
    rows = _query(
        "SELECT id, goal, created, deadline, status, envelope, "
        "plan_approved, plan_token, verify_attempts, summary "
        "FROM missions WHERE id = ?", (mid,))
    if not rows:
        return None
    r = rows[0]
    try:
        envelope = json.loads(r[5] or "{}")
    except Exception:
        envelope = {}
    return {"id": r[0], "goal": r[1], "created": r[2],
            "deadline": r[3], "status": r[4], "envelope": envelope,
            "plan_approved": bool(r[6]), "plan_token": r[7],
            "verify_attempts": r[8], "summary": r[9]}


def _step_rows(mid: int) -> List[Dict[str, Any]]:
    rows = _query(
        "SELECT id, idx, tool, args, status, output, retries, "
        "approval_token, child_handle FROM mission_steps "
        "WHERE mission_id = ? ORDER BY idx", (mid,))
    out = []
    for r in rows:
        try:
            args = json.loads(r[3] or "{}")
        except Exception:
            args = {}
        out.append({"id": r[0], "idx": r[1], "tool": r[2],
                    "args": args, "status": r[4], "output": r[5],
                    "retries": r[6], "approval_token": r[7],
                    "child_handle": r[8]})
    return out


def _set_mission(mid: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE missions SET {cols} WHERE id = ?",
          (*fields.values(), mid))


def _set_step(step_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE mission_steps SET {cols} WHERE id = ?",
          (*fields.values(), step_id))


# ------------------------------------------------------------------
# Policy helpers
# ------------------------------------------------------------------

def _is_irreversible(tool: str, args=None) -> bool:
    from aria import approval as _approval
    if tool in _approval.DESTRUCTIVE_TOOLS or \
            tool in _EXTRA_IRREVERSIBLE:
        return True
    # Pass 27 #6: browser_agent is action-aware — a click or a
    # press can activate an irreversible control, so those actions
    # need the mission's irreversible grant even though the tool
    # name alone is not destructive. Without this, an auto-mode
    # envelope allowing browser_agent executed a "Delete account"
    # click with an empty pre_approved_irreversible. Reads,
    # navigation, typing, scrolling and screenshots stay
    # non-irreversible, and an action-less call is treated as a
    # read (fail-open would be the click; the guard itself still
    # classifies the live label).
    if tool == "browser_agent":
        action = str((args or {}).get("action") or "").strip().lower()
        return action in ("click", "press")
    # User-defined tools run arbitrary Python in ARIA's process
    # (pass 26 B3#2): approval.needs_approval and
    # config.is_safe_mode_blocked already treat them as
    # irreversible-class; missions must too, or an allowed user
    # tool would execute preauthorized in auto mode without ever
    # appearing in pre_approved_irreversible or [irreversible].
    return _config.is_discovered_mcp_tool(tool) or \
        _config.is_user_tool(tool)


def _is_failure(out: Any) -> bool:
    """ARIA's tool-result failure convention (same markers
    taskexec watches). Approval / safe-mode strings are handled
    before this is consulted."""
    return normalize(out).status == FAILED


def _known_tools() -> set:
    try:
        from aria.tools.dispatch import get_registered_tools
        return set(get_registered_tools())
    except Exception:
        return set()


# ------------------------------------------------------------------
# Creation + the plan-approval gate
# ------------------------------------------------------------------

def create_mission(goal: str, steps: List[Dict[str, Any]], *,
                   allowed_tools: Optional[List[str]] = None,
                   pre_approved_irreversible:
                   Optional[List[str]] = None,
                   spend_cap: float = 0,
                   deadline: Optional[str] = None,
                   task_type: Optional[str] = None) -> str:
    """Insert a mission + steps and park its plan for Alek's
    one-time OK. Returns the human-readable plan text (with the
    approval store's AWAITING line appended)."""
    _ensure_tables()
    _ensure_listener()
    goal = str(goal or "").strip()
    if not goal:
        return "[Mission error: a goal is required.]"
    if not steps:
        return ("[Mission error: a mission needs at least one "
                "step.]")
    try:
        spend_cap = float(spend_cap)
        if not math.isfinite(spend_cap) or spend_cap < 0:
            raise ValueError()
    except (ValueError, TypeError):
        return '[Mission error: model spend cap must be a finite nonnegative number.]'
    known = _known_tools()
    clean_steps = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not str(s.get("tool") or ""):
            return (f"[Mission error: step {i + 1} has no tool "
                    f"name.]")
        tool = str(s["tool"]).strip()
        if tool in _MISSION_TOOLS:
            return (f"[Mission error: step {i + 1} names "
                    f"'{tool}' — missions cannot spawn or manage "
                    f"missions.]")
        if known and tool not in known:
            return (f"[Mission error: step {i + 1} names tool "
                    f"'{tool}', which does not exist.]")
        args = s.get("args") or {}
        if not isinstance(args, dict):
            return (f"[Mission error: step {i + 1} args must be "
                    f"an object.]")
        clean_steps.append({"tool": tool, "args": args})
    if allowed_tools:
        allowed = sorted({str(t).strip() for t in allowed_tools
                          if str(t).strip()})
    else:
        allowed = sorted({s["tool"] for s in clean_steps})
    envelope = {
        "allowed_tools": allowed,
        "spend_cap": float(spend_cap or 0),
        "pre_approved_irreversible": sorted(
            {str(t).strip() for t in
             (pre_approved_irreversible or []) if str(t).strip()}),
    }
    # Outcomes grader (design 2026-11-08): an explicit task
    # type, validated against the rubric registry, rides the
    # envelope so _verify can select its rubric by it. Absent
    # or unknown → the key stays out and the grader infers
    # from the step tools instead.
    if task_type:
        try:
            from aria.agent import rubrics as _rubrics
            if str(task_type).strip() in \
                    _rubrics.known_task_types():
                envelope["task_type"] = str(task_type).strip()
        except Exception:
            pass
    mid = _exec(
        "INSERT INTO missions (goal, created, deadline, status, "
        "envelope, plan_approved) VALUES (?, ?, ?, 'planned', "
        "?, 0)",
        (goal, _now(), deadline, json.dumps(envelope)))
    for i, s in enumerate(clean_steps):
        _exec(
            "INSERT INTO mission_steps (mission_id, idx, tool, "
            "args, status) VALUES (?, ?, ?, ?, 'pending')",
            (mid, i, s["tool"], json.dumps(s["args"])))
    add_log(f"[mission] #{mid} created: {goal[:80]} "
            f"({len(clean_steps)} step(s))")
    return _park_plan(mid)


def _plan_text(m: Dict[str, Any], steps: List[Dict[str, Any]]) -> str:
    env = m["envelope"]
    lines = [f"Mission #{m['id']}: {m['goal']}"]
    if m["deadline"]:
        lines.append(f"Deadline: {m['deadline']}")
    lines.append("Plan:")
    for s in steps:
        mark = " [irreversible]" if _is_irreversible(
            s["tool"], s.get("args")) else ""
        # Pass 27 #15: step args used to stop at 160 chars with
        # no indicator — a material argument could hide past the
        # cut. Show the redacted args whole up to the bound, with
        # an explicit indicator when they do not fit.
        rendered = json.dumps(
            _config.redact_data(s.get("args") or {}), default=str)
        if len(rendered) > 2000:
            rendered = (rendered[:2000] +
                        f" … [truncated: {len(rendered) - 2000} more chars]")
        lines.append(f"  {s['idx'] + 1}. {s['tool']}{mark} {rendered}")
    lines.append(
        f"Envelope: tools [{', '.join(env.get('allowed_tools') or [])}]; "
        f"model spend cap ${env.get('spend_cap', 0):g}; pre-approved "
        f"irreversible: "
        f"{', '.join(env.get('pre_approved_irreversible') or []) or 'none'}.")
    lines.append(
        "Anything outside the envelope parks for your approval, "
        "even in auto mode.")
    return "\n".join(lines)


def _park_plan(mid: int) -> str:
    """Park the plan in the EXISTING approval store as a
    mission_create replay carrying the mission id: Confirm
    replays tool_mission_create with _approve_plan set, which
    unlocks the mission; Deny cancels it (resolution listener).
    Returns plan text + the store's AWAITING line."""
    from aria import approval as _approval
    m = _mission_row(mid)
    steps = _step_rows(mid)
    plan = _plan_text(m, steps)
    replay_args = {
        "goal": m["goal"],
        "steps": json.dumps(
            [{"tool": s["tool"], "args": s["args"]} for s in steps]),
        "_mission_id": mid,
        "_approve_plan": True,
    }
    awaiting = _approval.request_approval(
        "mission_create", replay_args,
        desc=f"Approve mission #{mid} plan — {m['goal']}\n{plan}")
    tok = _TOKEN_RE.search(awaiting or "")
    if tok:
        _set_mission(mid, plan_token=tok.group(1))
    return plan + "\n" + awaiting


def approve_plan(mid: int) -> str:
    """The replayed half of the plan gate (called by
    tool_mission_create under approve_token's preauthorized
    replay). Idempotent: approving twice changes nothing."""
    _ensure_tables()
    m = _mission_row(mid)
    if m is None:
        return f"No mission #{mid} found."
    if m["status"] in TERMINAL:
        return f"Mission #{mid} is already {m['status']}."
    if not m["plan_approved"]:
        _set_mission(mid, plan_approved=1)
        add_log(f"[mission] #{mid} plan approved — execution "
                f"unlocked")
    return (f"Mission #{mid} plan approved: '{m['goal']}'. "
            f"It will run on the next scheduler tick.")


# ------------------------------------------------------------------
# Approval resolution listener — the checkpoint path for parked
# steps. approve_token remains the ONLY execution route; this
# listener only records outcomes into mission state.
# ------------------------------------------------------------------

def _ensure_listener() -> None:
    global _listener_ready
    if _listener_ready:
        return
    try:
        from aria import approval as _approval
        _approval.add_resolution_listener(_on_approval_resolved)
        _listener_ready = True
    except Exception as e:
        _config.log_silent("missions._ensure_listener", e)


def _on_approval_resolved(token, tool, args, approved, result):
    try:
        _ensure_tables()
        # A parked STEP resolved?
        rows = _query(
            "SELECT id, mission_id, idx, tool FROM mission_steps "
            "WHERE approval_token = ? AND status = 'waiting'",
            (token,))
        if rows:
            step_id, mid, idx, stool = rows[0]
            m = _mission_row(mid)
            if m is None or m["status"] in TERMINAL:
                return  # mission already settled (e.g. cancelled)
            if not approved:
                _set_step(step_id, status="denied",
                          output="Approval denied by Alek.",
                          approval_token=None)
                _finish(mid, "failed",
                        note=f"Step {idx + 1} ({stool}) approval "
                             f"was denied — mission stopped.")
                return
            result = str(result or "")
            chained = _TOKEN_RE.search(result) \
                if result.startswith("[AWAITING_APPROVAL") else None
            if chained:
                # The replay re-parked behind a handler-side gate
                # (e.g. run_shell_command in a confirm mode): the
                # step stays waiting on the NEW token.
                _set_step(step_id, approval_token=chained.group(1))
                return
            if result.startswith("[Safe mode"):
                _set_step(step_id, status="failed", output=result,
                          approval_token=None)
                _finish(mid, "failed",
                        note=f"Step {idx + 1} ({stool}) was "
                             f"safe-mode blocked after approval.")
                return
            step = next(s for s in _step_rows(mid) if s['id'] == step_id)
            if not _handle_step_result(mid, step, result):
                if m['status'] == 'waiting_approval':
                    _exec("UPDATE missions SET status='running' WHERE id=? AND status='waiting_approval'", (mid,))
            return
        # A parked PLAN resolved by Deny (Confirm replays into
        # approve_plan, which flips the flag itself).
        rows = _query(
            "SELECT id FROM missions WHERE plan_token = ?",
            (token,))
        if rows and not approved:
            mid = rows[0][0]
            m = _mission_row(mid)
            if m is not None and m["status"] not in TERMINAL:
                _finish(mid, "cancelled",
                        note="Plan denied at the approval gate — "
                             "mission never ran.")
    except Exception as e:
        _config.log_silent("missions._on_approval_resolved", e)


# ------------------------------------------------------------------
# Executor — single-flight, scheduler-tick driven.
# ------------------------------------------------------------------

def _reconcile_parked() -> None:
    """Re-park approvals the in-memory store has lost.

    The approval store is in-memory with a 10-minute expiry
    (aria.approval, by design) — a restart, or Alek simply not
    tapping for 10 minutes, kills a parked step's token while
    the mission's durable state still points at it. Without
    this, the mission would wait forever on a token no surface
    can resolve. Dead-token steps go back to pending (the
    executor re-runs the gate and parks them fresh); a dead
    plan token re-parks the plan. Live tokens are untouched.
    """
    try:
        from aria import approval as _approval
        live = {e["token"] for e in _approval.pending_entries()}
    except Exception:
        return
    rows = _query(
        "SELECT id, mission_id, approval_token FROM "
        "mission_steps WHERE status = 'waiting'")
    for step_id, mid, tok in rows:
        if tok and tok not in live:
            _set_step(step_id, status="pending",
                      approval_token=None)
            m = _mission_row(mid)
            if m is not None and \
                    m["status"] == "waiting_approval":
                _set_mission(mid, status="planned")
            add_log(f"[mission] #{mid} parked approval expired "
                    f"or was lost — step will re-park")
    rows = _query(
        "SELECT id, plan_token FROM missions WHERE "
        "plan_approved = 0 AND status = 'planned'")
    for mid, tok in rows:
        if not tok or tok not in live:
            _park_plan(mid)


def _reconcile_waiting_children(mid: int) -> None:
    """Settle steps waiting on a delegated task / background job
    from the child's terminal outcome (pass 27 #25).

    Called at the top of each _run_mission pass, before the
    worker decides the mission is blocked: a finished child
    settles its step through _handle_step_result (so a failed
    child retries / fails the mission exactly like a failed
    step, and a completed child marks the step done), and the
    loop then proceeds to the next step in the same pass. A
    child that is still running leaves the step waiting.

    A child with no record at all (the task queue is in-memory
    until pass 27 #26 persists submissions, so a restart can
    lose it; a job row can likewise be gone) is NOT certified:
    the step goes 'uncertain' and the mission 'blocked_input',
    surfacing the loss instead of completing on a submission.
    Never raises: a failed lookup leaves the step waiting.
    """
    try:
        steps = [s for s in _step_rows(mid)
                 if s["status"] == "waiting_child"]
        if not steps:
            return
        for step in steps:
            try:
                handle = json.loads(step.get("child_handle") or "{}")
            except Exception:
                handle = {}
            kind = handle.get("kind")
            project = handle.get("project")
            try:
                if kind == "delegated_task":
                    _settle_delegated_child(mid, step, handle, project)
                elif kind == "background_job":
                    _settle_job_child(mid, step, handle, project)
                else:
                    _set_step(step["id"], status="uncertain",
                              output="Started a child with no usable "
                                     "handle — its outcome cannot be "
                                     "verified.")
            except Exception as e:
                _config.log_silent(
                    "missions._reconcile_waiting_children", e)
    except Exception as e:
        _config.log_silent("missions._reconcile_waiting_children", e)


def _settle_delegated_child(mid, step, handle, project) -> None:
    """One delegated-task child: settle its mission step from the
    task's terminal status, or leave it waiting while the task
    runs. A missing task record marks the step uncertain."""
    from aria.agent import taskqueue
    from aria.agent import taskstore
    from aria import outcome as _outcome_mod
    tid = handle.get("task_id")
    if not tid:
        _set_step(step["id"], status="uncertain",
                  output="Delegated task handle has no task id — its "
                         "outcome cannot be verified.")
        return
    # Pass 28 #13: a task that blocked and was resumed runs on
    # under a NEW id — the step's handle still names the old
    # (blocked) id, and settling from it would wait forever while
    # the resumed task completes elsewhere. Follow the journal's
    # resumed_by chain to the live id and re-point the stored
    # handle, so the step settles from the task that is actually
    # running. A blocked task with no resume keeps waiting, as
    # before; the visited guard follows multi-hop chains without
    # looping on a corrupt record.
    seen = set()
    while tid and tid not in seen:
        seen.add(tid)
        rec = taskstore.get(tid)
        if rec is None or rec.get("status") != "resumed":
            break
        nxt = rec.get("resumed_by") or ""
        if not nxt:
            break
        tid = nxt
    if tid != handle.get("task_id"):
        handle = dict(handle, task_id=tid)
        _set_step(step["id"], child_handle=json.dumps(handle))
    use = workspaces.use(project) if project else contextlib.nullcontext()
    with use:
        st = taskqueue.get_queue().get_status(tid)
    if st is None:
        _set_step(step["id"], status="uncertain",
                  output=f"Delegated task {tid} has no record — it "
                         f"may have been lost on restart; its outcome "
                         f"cannot be verified.")
        return
    status = (st.get("status") or "")
    if status in ("pending", "running", "blocked"):
        return  # still under way; keep waiting
    if status == "completed":
        result = str(st.get("result") or
                     f"Delegated task {tid} completed.")
        _handle_step_result(mid, step, result)
        return
    reason = (st.get("error") or
              f"Delegated task {tid} ended {status}.")
    _handle_step_result(
        mid, step,
        _outcome_mod.failed("delegate_task", str(reason)))


def _settle_job_child(mid, step, handle, project) -> None:
    """One background-job child: settle its mission step from the
    job DB's terminal status. The DB row is reconciled first
    (the scheduler's poll does the same), so a process that
    finished since the last tick is read as finished, not
    running."""
    from aria.agent import workers as _workers_mod
    from aria import outcome as _outcome_mod
    jid = handle.get("job_id")
    if not jid:
        _set_step(step["id"], status="uncertain",
                  output="Background job handle has no job id — its "
                         "outcome cannot be verified.")
        return
    try:
        _workers_mod._poll_background_jobs()
    except Exception:
        pass
    use = workspaces.use(project) if project else contextlib.nullcontext()
    # Pass 28 #23: fetch the row by id — a list-based lookup
    # mistakes any job older than the list window for missing.
    with use:
        row = _memory.job_db_get(jid)
    if row is None:
        _set_step(step["id"], status="uncertain",
                  output=f"Background job #{jid} has no record — its "
                         f"outcome cannot be verified.")
        return
    status = (row.get("status") or "")
    if status == "running":
        return  # still under way; keep waiting
    if status == "completed":
        _handle_step_result(
            mid, step,
            f"Background job #{jid} completed (exit 0).")
        return
    code = row.get("exit_code")
    _handle_step_result(
        mid, step,
        _outcome_mod.failed(
            "manage_background_job",
            f"Background job #{jid} ended {status}"
            + (f" with exit code {code}" if code is not None
               else "")))


def executor_tick() -> bool:
    # Keep approved work running even after the desktop switches projects.
    for project in workspaces.list_projects():
        with workspaces.use(project['id']):
            import os
            if not os.path.exists(_memory.db_path()):
                _memory.init_databases()
            if _executor_tick():
                return True
    return False


def _executor_tick() -> bool:
    """Called from scheduler_loop (guarded there like every
    other subsystem tick). Starts the worker thread when a
    runnable mission exists and none is running. Returns True
    when a worker was started."""
    global _worker_alive
    _ensure_tables()
    _ensure_listener()
    _reconcile_parked()
    with _worker_lock:
        if _worker_alive:
            return False
        # Pass 28 #21: pick the oldest mission with work to do —
        # a mission whose steps are all waiting on a child must
        # not consume the pick-and-start cycle while a later
        # mission starves. Only when NO mission has work does
        # the pick fall back to the oldest runnable mission even
        # if it is waiting: its worker pass is what settles a
        # finished child (reconciliation lives in _run_mission),
        # so a waiting mission with a settled child is never
        # stranded by the skip.
        rows = _query(
            "SELECT id FROM missions WHERE plan_approved = 1 AND "
            "status IN ('planned', 'running', 'verifying', "
            "'waiting_child') AND NOT EXISTS (SELECT 1 FROM "
            "mission_steps s WHERE s.mission_id = missions.id "
            "AND s.status = 'waiting_child') "
            "ORDER BY id LIMIT 1")
        if not rows:
            rows = _query(
                "SELECT id FROM missions WHERE plan_approved = 1 "
                "AND status IN ('planned', 'running', 'verifying', "
                "'waiting_child') ORDER BY id LIMIT 1")
        if not rows:
            return False
        _worker_alive = True
        mid = rows[0][0]
    threading.Thread(target=runtime.copied_call(_mission_entry, mid),
                     daemon=True, name="aria-mission").start()
    return True


def _mission_entry(mid):
    mission = _mission_row(mid)
    # The envelope rides the execution context (pass 25): dispatch
    # enforces it on every tool call the mission makes, nested
    # calls included — the step-level check in _run_mission only
    # ever saw the top-level step tool. The mission id + goal ride
    # along so a nested park can name what it belongs to.
    env = dict(mission['envelope'] or {})
    env['mission_id'] = mid
    env['goal'] = mission['goal']
    with runtime.execution('mission', execution_id=f'mission:{workspaces.current()}:{mid}',
                           budget_scope=f'mission:{mid}',
                           spend_cap=mission['envelope'].get('spend_cap'),
                           envelope=env):
        _run_mission_guarded(mid)


def _run_mission_guarded(mid: int) -> None:
    global _worker_alive
    try:
        _run_mission(mid)
    except runtime.Cancelled:
        _finish(mid, 'cancelled', note='Cancelled; an in-flight action may still need reconciliation.')
    except Exception as e:
        try:
            add_log(f"[mission] #{mid} executor error: {e}")
            m = _mission_row(mid)
            if m is not None and m["status"] not in TERMINAL:
                _finish(mid, "failed",
                        note=f"Executor error: {e}")
        except Exception as _e_silent:
            _config.log_silent("missions._run_mission_guarded",
                               _e_silent)
    finally:
        with _worker_lock:
            _worker_alive = False


def _run_mission(mid: int) -> None:
    from aria import approval as _approval
    from aria.tools import dispatch as _dispatch
    while True:
        runtime.check_cancelled()
        m = _mission_row(mid)
        if m is None or m["status"] in TERMINAL or \
                m["status"] == "paused":
            return
        if m["deadline"]:
            try:
                if datetime.now() > datetime.strptime(
                        m["deadline"], "%Y-%m-%d %H:%M:%S"):
                    _finish(mid, "failed",
                            note="Deadline passed before the "
                                 "mission finished.")
                    return
            except ValueError:
                pass
        steps = _step_rows(mid)
        # Pass 27 #25: settle steps waiting on a child first — a
        # finished child completes/fails its step here, and the
        # loop then proceeds to the next step in the same pass.
        _reconcile_waiting_children(mid)
        steps = _step_rows(mid)
        # The reconciliation may have finished the mission (a
        # failed child fails it): re-read before picking the
        # next step, or the stale row lets the loop run on past a
        # terminal mission.
        m = _mission_row(mid)
        if m is None or m["status"] in TERMINAL or \
                m["status"] == "paused":
            return
        if any(s['status'] == 'waiting_child' for s in steps):
            _set_mission(mid, status='waiting_child')
            return
        if any(s['status'] == 'uncertain' for s in steps):
            _set_mission(mid, status='blocked_input')
            return
        if any(s['status'] == 'waiting' for s in steps):
            _set_mission(mid, status='waiting_approval')
            return
        step = next((s for s in steps
                     if s["status"] in ("pending", "running")),
                    None)
        if step is None:
            if not _verify(mid):
                continue  # a repair was queued; loop re-enters
            return
        if step["status"] == "running":
            # Interrupted mid-flight by a restart: never
            # checkpointed, so it re-runs (completed steps are
            # the ones that never replay — see module docstring).
            _set_step(step["id"], status="pending")
            step["status"] = "pending"
        _set_mission(mid, status="running")
        _set_step(step["id"], status="running")
        tool, args = step["tool"], step["args"]
        env = m["envelope"]

        # Safe mode first: preauthorized dispatch skips this
        # check by design, so the executor owns it for every
        # path a mission step can take.
        if _config.is_safe_mode() and \
                _config.is_safe_mode_blocked(tool):
            _set_step(step["id"], status="failed",
                      output=f"[Safe mode: '{tool}' is disabled.]")
            _finish(mid, "failed",
                    note=f"Step {step['idx'] + 1} ({tool}) is "
                         f"hard-blocked in safe mode.")
            return

        allowed = tool in (env.get("allowed_tools") or [])
        pre_ok = tool in (env.get("pre_approved_irreversible")
                          or [])
        may_run = allowed and (not _is_irreversible(tool, args)
                               or pre_ok)
        mode = _approval.get_mode()
        # One logical action id survives restarts and approval replay. Only
        # explicitly replay-safe reads get a new id for an intentional retry.
        operation_id = f"mission:{mid}:step:{step['id']}"
        if retry_safe(tool):
            operation_id += f":retry:{step['retries']}:verify:{m['verify_attempts']}"
        with runtime.operation(operation_id):
            if may_run and mode == "auto":
                # Inside the envelope + auto: run without asking.
                result = _execute_step(_dispatch, tool, args,
                                       preauthorized=True)
            elif may_run:
                # Inside the envelope under a confirm mode: the
                # dispatcher's own gate decides — non-destructive
                # runs, destructive parks in the shared store.
                result = _execute_step(_dispatch, tool, args,
                                       preauthorized=False)
            else:
                # Outside the envelope (or an irreversible action
                # not named in it): park in the shared store in
                # EVERY mode — the envelope fork Alek approved.
                # Pass 28 #4: park through the COMMON revision
                # freezer (dispatch._freeze_park_args), not local
                # per-tool helpers — the local path froze only
                # trust_routine and skill_activate, so an export,
                # user tool, mcp_connect, or discovered MCP tool
                # parked UNFROZEN and its replacement rode the
                # token. A freeze failure refuses to park at all:
                # a revisionless approval must never be stashed.
                park_args = args
                park_error = None
                try:
                    park_args = _dispatch._freeze_park_args(tool, args)
                except Exception as e:
                    park_error = (f"{tool} cannot be parked: "
                                  f"{e}")
                if park_error:
                    _set_step(step["id"], status="failed",
                              output=park_error)
                    _finish(mid, "failed", note=park_error)
                    return
                result = _approval.request_approval(
                    tool, park_args,
                    desc=f"Mission #{mid} step {step['idx'] + 1}: "
                         f"{_approval._describe_tool(tool, park_args)} "
                         f"(outside the mission's envelope — goal: "
                         f"{m['goal']})")
        if _handle_step_result(mid, step, result):
            return  # parked / blocked / finished — stop here
        # else the step completed (or was re-queued for retry);
        # loop to the next step.


def _execute_step(dispatch, tool: str, args: dict, *,
                  preauthorized: bool) -> str:
    # Fresh dispatch turn context per step (taskexec's pattern):
    # the per-turn duplicate guard must not eat a mission step
    # that repeats a call made in conversation.
    dispatch.reset_turn_state()
    result, _ok = dispatch.execute_tool(
        tool, args or {}, preauthorized=preauthorized)
    return str(result)


_CHILD_HANDLE_RE = {
    "delegate_task": (r"task_id=([A-Za-z0-9_-]+)", "task_id"),
    # Pass 28 #19: task_resume starts the same kind of child (a
    # delegated task under a new id) — a mission step that resumes
    # a blocked child must wait on it, not certify the submission
    # or strand the step as uncertain.
    "task_resume": (r"task_id=([A-Za-z0-9_-]+)", "task_id"),
    "manage_background_job": (r"job_id=(\d+)", "job_id"),
}


def _parse_child_handle(tool: str, result: str):
    """(kind, handle) for a STARTED result carrying a durable
    child handle, else (None, None). Pass 27 #25: delegate_task
    and manage_background_job return outcome.started with the
    child id + project as facts; the mission parses them back
    here so a later tick can settle the step from the child's
    terminal outcome. The project rides along because the child
    stores are project-scoped (taskqueue.get_status and the job
    DB both filter by the current project)."""
    spec = _CHILD_HANDLE_RE.get(tool or "")
    if spec is None:
        return None, None
    pattern, key = spec
    m = re.search(pattern, str(result or ""))
    if not m:
        return None, None
    p = re.search(r"project=([A-Za-z0-9_-]+)", str(result or ""))
    handle = {"kind": ("delegated_task" if tool in ("delegate_task",
                                                    "task_resume")
                       else "background_job"),
              key: (int(m.group(1)) if key == "job_id"
                    else m.group(1)),
              "project": p.group(1) if p else None}
    return handle["kind"], handle


def _handle_step_result(mid: int, step: Dict[str, Any],
                        result: str) -> bool:
    """Record one step outcome. Returns True when the worker
    must stop (parked, blocked, or mission finished)."""
    idx = step["idx"]
    if result.startswith("[AWAITING_APPROVAL"):
        tok = _TOKEN_RE.search(result)
        _set_step(step["id"], status="waiting",
                  approval_token=tok.group(1) if tok else None)
        _set_mission(mid, status="waiting_approval")
        add_log(f"[mission] #{mid} step {idx + 1} parked for "
                f"approval")
        return True
    if result.startswith("[Safe mode"):
        _set_step(step["id"], status="failed", output=result)
        _finish(mid, "failed",
                note=f"Step {idx + 1} ({step['tool']}) was "
                     f"safe-mode blocked.")
        return True
    if result.startswith("NEED_INPUT:"):
        question = result[len("NEED_INPUT:"):].strip() or \
            "a step needs user input"
        _set_step(step["id"], status="pending")
        _set_mission(mid, status="blocked_input")
        add_log(f"[mission] #{mid} blocked on input: "
                f"{question[:80]}")
        return True
    structured = normalize(result)
    if structured.status == STARTED:
        # Pass 27 #25: a step that started a child (delegated
        # task / background job) waits on that child — it is NOT
        # done, and it is NOT 'uncertain' either: the child handle
        # is stored and a later tick settles the step from the
        # child's terminal outcome. A STARTED result with no
        # handle falls through to the generic branch below.
        kind, handle = _parse_child_handle(step["tool"], result)
        if kind is not None:
            _set_step(step["id"], status="waiting_child",
                      child_handle=json.dumps(handle),
                      output=str(structured)[:_OUTPUT_CAP])
            _set_mission(mid, status="waiting_child")
            add_log(f"[mission] #{mid} step {idx + 1} started a "
                    f"{kind} ({handle}); waiting for its terminal "
                    f"outcome")
            return True
    if structured.status in (UNVERIFIED, STARTED, ASK):
        _set_step(step['id'], status='uncertain', output=str(structured)[:_OUTPUT_CAP])
        _set_mission(mid, status='blocked_input')
        add_log(f'[mission] #{mid} needs evidence for step {idx + 1}; it will not be replayed')
        return True
    if _is_failure(result):
        retries = int(step["retries"] or 0) + 1
        if retry_safe(step["tool"]) and retries <= MAX_STEP_RETRIES:
            _set_step(step["id"], status="pending",
                      retries=retries, output=result[:_OUTPUT_CAP])
            add_log(f"[mission] #{mid} step {idx + 1} failed "
                    f"(attempt {retries}/{MAX_STEP_RETRIES}): "
                    f"{result[:120]}")
            return False  # loop retries the step
        _set_step(step["id"], status="failed", retries=retries,
                  output=result[:_OUTPUT_CAP])
        _finish(mid, "failed",
                note=f"Step {idx + 1} ({step['tool']}) failed "
                     f"after {retries} attempt(s): "
                     f"{result[:200]}")
        return True
    _set_step(step["id"], status="done",
              output=result[:_OUTPUT_CAP], approval_token=None)
    add_log(f"[mission] #{mid} step {idx + 1} done")
    return False


# ------------------------------------------------------------------
# Verification + delivery summary
# ------------------------------------------------------------------

# The last outcome grade per mission (Outcomes/Grader upgrade,
# design 2026-11-08). Pass 36 #21: this used to be keyed by the
# bare numeric mission id and read by _build_summary even when
# the current grade was disabled or unavailable — a later
# attempt, or another project with the same id, could publish
# old criterion citations. It is now write-only legacy: _verify
# still stores here (EXEC owns that line) but nothing reads it.
# The live handoff is _CURRENT_GRADE below.
_LAST_GRADE: Dict[int, dict] = {}

# Same-call-stack handoff from _outcome_grade to _finish,
# keyed by (database path, mission id) so a grade can never
# leak across projects, and consumed (popped) by _finish when
# it builds the terminal summary. _outcome_grade refreshes or
# deletes the entry on every verify, so a cannot_grade / off
# run leaves no stale grade behind. The direct path is the
# grade= argument on _finish/_build_summary; this slot exists
# only until _verify passes its grade_result through directly.
_CURRENT_GRADE: Dict[tuple, dict] = {}


def _settle_mission_delegates(mid: int,
                              steps: List[Dict[str, Any]]) -> None:
    """Pass 36 #28: before grading, write a settlement record
    on each delegated-child step's journal receipt from the
    child's terminal task-journal status, so the grader sees
    the completed child instead of a STARTED receipt. Only
    terminal child states settle; the receipt's state is never
    rewritten. Best-effort; never raises."""
    try:
        from aria.agent import taskstore as _ts
        for s in steps:
            handle = s.get("child_handle")
            if isinstance(handle, str):
                try:
                    handle = json.loads(handle or "{}")
                except Exception:
                    continue
            if not isinstance(handle, dict):
                continue
            if handle.get("kind") != "delegated_task":
                continue
            tid = handle.get("task_id")
            if not tid:
                continue
            project = handle.get("project")
            try:
                use = workspaces.use(project) if project \
                    else contextlib.nullcontext()
            except Exception:
                use = contextlib.nullcontext()
            with use:
                child = _ts.get(str(tid))
            status = (child or {}).get("status") or ""
            if status in ("completed", "failed", "cancelled"):
                action_journal.settle(
                    f"mission:{mid}:step:{s['id']}", str(tid),
                    status, (child or {}).get("result") or
                    (child or {}).get("error") or "")
    except Exception as e:
        add_log(f"[mission] #{mid} delegate settle failed ({e})")


def _outcome_grade(mid: int, m: Dict[str, Any],
                   steps: List[Dict[str, Any]]) -> Optional[dict]:
    """The shared outcome grader's verdict for this mission,
    or None when the grader is off or could not grade — the
    caller then keeps the legacy verify outcome exactly.
    Probes: the action-journal receipt of every non-replay-safe
    step (its mission operation id is deterministic) plus the
    grader's file probes. A decisive grade is handed to
    _finish via _CURRENT_GRADE (pass 36 #21); a None outcome
    clears any earlier grade for this mission, so _finish can
    never render a stale one."""
    try:
        from aria.agent import grader as _grader
        if not _grader.outcomes_enabled():
            _CURRENT_GRADE.pop((_memory.db_path(), mid), None)
            return None
        # Pass 36 #28: settle delegate receipts from their
        # children's terminal outcomes first, so the probes
        # below carry the settlement.
        _settle_mission_delegates(mid, steps)
        journal_ids = {}
        for s in steps:
            if not retry_safe(s["tool"]):
                journal_ids[s["idx"] + 1] = \
                    f"mission:{mid}:step:{s['id']}"
        # Pass 36 #29: a mission records no worker model
        # identity (steps run through dispatch, not a traced
        # planning call), so worker_model stays None and the
        # grader labels provenance unknown — never presumed.
        # Pass 36 #31: the grade is bounded by the mission's
        # remaining deadline, capped at 300 s so one slow grade
        # can never hold the single mission worker past a useful
        # window; a mission with no deadline gets the same 300 s
        # default. Expiry is cannot_grade and the caller keeps
        # its legacy verify outcome.
        import time as _time
        deadline = None
        try:
            if m.get("deadline"):
                remaining = (datetime.strptime(
                    m["deadline"], "%Y-%m-%d %H:%M:%S")
                    - datetime.now()).total_seconds()
            else:
                remaining = 300.0
            deadline = _time.monotonic() + max(
                0.0, min(remaining, 300.0))
        except Exception:
            deadline = None
        # Pass 36 #32: the plan's check-step list rides into
        # the bundle, so the code rubric's checked_after_edit
        # criterion is judged against the steps the plan
        # actually included.
        result = _grader.grade(
            m["goal"], steps,
            task_type=(m.get("envelope") or {}).get("task_type"),
            worker_model=None,
            deadline=deadline,
            check_steps=_grader.identify_check_steps(steps),
            probes=_grader.build_probes(
                _grader.normalize_steps(steps),
                journal_ids=journal_ids))
        if result["verdict"] == "cannot_grade":
            _CURRENT_GRADE.pop((_memory.db_path(), mid), None)
            return None
        _CURRENT_GRADE[(_memory.db_path(), mid)] = result
        return result
    except Exception as e:
        _CURRENT_GRADE.pop((_memory.db_path(), mid), None)
        add_log(f"[mission] #{mid} outcome grader errored ({e}); "
                f"legacy verify outcome kept")
        return None


def _grade_targets(result: dict,
                   steps: List[Dict[str, Any]]) -> List[dict]:
    """Repair targets from the grade's failed criteria /
    present failure cases, mapped by rubric scope. Unmapped
    ids contribute nothing — the caller keeps its legacy
    targeting for those."""
    try:
        from aria.agent import rubrics as _rubrics
        rubric = _rubrics.get_rubric(result.get("rubric_id"))
        targets: List[dict] = []
        for fid in result.get("failed_criteria") or []:
            for s in _rubrics.steps_in_scope(rubric, fid, steps):
                if s not in targets:
                    targets.append(s)
        return targets
    except Exception:
        return []


def _verify(mid: int) -> bool:
    """Verify pass. Returns True when the mission reached a
    terminal state; False when a repair was queued and the
    worker loop should re-enter."""
    m = _mission_row(mid)
    steps = _step_rows(mid)
    if m['status'] in TERMINAL or m['status'] == 'paused':
        return True
    _set_mission(mid, status="verifying")
    verdict: Optional[bool] = None
    if _VERIFIER is not None:
        try:
            verdict = _VERIFIER(m["goal"], steps)
        except Exception as e:
            add_log(f"[mission] #{mid} verifier errored ({e}); "
                    f"falling back to evidence check")
            verdict = None
    if verdict is None:
        bad = [s for s in steps
               if s["status"] != "done" or not s["output"]
               or _is_failure(s["output"])]
        verdict = not bad
    else:
        bad = [s for s in steps if s["status"] != "done"]
        failed = [s for s in steps
                  if s["status"] == "done" and _is_failure(s["output"])]
        if failed:
            # An injected verifier's word does not override the
            # recorded evidence (pass 18): a step whose own
            # output is a failure marker cannot certify the
            # mission done — the fallback branch above has
            # always checked this; the verifier branch skipped
            # it, so a confident verifier could paper over a
            # failed step. The failed steps become the repair
            # targets; retry-safety below routes effect tools
            # to done_unverified instead of a blind re-run.
            bad = bad + failed
            verdict = False
    # Outcomes grader (design 2026-11-08): a rubric grade
    # supplements — never replaces — the verdict above. A
    # grader FAIL forces the repair path with criteria-mapped
    # targets unioned into `bad`; a grader PASS changes
    # nothing (the deterministic evidence check and any
    # injected verifier still gate, so the pass-18 invariant
    # stands: a confident grade cannot certify a step whose
    # own output is a failure marker). cannot_grade / setting
    # off → the legacy outcome is untouched.
    grade_result = _outcome_grade(mid, m, steps)
    if grade_result is not None:
        _LAST_GRADE[mid] = grade_result
        if grade_result["verdict"] == "fail":
            verdict = False
            for s in _grade_targets(grade_result, steps):
                if s not in bad:
                    bad.append(s)
    if verdict:
        _finish(mid, "done")
        return True
    attempts = int(m["verify_attempts"] or 0) + 1
    _set_mission(mid, verify_attempts=attempts)
    if attempts <= MAX_VERIFY_ATTEMPTS:
        # Repair: re-run the offending steps (or, when an
        # injected verifier rejected wholesale, the last step —
        # its output is the mission's final evidence).
        targets = bad or steps[-1:]
        if any(not retry_safe(s['tool']) for s in targets):
            _finish(mid, 'done_unverified', note='Verification did not pass. '
                    'Actions with effects were preserved; check their receipts instead of repeating them.')
            return True
        for s in targets:
            _set_step(s["id"], status="pending", output=None)
        _set_mission(mid, status="running")
        add_log(f"[mission] #{mid} verify failed — repair "
                f"attempt {attempts}/{MAX_VERIFY_ATTEMPTS}")
        return False
    _finish(mid, "done_unverified",
            note="Verification did not pass after "
                 f"{MAX_VERIFY_ATTEMPTS} repair attempt(s) — "
                 "the recorded step outputs do not prove the "
                 "goal was accomplished.")
    return True


def _build_summary(m: Dict[str, Any],
                   steps: List[Dict[str, Any]],
                   note: str = "",
                   grade: Optional[dict] = None) -> str:
    """Pass 36 #21: the grade rendered here is the one passed
    in — the current verification's result — never a lookup in
    a process-wide map. A mission that was never graded in this
    verify renders no grade at all."""
    done = sum(1 for s in steps if s["status"] == "done")
    lines = [f"Asked: {m['goal']}",
             f"Done: {done}/{len(steps)} step(s) completed "
             f"— status: {m['status']}."]
    lines.append("Evidence:")
    for s in steps:
        out = (s["output"] or "").strip().replace("\n", " ")
        lines.append(f"  {s['idx'] + 1}. {s['tool']} "
                     f"[{s['status']}]"
                     f"{': ' + out[:200] if out else ''}")
    if grade:
        # The graded report shape (design 2026-11-08): the
        # grade line, then per-criterion verdicts with the
        # grader's cited evidence, then the failure cases.
        try:
            from aria.agent import grader as _grader
            lines.append(_grader.format_grade_note(grade))
        except Exception:
            lines.append(
                f"Grade: {grade.get('rubric_id')} "
                f"v{grade.get('rubric_version')} "
                f"{grade.get('verdict')}")
        for c in grade.get("criteria") or []:
            ev = f" — {c['evidence']}" if c.get("evidence") else ""
            lines.append(f"  criterion {c['id']}: "
                         f"{c['verdict']}{ev}")
        for fc in grade.get("failure_cases") or []:
            ev = (f" — {fc['evidence']}"
                  if fc.get("evidence") else "")
            state = "PRESENT" if fc.get("present") else "absent"
            lines.append(f"  failure case {fc['id']}: {state}{ev}")
    parked = [f"step {s['idx'] + 1} ({s['tool']}): {s['status']}"
              for s in steps
              if s["status"] in ("waiting", "denied", "failed",
                                  "cancelled")]
    lines.append("Parked/failed: "
                 + ("; ".join(parked) if parked else "none"))
    if note:
        lines.append(note)
    return "\n".join(lines)


def _finish(mid: int, status: str, note: str = "",
            grade: Optional[dict] = None) -> None:
    """Pass 36 #21: the grade committed alongside the status
    is the current verification's result. Callers pass it
    directly (grade=); until _verify does, a terminal
    done/done_unverified finish consumes the _CURRENT_GRADE
    handoff _outcome_grade just wrote for this mission — the
    pop means no later finish can re-render it, and the
    (database, mission) key means no other project's grade can
    leak in. Any other status renders no grade."""
    m = _mission_row(mid)
    if m is None or m["status"] in TERMINAL:
        return
    if grade is None and status in ("done", "done_unverified"):
        grade = _CURRENT_GRADE.pop((_memory.db_path(), mid), None)
    m["status"] = status
    steps = _step_rows(mid)
    _set_mission(mid, status=status,
                 summary=_build_summary(m, steps, note, grade))
    add_log(f"[mission] #{mid} {status}: {m['goal'][:80]}")


# ------------------------------------------------------------------
# Pause / cancel / views
# ------------------------------------------------------------------

def pause_mission(mid: int) -> str:
    """Pause an active mission (the executor stops between
    steps). Pausing a paused mission resumes it — the four
    mission tools carry no separate resume, by design."""
    _ensure_tables()
    m = _mission_row(mid)
    if m is None:
        return f"No mission #{mid} found."
    if m["status"] in TERMINAL:
        return f"Mission #{mid} is already {m['status']}."
    if m["status"] == "paused":
        _set_mission(mid, status="running")
        return f"Mission #{mid} resumed: '{m['goal']}'."
    _set_mission(mid, status="paused")
    return (f"Mission #{mid} paused: '{m['goal']}'. "
            f"It will stop between steps.")


def cancel_mission(mid: int) -> str:
    _ensure_tables()
    m = _mission_row(mid)
    if m is None:
        return f"No mission #{mid} found."
    if m["status"] in TERMINAL:
        return f"Mission #{mid} is already {m['status']}."
    steps = _step_rows(mid)
    waiting = [s for s in steps if s["status"] == "waiting"
               and s["approval_token"]]
    runtime.cancel_work(f'mission:{workspaces.current()}:{mid}')
    # Delegated children outlive the mission's own cancel event
    # unless stopped here (pass 26 B2#5): each child runs under
    # its own execution id, so cancel_work above never reaches
    # them. Only cancel when a queue instance exists — building
    # one from a cancel call would start a worker for nothing.
    stopped = 0
    try:
        from aria.agent import taskqueue as _taskqueue_mod
        _queue = _taskqueue_mod._queue
        if _queue is not None:
            stopped = _queue.cancel_children_of_mission(mid)
    except Exception as e:
        _config.log_silent("missions.cancel_mission.children", e)
    # Background jobs outlive the mission the same way (pass 28
    # #24): a step that started a job carries its child_handle —
    # kill the job in the handle's project, or it runs on after
    # the mission is dead.
    stopped_jobs = 0
    try:
        from aria.agent import workers as _workers_mod
        for s in steps:
            try:
                handle = json.loads(s.get("child_handle") or "{}")
            except Exception:
                handle = {}
            if handle.get("kind") != "background_job":
                continue
            jid = handle.get("job_id")
            if not jid:
                continue
            project = handle.get("project")
            use = workspaces.use(project) if project \
                else contextlib.nullcontext()
            try:
                with use:
                    if _workers_mod.cancel_background_job(int(jid)):
                        stopped_jobs += 1
            except Exception as e:
                _config.log_silent("missions.cancel_mission.job", e)
    except Exception as e:
        _config.log_silent("missions.cancel_mission.jobs", e)
    # Deferred work created under the mission's origin policy
    # (pass 28 #24): schedules, triggers and autonomous goals
    # whose stored policy names this mission would otherwise
    # keep firing under the dead mission's envelope and cap.
    revoked = _revoke_deferred_for_mission(mid)
    _finish(mid, "cancelled",
            note="Cancelled by Alek — remaining steps will "
                 "not run.")
    for s in steps:
        if s["status"] in ("pending", "running", "waiting",
                           "uncertain", "waiting_child"):
            _set_step(s["id"], status="cancelled",
                      approval_token=None)
    # Clear any parked approval from every surface (the
    # listener sees the mission already terminal and stands
    # down).
    if waiting:
        try:
            from aria import approval as _approval
            for s in waiting:
                _approval.deny_token(s["approval_token"])
        except Exception as e:
            _config.log_silent("missions.cancel_mission", e)
    return (f"Mission #{mid} cancelled: '{m['goal']}'. "
            f"Stopped {stopped} delegated child task(s), "
            f"{stopped_jobs} background job(s); revoked "
            f"{revoked['schedules']} schedule(s), "
            f"{revoked['triggers']} trigger(s), "
            f"{revoked['goals']} autonomous goal(s).")


def _policy_mission_id(policy) -> Optional[int]:
    """The mission_id a stored origin policy names, or None.
    Policies arrive as dicts (triggers) or JSON text (schedules,
    goals); a legacy record with no policy names no mission."""
    if isinstance(policy, str):
        try:
            policy = json.loads(policy)
        except Exception:
            return None
    if not isinstance(policy, dict):
        return None
    try:
        mid = policy.get("mission_id")
        return int(mid) if mid is not None else None
    except (TypeError, ValueError):
        return None


def _revoke_deferred_for_mission(mid: int) -> Dict[str, int]:
    """Revoke the schedules, triggers and autonomous goals a
    mission created under its origin policy (pass 28 #24).
    Returns the revoked counts per store. Never raises: a
    failed sweep must not break the cancel itself."""
    revoked = {"schedules": 0, "triggers": 0, "goals": 0}
    try:
        from aria import scheduler as _sched_mod
        for tid, policy in _sched_mod.sched_list_with_policy():
            if _policy_mission_id(policy) == mid:
                if _sched_mod.sched_cancel(tid):
                    revoked["schedules"] += 1
    except Exception as e:
        _config.log_silent("missions.cancel_mission.schedules", e)
    try:
        from aria import triggers as _trig_mod
        for trig in _trig_mod.load_triggers():
            if _policy_mission_id(trig.get("policy")) == mid:
                if _trig_mod.delete_trigger(trig.get("id")):
                    revoked["triggers"] += 1
    except Exception as e:
        _config.log_silent("missions.cancel_mission.triggers", e)
    try:
        from aria.agent import proactive as _pro_mod
        for goal in _pro_mod.goal_list():
            if goal.get("status") == "completed":
                continue
            if _policy_mission_id(goal.get("policy")) == mid:
                if _pro_mod.goal_cancel(goal.get("id")):
                    revoked["goals"] += 1
    except Exception as e:
        _config.log_silent("missions.cancel_mission.goals", e)
    return revoked


def mission_summaries(limit: int = 10) -> List[Dict[str, Any]]:
    """Structured view for the bridge Missions section."""
    _ensure_tables()
    rows = _query(
        "SELECT id FROM missions ORDER BY id DESC LIMIT ?",
        (int(limit),))
    out = []
    for (mid,) in rows:
        m = _mission_row(mid)
        steps = _step_rows(mid)
        out.append({
            "id": m["id"], "goal": m["goal"],
            "status": m["status"], "created": m["created"],
            "done": sum(1 for s in steps
                        if s["status"] == "done"),
            "total": len(steps),
            "summary": m["summary"] or "",
        })
    return out


def active_mission_cards(limit: int = 3) -> List[Dict[str, Any]]:
    """Non-terminal missions for the Shell Today strip."""
    _ensure_tables()
    rows = _query(
        "SELECT id FROM missions WHERE status NOT IN "
        "('done', 'done_unverified', 'failed', 'cancelled') "
        "ORDER BY id LIMIT ?", (int(limit),))
    out = []
    for (mid,) in rows:
        m = _mission_row(mid)
        steps = _step_rows(mid)
        out.append({
            "id": m["id"], "goal": m["goal"],
            "status": m["status"],
            "done": sum(1 for s in steps
                        if s["status"] == "done"),
            "total": len(steps),
        })
    return out


def list_text(limit: int = 10) -> str:
    _ensure_tables()
    rows = _query(
        "SELECT id FROM missions ORDER BY id DESC LIMIT ?",
        (int(limit),))
    if not rows:
        return "No missions yet."
    lines = []
    for (mid,) in rows:
        m = _mission_row(mid)
        steps = _step_rows(mid)
        done = sum(1 for s in steps if s["status"] == "done")
        gate = "" if m["plan_approved"] else " (plan not approved)"
        lines.append(f"#{mid} [{m['status']}] {done}/{len(steps)} "
                     f"steps{gate} — {m['goal']}")
        if m["status"] in TERMINAL and m["summary"]:
            lines.append("  " + m["summary"].split("\n")[1]
                         if "\n" in m["summary"] else
                         "  " + m["summary"])
    return "\n".join(lines)


# ------------------------------------------------------------------
# Tool wrappers (registered in aria/tools/dispatch.py, declared
# in aria/tools/schemas.py — exactly four mission tools).
# ------------------------------------------------------------------

def tool_mission_create(args: dict) -> str:
    a = args or {}
    if "_approve_plan" in a or "_mission_id" in a:
        # Private plan-gate arguments. The ONLY legitimate
        # caller of this branch is _park_plan's stashed replay,
        # executed by approve_token() after the user's banner
        # tap — and approve_token() marks exactly that replay
        # (tool + args) on the thread. An ordinary model call
        # carrying these keys is a forged plan approval: the
        # schema never declares them, and a generic
        # preauthorized scope (routine replay, skill step) is
        # NOT enough — routines carry preauthorized too. Fail
        # closed: refuse, approve nothing, create nothing.
        # (ChatGPT x ARIA loop pass 11, 2026-10-31: the old
        # code honoured the flags on any call, so a model
        # could unlock its own pending mission plan, and the
        # approve branch ran before goal/steps validation, so
        # even a bare {"_mission_id": N, "_approve_plan": true}
        # unlocked it.)
        try:
            from aria import approval as _approval
            _is_replay = _approval.in_approve_replay(
                "mission_create", a)
        except Exception:
            _is_replay = False
        if not _is_replay:
            return ("[Mission error: plan approval is only "
                    "honoured on the user's approval tap — a "
                    "mission_create call carrying _approve_plan "
                    "or _mission_id outside an approve-token "
                    "replay is refused, and nothing was "
                    "approved or created.]")
        try:
            mid = int(a.get("_mission_id") or 0)
        except Exception:
            mid = 0
        if not mid:
            return ("[Mission error: the plan-approval replay "
                    "carries no mission id.]")
        return approve_plan(mid)
    raw_steps = a.get("steps")
    if isinstance(raw_steps, str):
        try:
            raw_steps = json.loads(raw_steps)
        except Exception:
            return ("[Mission error: steps must be a JSON array "
                    "of {\"tool\": name, \"args\": {...}} objects.]")
    deadline = None
    if a.get("deadline_minutes"):
        try:
            deadline = (datetime.now() + timedelta(
                minutes=float(a["deadline_minutes"]))).strftime(
                    "%Y-%m-%d %H:%M:%S")
        except Exception:
            deadline = None
    allowed = a.get("allowed_tools")
    if isinstance(allowed, str):
        allowed = [t.strip() for t in allowed.split(",")
                   if t.strip()]
    pre = a.get("pre_approved_irreversible")
    if isinstance(pre, str):
        pre = [t.strip() for t in pre.split(",") if t.strip()]
    try:
        spend = float(a.get("spend_cap") or 0)
    except Exception:
        spend = 0
    return create_mission(
        a.get("goal", ""), raw_steps if isinstance(raw_steps, list)
        else [],
        allowed_tools=allowed, pre_approved_irreversible=pre,
        spend_cap=spend, deadline=deadline,
        task_type=a.get("task_type"))


def tool_mission_list(args: dict) -> str:
    return list_text()


def tool_mission_pause(args: dict) -> str:
    try:
        mid = int((args or {}).get("mission_id") or 0)
    except Exception:
        mid = 0
    if not mid:
        return "[Mission error: mission_id is required.]"
    return pause_mission(mid)


def tool_mission_cancel(args: dict) -> str:
    try:
        mid = int((args or {}).get("mission_id") or 0)
    except Exception:
        mid = 0
    if not mid:
        return "[Mission error: mission_id is required.]"
    return cancel_mission(mid)


def record_receipt(mid, step_id, reference):
    """User attests a checked external receipt from Work Center. Never model-callable."""
    reference = str(reference).strip()
    if not reference:
        raise ValueError('Enter the receipt or evidence you checked')
    step = next(s for s in _step_rows(mid) if s['id'] == step_id)
    if step['status'] != 'uncertain':
        raise ValueError('This step is not awaiting reconciliation')
    result = f'status=done action={step["tool"]} user_checked_receipt={reference}'
    action_journal.finish(f'mission:{mid}:step:{step_id}', result)
    _set_step(step_id, status='done', output=result, approval_token=None)
    _set_mission(mid, status='running')
