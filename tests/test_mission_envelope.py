"""Mission envelopes must constrain nested tools and delegated spend
(pass 25 P1#5).

Before this fix the envelope was checked only on the mission's
top-level step tool, and the spend cap lived only on the mission
worker's own execution context:
- a step using an allowed tool could nest ANY tool (a routine
  replay, a delegated call, a wrapper) past the check,
  preauthorized, with no envelope consulted anywhere below it;
- a delegated task minted a fresh execution with spend_cap None,
  so work delegated out of a mission spent outside its cap;
- a nested runtime.execution() likewise reset the cap to None.

Fix contract pinned here:
- runtime.execution() inherits the parent's spend_cap, envelope,
  and (with an inherited cap) budget scope when the caller passes
  none of them; explicit arguments always win;
- dispatch.execute_tool parks an out-of-envelope call for
  approval (the mission's own fork), nested calls included,
  unless it is the approved replay of that parked call;
- a delegated task captures the submitting execution's cap,
  scope, and envelope at submit time and runs under them.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMPHOME = tempfile.mkdtemp(prefix='aria_mission_envelope_')
os.environ['HOME'] = _TMPHOME

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

for _name in ('aria.scheduler', 'aria.vision', 'aria.hardware',
              'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules.setdefault(_name, types.ModuleType(_name))
tools_pkg = types.ModuleType('aria.tools')
tools_pkg.__path__ = [str(Path(__file__).parents[1] / 'aria' / 'tools')]
sys.modules.setdefault('aria.tools', tools_pkg)
agent_pkg = types.ModuleType('aria.agent')
agent_pkg.__path__ = [str(Path(__file__).parents[1] / 'aria' / 'agent')]
sys.modules.setdefault('aria.agent', agent_pkg)

import pytest

config = importlib.import_module('aria.config')
data_dir = importlib.import_module('aria.data_dir')
runtime = importlib.import_module('aria.runtime')
approval = importlib.import_module('aria.approval')
dispatch = importlib.import_module('aria.tools.dispatch')
taskqueue = importlib.import_module('aria.agent.taskqueue')

CALLS = []


def _probe_allowed(a):
    CALLS.append(('probe_allowed', dict(a)))
    return 'ALLOWED_OK'


def _probe_blocked(a):
    CALLS.append(('probe_blocked', dict(a)))
    return 'BLOCKED_OK'


def _probe_email(a):
    CALLS.append(('send_email', dict(a)))
    return 'EMAIL_OK'


dispatch.register_tool('probe_allowed', _probe_allowed)
dispatch.register_tool('probe_blocked', _probe_blocked)
dispatch.register_tool('send_email', _probe_email)

ENV = {"allowed_tools": ["probe_allowed", "send_email"],
       "pre_approved_irreversible": [],
       "spend_cap": 5.0,
       "mission_id": 7,
       "goal": "envelope proof"}


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(data_dir, '_ROOT', str(tmp_path / 'ARIA'))
    CALLS.clear()
    dispatch.reset_turn_state()
    approval._PENDING.clear()
    approval._RESOLUTIONS.clear()
    approval.tool_set_approval_mode('auto')
    yield
    CALLS.clear()
    dispatch.reset_turn_state()
    approval._PENDING.clear()
    approval.tool_set_approval_mode('auto')


# -- runtime inheritance --------------------------------------------

def test_execution_inherits_cap_scope_envelope():
    with runtime.execution('mission', budget_scope='mission:7',
                           spend_cap=5.0, envelope=dict(ENV)):
        with runtime.execution('tool'):
            ctx = runtime.current()
            assert ctx.spend_cap == 5.0
            assert ctx.budget_scope == 'mission:7'
            assert ctx.envelope == ENV


def test_execution_explicit_args_win():
    with runtime.execution('mission', budget_scope='mission:7',
                           spend_cap=5.0, envelope=dict(ENV)):
        with runtime.execution('tool', spend_cap=9.0):
            ctx = runtime.current()
            assert ctx.spend_cap == 9.0
            # An explicit cap is a different budget: it does NOT
            # inherit the parent's scope.
            assert ctx.budget_scope != 'mission:7'
        with runtime.execution('tool', envelope=None):
            # envelope=None is "unspecified" here, so it inherits —
            # there is no way to spell "no envelope" inside one.
            assert runtime.current().envelope == ENV


def test_execution_without_parent_is_unchanged():
    with runtime.execution('tool'):
        ctx = runtime.current()
        assert ctx.spend_cap is None
        assert ctx.envelope is None


# -- dispatch envelope ----------------------------------------------

def test_dispatch_envelope_parks_nested_call():
    with runtime.execution('mission', budget_scope='mission:7',
                           spend_cap=5.0, envelope=dict(ENV)):
        res, _ok = dispatch.execute_tool(
            'probe_blocked', {}, preauthorized=True)
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert CALLS == [], 'out-of-envelope call executed'
    pending = approval.pending_entries()
    assert len(pending) == 1 and pending[0]['tool'] == 'probe_blocked'
    assert "outside the mission's envelope" in pending[0]['desc']
    assert 'Mission #7' in pending[0]['desc']


def test_dispatch_envelope_allows_listed_tool():
    with runtime.execution('mission', budget_scope='mission:7',
                           spend_cap=5.0, envelope=dict(ENV)):
        res, _ok = dispatch.execute_tool(
            'probe_allowed', {}, preauthorized=True)
    assert res == 'ALLOWED_OK', res
    assert CALLS == [('probe_allowed', {})]


def test_dispatch_envelope_irreversible_needs_preapproval():
    env = dict(ENV)
    # send_email is listed but irreversible and NOT pre-approved.
    with runtime.execution('mission', budget_scope='mission:7',
                           spend_cap=5.0, envelope=env):
        res, _ok = dispatch.execute_tool(
            'send_email', {'to': 'x@y.z', 'subject': 's', 'body': 'b'},
            preauthorized=True)
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert CALLS == []
    # Pre-approving it lets the same call run.
    env2 = dict(ENV)
    env2['pre_approved_irreversible'] = ['send_email']
    with runtime.execution('mission', budget_scope='mission:7',
                           spend_cap=5.0, envelope=env2):
        res, _ok = dispatch.execute_tool(
            'send_email', {'to': 'x@y.z', 'subject': 's', 'body': 'b'},
            preauthorized=True)
    assert res == 'EMAIL_OK', res
    assert CALLS == [('send_email', {'to': 'x@y.z', 'subject': 's',
                                    'body': 'b'})]


def test_dispatch_envelope_approve_replay_executes():
    with runtime.execution('mission', budget_scope='mission:7',
                           spend_cap=5.0, envelope=dict(ENV)):
        res, _ok = dispatch.execute_tool(
            'probe_blocked', {}, preauthorized=True)
    assert res.startswith('[AWAITING_APPROVAL token='), res
    token = res.split('token=', 1)[1].split(']', 1)[0]
    out = approval.approve_token(token)
    assert CALLS == [('probe_blocked', {})], CALLS
    assert 'BLOCKED_OK' in out, out


def test_dispatch_no_envelope_runs_freely():
    res, _ok = dispatch.execute_tool(
        'probe_blocked', {}, preauthorized=True)
    assert res == 'BLOCKED_OK', res
    assert CALLS == [('probe_blocked', {})]


# -- delegated tasks -------------------------------------------------

class _RecordingExecutor:
    def __init__(self):
        self.seen = []
        self.done = threading.Event()

    def execute(self, goal, speak, cancel_flag, **kwargs):
        ctx = runtime.current()
        self.seen.append((ctx.spend_cap, ctx.budget_scope,
                          ctx.envelope))
        self.done.set()
        return 'TASK_DONE'


def _run_queue(queue, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if not queue._queue and queue._active_count == 0:
            return True
        time.sleep(0.05)
    return False


def test_taskqueue_inherits_cap_scope_envelope():
    seen = _RecordingExecutor()
    queue = taskqueue.TaskQueue(
        max_concurrent=1, executor_factory=lambda: seen)
    queue.start()
    try:
        with runtime.execution('mission', budget_scope='mission:7',
                               spend_cap=5.0, envelope=dict(ENV)):
            queue.submit(goal='delegated probe')
        assert seen.done.wait(10), 'delegated task never ran'
        assert _run_queue(queue)
        assert seen.seen == [(5.0, 'mission:7', ENV)], seen.seen
    finally:
        queue.stop()


def test_taskqueue_uncapped_submit_is_unchanged():
    seen = _RecordingExecutor()
    queue = taskqueue.TaskQueue(
        max_concurrent=1, executor_factory=lambda: seen)
    queue.start()
    try:
        queue.submit(goal='uncapped probe')
        assert seen.done.wait(10), 'delegated task never ran'
        assert _run_queue(queue)
        cap, scope, env = seen.seen[0]
        assert cap is None and env is None, seen.seen
        assert scope.startswith('task:'), scope
    finally:
        queue.stop()
