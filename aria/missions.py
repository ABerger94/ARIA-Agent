"""ARIA Durable Mission Layer v1 (Alek's design, approved
2026-10-31: "I like it. Build it out fully.").

A mission is a job handed to ARIA once, in plain language, that
outlives the conversation, a restart, and Alek's shift. She
plans it (the plan is fixed at creation — no LLM replanning),
works it step by step, only bothers him at the points the
authorization envelope says matter, and hands back a result
with evidence.

Design contract (as approved):
  * Persistence: missions + mission_steps tables in the SAME
    SQLite store the scheduler runs on (aria.memory.DB_PATH —
    <install root>/workspace/aria_memory.db, the store V20
    confirmed canonical), written through DB_LOCK like the
    scheduler's own tables. State therefore lives at the
    install root, never home-derived.
  * Lifecycle: planned -> running -> (waiting_approval |
    blocked_input | blocked_model) -> verifying -> done |
    done_unverified | failed, plus paused / cancelled as user
    controls. The scheduler's 10s loop calls executor_tick();
    a single-flight worker thread runs ONE mission at a time,
    others queue in creation order.
  * Checkpoints: a step's output is written to its row the
    moment it succeeds. Completed steps are NEVER replayed —
    after a restart the executor resumes at the first step
    whose status is not 'done' (a step interrupted mid-flight
    re-runs; it was never checkpointed).
  * The envelope is the contract, set at creation: allowed
    tools, a spending cap (default $0 — recorded as contract;
    v1 has no billing seam to meter, so the cap binds by
    keeping purchase-shaped tools out of the envelope unless
    named), and irreversible actions pre-approved BY TOOL NAME.
    A step inside the envelope under approval mode 'auto'
    runs without asking (auto means auto). A step outside the
    envelope, or an irreversible action NOT named in it, parks
    through the EXISTING approval store (aria.approval) in
    every mode — so it surfaces on the dashboard banner, the
    Shell action cards, and the phone bridge band, and resolves
    through the one approve_token / deny_token path. No new
    approval path exists here.
  * Safe mode hard-blocks its usual list inside missions too
    (checked executor-side before any preauthorized dispatch,
    because preauthorized dispatch skips that check by design).
  * The plan-approval gate is the one new gate: mission_create
    parks the plan itself in the approval store as a
    'mission_create' replay carrying the mission id. Confirm
    replays it and unlocks execution; Deny cancels the mission.
  * Verification: after the last step, a verify pass judges
    the recorded step outputs against the goal (deterministic
    in v1: every step done, output present and not
    failure-shaped; an injected verifier may overrule). Max 2
    repair attempts (the offending steps re-run), then the
    mission closes done_unverified and SAYS so.
  * Quiet hours / presence govern speech only — this module
    never speaks. Delivery is the stored summary, surfaced on
    the Shell Today strip and the phone bridge Missions
    section (in-page only, no push).

Cuts honored: no parallel steps, no missions spawning missions
(mission tools are refused as steps), no new providers, no
recurring missions (the scheduler owns recurrence).
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from aria import config as _config
from aria.config import add_log
from aria import memory as _memory

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
        conn = sqlite3.connect(_memory.DB_PATH)
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
            conn.commit()
        finally:
            conn.close()


def _exec(sql: str, params=()) -> int:
    with _memory.DB_LOCK:
        conn = sqlite3.connect(_memory.DB_PATH)
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid or 0
        finally:
            conn.close()


def _query(sql: str, params=()) -> List[tuple]:
    with _memory.DB_LOCK:
        conn = sqlite3.connect(_memory.DB_PATH)
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
        "approval_token FROM mission_steps WHERE mission_id = ? "
        "ORDER BY idx", (mid,))
    out = []
    for r in rows:
        try:
            args = json.loads(r[3] or "{}")
        except Exception:
            args = {}
        out.append({"id": r[0], "idx": r[1], "tool": r[2],
                    "args": args, "status": r[4], "output": r[5],
                    "retries": r[6], "approval_token": r[7]})
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

def _is_irreversible(tool: str) -> bool:
    from aria import approval as _approval
    if tool in _approval.DESTRUCTIVE_TOOLS or \
            tool in _EXTRA_IRREVERSIBLE:
        return True
    return _config.is_discovered_mcp_tool(tool)


def _is_failure(out: Any) -> bool:
    """ARIA's tool-result failure convention (same markers
    taskexec watches). Approval / safe-mode strings are handled
    before this is consulted."""
    if not isinstance(out, str):
        return False
    return (out.startswith("[Tool Error")
            or out.startswith("[Argument error")
            or out.startswith("Unknown tool:")
            or "Traceback (most recent call last)" in out)


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
                   deadline: Optional[str] = None) -> str:
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
        mark = " [irreversible]" if _is_irreversible(s["tool"]) \
            else ""
        lines.append(f"  {s['idx'] + 1}. {s['tool']}{mark} "
                     f"{json.dumps(s['args'])[:160]}")
    lines.append(
        f"Envelope: tools [{', '.join(env.get('allowed_tools') or [])}]; "
        f"spend cap ${env.get('spend_cap', 0):g}; pre-approved "
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
            if _is_failure(result):
                _set_step(step_id, status="failed", output=result,
                          approval_token=None)
                _finish(mid, "failed",
                        note=f"Step {idx + 1} ({stool}) failed "
                             f"after approval: {result[:200]}")
                return
            # Success: this IS the checkpoint — the step's real
            # output, recorded once, never replayed.
            _set_step(step_id, status="done",
                      output=result[:_OUTPUT_CAP],
                      approval_token=None)
            if m["status"] == "waiting_approval":
                _set_mission(mid, status="running")
            add_log(f"[mission] #{mid} step {idx + 1} approved "
                    f"and completed")
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


def executor_tick() -> bool:
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
        rows = _query(
            "SELECT id FROM missions WHERE plan_approved = 1 AND "
            "status IN ('planned', 'running', 'verifying') "
            "ORDER BY id LIMIT 1")
        if not rows:
            return False
        _worker_alive = True
        mid = rows[0][0]
    threading.Thread(target=_run_mission_guarded, args=(mid,),
                     daemon=True, name="aria-mission").start()
    return True


def _run_mission_guarded(mid: int) -> None:
    global _worker_alive
    try:
        _run_mission(mid)
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
        may_run = allowed and (not _is_irreversible(tool)
                               or pre_ok)
        mode = _approval.get_mode()
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
            result = _approval.request_approval(
                tool, args,
                desc=f"Mission #{mid} step {step['idx'] + 1}: "
                     f"{_approval._describe_tool(tool, args)} "
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
    if _is_failure(result):
        retries = int(step["retries"] or 0) + 1
        if retries <= MAX_STEP_RETRIES:
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

def _verify(mid: int) -> bool:
    """Verify pass. Returns True when the mission reached a
    terminal state; False when a repair was queued and the
    worker loop should re-enter."""
    m = _mission_row(mid)
    steps = _step_rows(mid)
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
                   note: str = "") -> str:
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
    parked = [f"step {s['idx'] + 1} ({s['tool']}): {s['status']}"
              for s in steps
              if s["status"] in ("waiting", "denied", "failed",
                                  "cancelled")]
    lines.append("Parked/failed: "
                 + ("; ".join(parked) if parked else "none"))
    if note:
        lines.append(note)
    return "\n".join(lines)


def _finish(mid: int, status: str, note: str = "") -> None:
    m = _mission_row(mid)
    if m is None or m["status"] in TERMINAL:
        return
    _set_mission(mid, status=status)
    m = _mission_row(mid)
    steps = _step_rows(mid)
    _set_mission(mid, summary=_build_summary(m, steps, note))
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
    _finish(mid, "cancelled",
            note="Cancelled by Alek — remaining steps will "
                 "not run.")
    for s in steps:
        if s["status"] in ("pending", "running", "waiting"):
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
    return f"Mission #{mid} cancelled: '{m['goal']}'."


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
    if a.get("_approve_plan") and a.get("_mission_id"):
        # The plan gate's replay (approve_token, preauthorized):
        # unlock the mission instead of creating a duplicate.
        return approve_plan(int(a["_mission_id"]))
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
        spend_cap=spend, deadline=deadline)


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
