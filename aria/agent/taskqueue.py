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
from aria import runtime, workspaces
from aria.outcome import normalize, FAILED


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
    execution_scope: str = field(compare=False, default="")
    # Captured at submit time from the submitting thread's execution
    # (pass 25): a delegated task minted a fresh context with cap
    # None, so work delegated out of a mission spent outside its
    # spend cap and outside its tool envelope. Uncapped submissions
    # capture None/"" and behave exactly as before.
    spend_cap: Any = field(compare=False, default=None)
    budget_scope: str = field(compare=False, default="")
    envelope: Any = field(compare=False, default=None)
    # Identity of the submitting execution (pass 26 B2#5): a
    # mission cancel stops its delegated children by mission_id.
    # Never the parent's cancel event itself — sharing it would
    # cancel the child when the mission's step context ends
    # normally. Uncapped / non-mission submissions carry ""/None.
    parent_execution_id: str = field(compare=False, default="")
    mission_id: Any = field(compare=False, default=None)
    project: str = field(compare=False, default="default")
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
        execution_scope = ""
        resume_rec = None
        if resume_from:
            rec = taskstore.get(resume_from)
            if rec is None:
                raise ValueError(f"no journal record for task {resume_from}")
            if rec.get("status") not in taskstore.RESUMABLE_STATUSES:
                raise ValueError(
                    f"task {resume_from} is {rec.get('status')}, not resumable")
            resume_rec = rec
            execution_scope = rec.get("execution_scope") or f"task:{resume_from}"
            resume_state = {"plan": rec.get("plan"),
                            "completed": rec.get("completed") or [],
                            "results": rec.get("results") or {},
                            "pending_child": rec.get("pending_child"),
                            "execution_scope": execution_scope}
            if not goal:
                goal = rec.get("goal", "")
        task_id = str(uuid.uuid4())[:8]
        # Capture the submitting execution's budget + envelope now:
        # the worker runs on another thread, where the submitter's
        # context is not visible — capturing later would read a
        # fresh default (cap None, no envelope) and the delegation
        # would escape both (pass 25).
        try:
            _parent_ctx = runtime.current()
            parent_cap = _parent_ctx.spend_cap
            parent_scope = _parent_ctx.budget_scope
            parent_env = _parent_ctx.envelope
            parent_exec_id = _parent_ctx.id
            parent_mid = None
            if isinstance(parent_env, dict):
                parent_mid = parent_env.get("mission_id")
        except Exception:
            parent_cap, parent_scope, parent_env = None, "", None
            parent_exec_id, parent_mid = "", None
        if resume_rec is not None:
            # A resume restores the ORIGINAL mission policy from
            # the journal (pass 26 B1#4): a mission-origin task
            # resumed from ordinary conversation keeps its cap,
            # scope, envelope, and mission parent instead of
            # running uncapped on the resumer's context. A record
            # written before these fields existed has none of the
            # keys — only then does the resumer's context win.
            # Pass 28 #1: an explicit null (or, for the string
            # fields, an empty value) in the record counts as
            # absent — a journal record carrying "envelope": null /
            # "spend_cap": null must not override the resumer's
            # live policy with nothing (that is how a forged
            # record escaped a capped mission); only a real value
            # restores the original policy. spend_cap keeps the
            # is-not-None test because a $0 cap is meaningful;
            # budget_scope / parent_execution_id take the truthy
            # test because "" is their "no value" default and a
            # default-scope record must not drop the resumer's
            # mission scope (spend attribution) or parent link.
            if resume_rec.get("spend_cap") is not None:
                parent_cap = resume_rec["spend_cap"]
            if resume_rec.get("budget_scope"):
                parent_scope = resume_rec["budget_scope"]
            if resume_rec.get("envelope") is not None:
                parent_env = resume_rec["envelope"]
            if resume_rec.get("mission_id") is not None:
                parent_mid = resume_rec["mission_id"]
            if resume_rec.get("parent_execution_id"):
                parent_exec_id = resume_rec["parent_execution_id"]
        task = Task(
            priority=priority.value,
            created_at=time.time(),
            task_id=task_id,
            goal=goal,
            speak=speak,
            on_complete=on_complete,
            resume=resume_state,
            resumed_from=resume_from,
            project=workspaces.current(),
            execution_scope=execution_scope or f"task:{task_id}",
            spend_cap=parent_cap,
            budget_scope=parent_scope,
            envelope=parent_env,
            parent_execution_id=parent_exec_id,
            mission_id=parent_mid,
        )
        # Pass 27 #26: persist the pending record — goal, policy
        # snapshot (cap, scope, envelope), and stable identity —
        # BEFORE the in-memory enqueue and before submit returns
        # the id (the acknowledgement). A task queued but never
        # started used to exist only in memory: an exit before
        # a worker picked it up lost it with no trace, and
        # recover_interrupted had no record to recover. The
        # record's callbacks (speak/on_complete) cannot be
        # persisted — a recovered task resumes without them; the
        # outcome stays in the event ring + registry.
        taskstore.create(
            task_id, goal, resumed_from=resume_from,
            execution_scope=task.execution_scope,
            spend_cap=task.spend_cap, budget_scope=task.budget_scope,
            envelope=task.envelope, mission_id=task.mission_id,
            parent_execution_id=task.parent_execution_id,
            status="pending",
            # Pass 28 #14: seed the resumed checkpoint in the same
            # write as the record — it must be durable from birth,
            # not only in the in-memory Task.
            plan=(resume_state or {}).get("plan"),
            completed=(resume_state or {}).get("completed"),
            results=(resume_state or {}).get("results"),
            # Pass 28 #15: claim the previous task in the same
            # write as this record — a second resume must find it
            # already claimed, not build a second executor.
            claim_from=resume_from)
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
            if not task or task.project != workspaces.current():
                return False
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED,
                               TaskStatus.CANCELLED):
                return False
            task.cancel_flag.set()
            task.status = TaskStatus.CANCELLED
            add_log(f"Task cancelled: [{task_id}]")
        # Pass 28 #16: persist the cancellation — the journal
        # record must not stay 'pending', or a restart's recovery
        # would resurrect the cancelled task as resumable.
        taskstore.finish(task_id, "cancelled")
        return True

    def cancel_children_of_mission(self, mission_id) -> int:
        """Cancel every queued/running task submitted under
        *mission_id* (pass 26 B2#5). Returns how many were
        stopped. Only the flag + status are touched — the child
        keeps its own cancel event, so ending a mission's step
        context normally never cancels it; only an explicit
        mission cancel reaches it."""
        if mission_id is None:
            return 0
        stopped = 0
        stopped_ids = []
        with self._lock:
            for task in self._tasks.values():
                if task.mission_id != mission_id:
                    continue
                if task.project != workspaces.current():
                    continue
                if task.status in (TaskStatus.COMPLETED,
                                   TaskStatus.FAILED,
                                   TaskStatus.CANCELLED):
                    continue
                task.cancel_flag.set()
                task.status = TaskStatus.CANCELLED
                stopped += 1
                stopped_ids.append(task.task_id)
                add_log(f"Task cancelled with mission {mission_id}: "
                        f"[{task.task_id}]")
        # Pass 28 #16: persist each cancellation (see cancel()
        # above) — a mission-cancelled child must not resurrect as
        # resumable after a restart. Outside the queue lock: the
        # journal write is disk I/O.
        for tid in stopped_ids:
            taskstore.finish(tid, "cancelled")
        return stopped

    def get_status(self, task_id: str) -> Optional[dict]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                if task.project != workspaces.current():
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
            # Pass 28 #12: after a restart the in-memory queue is
            # empty, but the journal still holds the task — fall
            # back to the durable record instead of reporting "no
            # record". The journal file is project-scoped
            # (workspaces.path), so this cannot leak across
            # projects; a live in-memory task still wins above.
            rec = taskstore.get(task_id)
            if rec is None:
                return None
            return {
                "task_id": rec["task_id"],
                "goal": rec.get("goal", ""),
                "status": rec.get("status", ""),
                "result": rec.get("result", ""),
                "error": rec.get("error", ""),
                "elapsed_s": 0.0,
                "budget_s": int(getattr(_config, "DELEGATED_TASK_BUDGET_S", 600)),
            }

    def get_all_statuses(self) -> List[dict]:
        with self._lock:
            return [
                {"task_id": t.task_id, "goal": t.goal[:50],
                 "status": t.status.value}
                for t in self._tasks.values() if t.project == workspaces.current()
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
        with runtime.execution('task', project=task.project,
                               execution_id=f'task:{task.task_id}', cancel=task.cancel_flag,
                               budget_scope=task.budget_scope or task.execution_scope or f'task:{task.task_id}',
                               spend_cap=task.spend_cap, envelope=task.envelope):
            self._run_task_bound(task)

    def _run_task_bound(self, task: Task) -> None:
        add_log(f"Task running: [{task.task_id}] {task.goal[:60]}")
        # Journal first (Gap fill 3): the record exists before any
        # step runs, so a crash mid-task leaves a resumable trace.
        # Pass 27 #26: submit() already opened the record as
        # 'pending' — flip it to 'running' instead of overwriting
        # it, so the submit-time identity, policy, and created_at
        # survive. A record from before that change (or a resume
        # whose id is new) is created as before.
        if taskstore.get(task.task_id) is None:
            taskstore.create(task.task_id, task.goal,
                             resumed_from=task.resumed_from, execution_scope=task.execution_scope,
                             spend_cap=task.spend_cap, budget_scope=task.budget_scope,
                             envelope=task.envelope, mission_id=task.mission_id,
                             parent_execution_id=task.parent_execution_id)
        else:
            taskstore.finish(task.task_id, "running")

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
                elif normalize(result).status == FAILED:
                    task.status = TaskStatus.FAILED
                    task.error = str(result)
                else:
                    task.status = TaskStatus.COMPLETED
                    task.result = result
            taskstore.finish(
                task.task_id,
                task.status.value, result=str(result), error=task.error)
            if task.on_complete and task.status == TaskStatus.COMPLETED:
                try:
                    task.on_complete(task.task_id, result)
                except Exception as e:
                    add_log(f"Task on_complete callback error: {e}")
            add_log(f"Task completed: [{task.task_id}]")
        except Exception as e:
            from aria.agent.taskexec import TaskBlocked
            if isinstance(e, runtime.Cancelled):
                taskstore.finish(task.task_id, "cancelled")
                with self._lock:
                    task.status = TaskStatus.CANCELLED
            elif isinstance(e, TaskBlocked):
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
# Projects whose journals have been crash-recovered in this
# process (pass 28 #18): the journal is project-scoped, so
# recovery must run once per project, not once per process.
_recovered_projects = set()


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
        # Pass 28 #18: recover the CURRENT project the first time
        # it is seen, not only the project active at singleton
        # init — a project first used later never had its
        # interrupted records recovered.
        project = workspaces.current()
        if project not in _recovered_projects:
            _recovered_projects.add(project)
            try:
                n = taskstore.recover_interrupted()
                if n:
                    add_log(f"Task journal: {n} interrupted task(s) "
                            "recovered as blocked")
            except Exception as e:
                add_log(f"Task journal recovery failed: {e}")
        return _queue
