"""
ARIA delegated-task journal (Gap fill 3 — task-engine hardening).

WHY THIS EXISTS
    The delegated-task queue (aria.agent.taskqueue) kept every
    task in memory: a crash or restart lost the task, its plan,
    and — worse — any record of which steps had already run, so
    nothing could safely resume. Mark-LVI's delegation engine
    (core/delegation.py) survives this with durable checkpoints
    and explicit blocked states; this module is that idea in
    ARIA-Agent's shape: a small JSON journal the queue writes as
    execution proceeds.

    One record per task: goal, status (pending / running /
    completed / failed / cancelled / blocked), the plan, the
    completed steps with their results (the checkpoint), the
    blocked reason when parked, and the final result. A record
    left 'running' by a dead process is recovered as blocked
    ('interrupted') — resumable, never silently re-run.

STORAGE
    WORKSPACE_DIR/delegated_tasks.json, written atomically: the
    temp file is staged in the destination's own directory
    before os.replace (the settings_save cross-device lesson,
    2026-10-27). Progress-write failures (checkpoint/finish)
    are swallowed to a log line — the journal must never be able
    to break the queue it serves. But the CREATE write is the
    submission's durability itself: when it fails, create()
    raises instead of returning an unwritten record (pass 28
    #17), so a submission is never acknowledged as durable when
    it is not.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from aria import config as _config
from aria import workspaces

_LOCK = threading.Lock()
RESUMABLE_STATUSES = ("blocked", "failed", "interrupted")


def _path() -> str:
    return workspaces.path("delegated_tasks.json", os.path.join(_config.WORKSPACE_DIR, "delegated_tasks.json"))


def _load() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(data: Dict[str, Any]) -> bool:
    """Atomically write the journal. Returns True on success,
    False on failure (still logged, never raised) — the CALLER
    decides what a failed write means: create() refuses to
    acknowledge an unwritten record (pass 28 #17), while
    checkpoint()/finish() keep the old swallow-to-log behaviour
    for progress writes, since the submission itself stands."""
    try:
        dest = _path()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix=".tasks-", dir=os.path.dirname(dest))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return True
    except Exception as e:
        _config.log_silent("taskstore_save", e)
        return False


def _jsonable(value):
    """JSON-safe copy for journal storage: an envelope captured
    from a submitting context may carry non-JSON values, and an
    unserializable record would make _save swallow the whole
    write (pass 26 B1#4)."""
    if value is None:
        return None
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return None


def create(task_id: str, goal: str, resumed_from: str = "", execution_scope: str = "",
           spend_cap=None, budget_scope: str = "", envelope=None,
           mission_id=None, parent_execution_id: str = "",
           status: str = "running",
           plan=None, completed=None, results=None) -> Dict[str, Any]:
    """Open a journal record for a task about to run. The submitting
    execution's policy (spend cap, budget scope, envelope) and
    parent identity ride along, so a resume restores the ORIGINAL
    mission policy instead of the resumer's context (pass 26
    B1#4) and a mission cancel can find its children (B2#5).

    Pass 27 #26: submit() opens the record as status='pending'
    BEFORE acknowledging the submission, so a task queued but
    never started survives a restart as a visible, resumable
    record instead of vanishing. _run_task_bound flips a pending
    record to 'running' when the worker starts it; the default
    status='running' keeps the direct-create path unchanged.

    Pass 28 #17: the write is the durability — when it fails,
    raise RuntimeError instead of returning the record. A
    returned record the journal never saw would let submit()
    acknowledge a submission a restart cannot recover.

    Pass 28 #14: plan/completed/results seed the record's
    checkpoint in the SAME write as the creation — a resume's
    checkpoint must be durable from birth, not only in the
    in-memory Task, or a restart before the worker starts it
    recovers a record with no checkpoint and repeats finished
    steps.
    """
    rec = {
        "task_id": task_id, "goal": goal, "status": status,
        "plan": plan, "completed": list(completed or []),
        "results": {str(k): v for k, v in (results or {}).items()},
        "blocked_reason": "", "result": "", "error": "",
        "resumed_from": resumed_from, "execution_scope": execution_scope or f"task:{task_id}",
        "spend_cap": spend_cap, "budget_scope": budget_scope or "",
        "envelope": _jsonable(envelope), "mission_id": mission_id,
        "parent_execution_id": parent_execution_id or "",
        "created_at": time.time(), "updated_at": time.time(),
        "checkpoint_at": time.time() if (plan is not None or completed
                                         or results) else 0.0,
    }
    with _LOCK:
        data = _load()
        data[task_id] = rec
        if not _save(data):
            raise RuntimeError(
                f"task journal write failed for {task_id} — the "
                "submission was not acknowledged")
    return rec


def checkpoint(task_id: str, state: Dict[str, Any]) -> None:
    """Persist execution progress: plan + completed steps +
    their results, exactly what a resume needs to skip finished
    work instead of repeating it."""
    with _LOCK:
        data = _load()
        rec = data.get(task_id)
        if rec is None:
            return
        rec["plan"] = state.get("plan")
        rec["completed"] = state.get("completed") or []
        rec["results"] = {str(k): v for k, v in
                          (state.get("results") or {}).items()}
        rec["checkpoint_at"] = time.time()
        rec["updated_at"] = time.time()
        _save(data)


def finish(task_id: str, status: str, result: str = "",
           error: str = "", blocked_reason: str = "") -> None:
    with _LOCK:
        data = _load()
        rec = data.get(task_id)
        if rec is None:
            return
        rec["status"] = status
        if result:
            rec["result"] = result
        if error:
            rec["error"] = error
        if blocked_reason:
            rec["blocked_reason"] = blocked_reason
        rec["updated_at"] = time.time()
        _save(data)


def get(task_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _load().get(task_id)


def all_records() -> List[Dict[str, Any]]:
    with _LOCK:
        return sorted(_load().values(),
                      key=lambda r: r.get("created_at", 0))


def resumable() -> List[Dict[str, Any]]:
    return [r for r in all_records()
            if r.get("status") in RESUMABLE_STATUSES]


def recover_interrupted() -> int:
    """Mark records a dead process left 'running' — and records
    left 'pending' (queued but never started, pass 27 #26) — as
    blocked: the honest state is that their outcome is unknown
    (or never began), and a human (or ARIA, asked) resumes them
    explicitly from the checkpoint. Returns how many were
    recovered. Called when the queue singleton is first built."""
    count = 0
    with _LOCK:
        data = _load()
        for rec in data.values():
            if rec.get("status") == "running":
                rec["status"] = "blocked"
                rec["blocked_reason"] = (
                    "interrupted: ARIA restarted mid-task")
                rec["updated_at"] = time.time()
                count += 1
            elif rec.get("status") == "pending":
                rec["status"] = "blocked"
                rec["blocked_reason"] = (
                    "queued but never started: ARIA restarted "
                    "before execution")
                rec["updated_at"] = time.time()
                count += 1
        if count:
            _save(data)
    return count
