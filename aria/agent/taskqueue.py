"""
ARIA delegated-task queue (Merger Phase 3 — blueprint SS2.1, SS3.3).

Adapted from agent/task_queue.py in Mark-XXXIX-OR by FatihMakes
(https://github.com/FatihMakes/Mark-XXXIX-OR), licensed CC BY-NC 4.0.
ARIA is a personal, non-commercial system; attribution is retained
here and in the commit message. His structure is carried over:
priority-ordered queue, per-task cancel flag, status registry,
single worker draining one task at a time. Adaptations:

  * the executor is INJECTED (his queue lazily constructed his
    AgentExecutor; the default factory here builds ARIA's
    aria.agent.taskexec.AgentExecutor, and tests inject fakes);
  * print() logging replaced with the event ring (config.add_log);
  * concurrency is bounded by config.TASK_QUEUE_MAX_CONCURRENT
    (blueprint SS3.3: 1) instead of a constructor literal;
  * task records carry started_at + the whole-task budget
    (config.DELEGATED_TASK_BUDGET_S, enforced by the executor)
    so task_status can report elapsed/budget;
  * the active-count decrement lives in a finally (his decremented
    on the success path and the exception path separately).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from aria import config as _config
from aria.config import add_log
from aria.agent import taskstore


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"   # parked with a checkpoint; resumable


class TaskPriority(Enum):
    LOW = 3
    NORMAL = 2
    HIGH = 1


@dataclass(order=True)
class Task:
    priority: int
    created_at: float = field(compare=False)
    task_id: str = field(compare=False)
    goal: str = field(compare=False)
    status: TaskStatus = field(compare=False, default=TaskStatus.PENDING)
    result: Any = field(compare=False, default=None)
    error: str = field(compare=False, default="")
    speak: Any = field(compare=False, default=None)
    on_complete: Any = field(compare=False, default=None)
    resume: Any = field(compare=False, default=None)
    resumed_from: str = field(compare=False, default="")
    started_at: float = field(compare=False, default=0.0)
    cancel_flag: threading.Event = field(
        compare=False, default_factory=threading.Event)


def _default_executor_factory():
    from aria.agent.taskexec import AgentExecutor
    return AgentExecutor()


class TaskQueue:
    """Bounded-concurrency delegated-task queue. One worker thread
    drains the queue; each task runs the executor on its own thread
    (his shape), with _active_count enforcing the concurrency bound."""

    def __init__(self, max_concurrent: Optional[int] = None,
                 executor_factory: Optional[Callable] = None):
        self._queue: List[Task] = []
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._tasks: Dict[str, Task] = {}
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._max_concurrent = (
            max_concurrent if max_concurrent is not None
            else int(getattr(_config, "TASK_QUEUE_MAX_CONCURRENT", 1)))
        self._active_count = 0
        self._executor_factory = executor_factory or _default_executor_factory
        self._executor = None

    def _get_executor(self):
        if self._executor is None:
            self._executor = self._executor_factory()
        return self._executor

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="aria-task-queue")
        self._worker_thread.start()
        add_log("Task queue started (delegated tasks, concurrency "
                f"{self._max_concurrent})")

    def stop(self) -> None:
        self._running = False
        with self._condition:
            self._condition.notify_all()

    def submit(self, goal: str, priority: TaskPriority = TaskPriority.NORMAL,
               speak: Optional[Callable] = None,
               on_complete: Optional[Callable] = None,
               resume_from: str = "") -> str:
        resume_state = None
        if resume_from:
            rec = taskstore.get(resume_from)
            if rec is None:
                raise ValueError(f"no journal record for task {resume_from}")
            if rec.get("status") not in taskstore.RESUMABLE_STATUSES:
                raise ValueError(
                    f"task {resume_from} is {rec.get('status')}, not resumable")
            resume_state = {"plan": rec.get("plan"),
                            "completed": rec.get("completed") or [],
                            "results": rec.get("results") or {}}
            if not goal:
                goal = rec.get("goal", "")
        task_id = str(uuid.uuid4())[:8]
        task = Task(
            priority=priority.value,
            created_at=time.time(),
            task_id=task_id,
            goal=goal,
            speak=speak,
            on_complete=on_complete,
            resume=resume_state,
            resumed_from=resume_from,
        )
        with self._condition:
            self._queue.append(task)
            self._queue.sort(key=lambda t: (t.priority, t.created_at))
            self._tasks[task_id] = task
            self._condition.notify()
        add_log(f"Task queued: [{task_id}] {goal[:60]}")
        return task_id

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED,
                               TaskStatus.CANCELLED):
                return False
            task.cancel_flag.set()
            task.status = TaskStatus.CANCELLED
            add_log(f"Task cancelled: [{task_id}]")
            return True

    def get_status(self, task_id: str) -> Optional[dict]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            elapsed = (time.time() - task.started_at) if (
                task.status == TaskStatus.RUNNING and task.started_at) else 0.0
            return {
                "task_id": task.task_id,
                "goal": task.goal,
                "status": task.status.value,
                "result": task.result,
                "error": task.error,
                "elapsed_s": round(elapsed, 1),
                "budget_s": int(getattr(_config, "DELEGATED_TASK_BUDGET_S", 600)),
            }

    def get_all_statuses(self) -> List[dict]:
        with self._lock:
            return [
                {"task_id": t.task_id, "goal": t.goal[:50],
                 "status": t.status.value}
                for t in self._tasks.values()
            ]

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._queue if t.status == TaskStatus.PENDING)

    def _worker_loop(self) -> None:
        while self._running:
            task = None
            with self._condition:
                while self._running and not self._next_task():
                    self._condition.wait(timeout=1.0)
                task = self._next_task()
                if task:
                    task.status = TaskStatus.RUNNING
                    task.started_at = time.time()
                    self._active_count += 1
                    try:
                        self._queue.remove(task)
                    except ValueError:
                        pass
            if task:
                threading.Thread(
                    target=self._run_task, args=(task,),
                    daemon=True, name=f"aria-task-{task.task_id}").start()

    def _next_task(self) -> Optional[Task]:
        if self._active_count >= self._max_concurrent:
            return None
        for task in self._queue:
            if task.status == TaskStatus.PENDING and not task.cancel_flag.is_set():
                return task
        return None

    def _run_task(self, task: Task) -> None:
        add_log(f"Task running: [{task.task_id}] {task.goal[:60]}")
        # Journal first (Gap fill 3): the record exists before any
        # step runs, so a crash mid-task leaves a resumable trace.
        taskstore.create(task.task_id, task.goal,
                         resumed_from=task.resumed_from)

        def _ckpt(state: dict) -> None:
            taskstore.checkpoint(task.task_id, state)

        try:
            executor = self._get_executor()
            # Pass the hardening kwargs only to executors whose
            # execute() accepts them — injected test doubles keep
            # the original (goal, speak, cancel_flag) signature.
            import inspect
            params = inspect.signature(executor.execute).parameters
            kwargs: Dict[str, Any] = {}
            if "on_checkpoint" in params:
                kwargs["on_checkpoint"] = _ckpt
            if "resume" in params:
                kwargs["resume"] = task.resume
            if "verify" in params:
                try:
                    kwargs["verify"] = bool(
                        _config.get_setting("task_verify", True))
                except Exception:
                    kwargs["verify"] = False
            result = executor.execute(
                goal=task.goal,
                speak=task.speak,
                cancel_flag=task.cancel_flag,
                **kwargs,
            )
            with self._lock:
                if task.cancel_flag.is_set():
                    task.status = TaskStatus.CANCELLED
                else:
                    task.status = TaskStatus.COMPLETED
                    task.result = result
            taskstore.finish(
                task.task_id,
                "cancelled" if task.cancel_flag.is_set() else "completed",
                result=str(result))
            if task.on_complete and not task.cancel_flag.is_set():
                try:
                    task.on_complete(task.task_id, result)
                except Exception as e:
                    add_log(f"Task on_complete callback error: {e}")
            add_log(f"Task completed: [{task.task_id}]")
        except Exception as e:
            from aria.agent.taskexec import TaskBlocked
            if isinstance(e, TaskBlocked):
                # Parked, not failed: checkpoint persisted, reason
                # recorded, resumable via the task_resume tool.
                taskstore.checkpoint(task.task_id, e.state)
                taskstore.finish(task.task_id, "blocked",
                                 blocked_reason=e.reason)
                with self._lock:
                    task.status = TaskStatus.BLOCKED
                    task.error = e.reason
                add_log(f"Task blocked: [{task.task_id}] {e.reason[:80]}")
            else:
                with self._lock:
                    task.status = TaskStatus.FAILED
                    task.error = str(e)
                taskstore.finish(task.task_id, "failed", error=str(e))
                add_log(f"Task failed: [{task.task_id}] {e}")
        finally:
            with self._lock:
                self._active_count -= 1
            with self._condition:
                self._condition.notify()


_queue: Optional[TaskQueue] = None
_queue_lock = threading.Lock()


def get_queue() -> TaskQueue:
    """Process-wide queue singleton, started on first use (his shape)."""
    global _queue
    with _queue_lock:
        if _queue is None:
            _queue = TaskQueue()
            _queue.start()
            # Crash recovery (Gap fill 3): journal records a dead
            # process left 'running' become blocked — visible and
            # resumable, never silently re-run or silently lost.
            try:
                n = taskstore.recover_interrupted()
                if n:
                    add_log(f"Task journal: {n} interrupted task(s) "
                            "recovered as blocked")
            except Exception as e:
                add_log(f"Task journal recovery failed: {e}")
        return _queue
