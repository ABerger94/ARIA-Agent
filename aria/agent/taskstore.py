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
    2026-10-27). Every failure is swallowed to a log line — the
    journal must never be able to break the queue it serves.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from aria import config as _config

_LOCK = threading.Lock()
RESUMABLE_STATUSES = ("blocked", "failed", "interrupted")


def _path() -> str:
    return os.path.join(_config.WORKSPACE_DIR, "delegated_tasks.json")


def _load() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(data: Dict[str, Any]) -> None:
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
    except Exception as e:
        _config.log_silent("taskstore_save", e)


def create(task_id: str, goal: str, resumed_from: str = "") -> Dict[str, Any]:
    """Open a journal record for a task about to run."""
    rec = {
        "task_id": task_id, "goal": goal, "status": "running",
        "plan": None, "completed": [], "results": {},
        "blocked_reason": "", "result": "", "error": "",
        "resumed_from": resumed_from,
        "created_at": time.time(), "updated_at": time.time(),
        "checkpoint_at": 0.0,
    }
    with _LOCK:
        data = _load()
        data[task_id] = rec
        _save(data)
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
    """Mark records a dead process left 'running' as blocked —
    the honest state: their outcome is unknown, and a human (or
    ARIA, asked) resumes them explicitly from the checkpoint.
    Returns how many were recovered. Called when the queue
    singleton is first built."""
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
        if count:
            _save(data)
    return count
