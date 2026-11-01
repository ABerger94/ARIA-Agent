"""Per-execution state, cancellation, and shared desktop coordination."""
from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass, field
import functools
import threading
import time
import uuid

from aria import workspaces


class Cancelled(Exception):
    pass


@dataclass
class ExecutionContext:
    kind: str = 'tool'
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    project: str = ''
    cancel: threading.Event = field(default_factory=threading.Event)
    calls: dict = field(default_factory=dict)
    failures: dict = field(default_factory=dict)
    user_message: str = ''
    open_target: str = ''
    nudged: bool = False
    last_tool: tuple = (None, 0.0)
    toolkits: set = field(default_factory=lambda: {'core'})
    operation_id: str = ''
    budget_scope: str = ''
    spend_cap: float | None = None
    started: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


_CURRENT = contextvars.ContextVar('aria_execution', default=None)
_OPERATION = contextvars.ContextVar('aria_operation', default='')
DISPATCH_DEPTH = contextvars.ContextVar('aria_dispatch_depth', default=0)
_LOCAL = threading.local()
_ACTIVE = {}
_LOCK = threading.RLock()
FOREGROUND_LOCK = threading.RLock()
DESKTOP_LOCK = threading.RLock()


def current():
    bound = _CURRENT.get()
    if bound is not None:
        return bound
    project = workspaces.current()
    ctx = getattr(_LOCAL, 'context', None)
    if ctx is None or ctx.project != project:
        ctx = _LOCAL.context = ExecutionContext(project=project)
    return ctx


def check_cancelled():
    if current().cancel.is_set():
        raise Cancelled('Work cancelled; no further actions will start')


@contextlib.contextmanager
def execution(kind='tool', *, project=None, execution_id=None, cancel=None,
              budget_scope='', spend_cap=None):
    ctx = ExecutionContext(kind=kind, project=project or workspaces.current())
    if execution_id:
        ctx.id = execution_id
    if cancel is not None:
        ctx.cancel = cancel
    ctx.budget_scope = budget_scope or ctx.id
    ctx.spend_cap = spend_cap
    token = _CURRENT.set(ctx)
    with workspaces.use(ctx.project):
        with _LOCK:
            _ACTIVE[ctx.id] = ctx
        try:
            yield ctx
        finally:
            with _LOCK:
                _ACTIVE.pop(ctx.id, None)
            _CURRENT.reset(token)


def operation_id():
    return _OPERATION.get() or current().operation_id


@contextlib.contextmanager
def operation(value):
    token = _OPERATION.set(value)
    try:
        yield
    finally:
        _OPERATION.reset(token)


def snapshot():
    with _LOCK:
        return [{'id': c.id, 'kind': c.kind, 'project': c.project,
                 'elapsed': time.time() - c.started, 'cancelled': c.cancel.is_set()}
                for c in _ACTIVE.values()]


def cancel_work(execution_id=None, *, project=None, kind=None):
    project = project or workspaces.current()
    with _LOCK:
        targets = [c for c in _ACTIVE.values()
                   if c.project == project and (not execution_id or c.id == execution_id)
                   and (not kind or c.kind == kind)]
        for ctx in targets:
            ctx.cancel.set()
    return len(targets)


def foreground(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        # Capture selection before waiting for the previous conversation turn.
        project = workspaces.current()
        with execution('foreground', project=project) as ctx:
            while not FOREGROUND_LOCK.acquire(timeout=.1):
                check_cancelled()
            try:
                check_cancelled()
                from aria.agent import brain
                if (getattr(brain, '_HISTORY_PROJECT', 'default') != ctx.project or
                        not getattr(brain, '_HISTORY_LOADED', False) and not brain.CONVERSATION_HISTORY):
                    brain.CONVERSATION_HISTORY = workspaces.load_history()
                brain._HISTORY_PROJECT = ctx.project
                brain._HISTORY_LOADED = True
                try:
                    return fn(*args, **kwargs)
                finally:
                    workspaces.save_history(brain.CONVERSATION_HISTORY)
                    brain.BUSY_PROCESSING = False
            finally:
                FOREGROUND_LOCK.release()
    return wrapped


def copied_call(fn, *args, **kwargs):
    """Bind a callback to the submitting thread's project and execution context."""
    ctx = contextvars.copy_context()
    project = workspaces.current()
    def bound():
        with workspaces.use(project):
            return fn(*args, **kwargs)
    return lambda: ctx.run(bound)
