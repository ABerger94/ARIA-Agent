"""Live brain-state delegation (2026-10-27, ChatGPT x ARIA loop
runner-up #2): aria.agent used to from-import BUSY_PROCESSING,
LAST_ACTIVITY, and CONVERSATION_HISTORY from brain, snapshotting
them at package import while brain REBINDS all three as she runs.
Package readers (main's scheduler / heartbeat / consolidation
callbacks) therefore saw busy as permanently False, a diverging
activity stamp, and a stale history list after any compaction.

Pinned here against the REAL package __init__ and REAL brain:
attribute access on aria.agent returns brain's CURRENT binding,
in both directions, including across a history rebind.

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import types
import tempfile

os.environ['HOME'] = tempfile.mkdtemp(prefix='aria_agent_state_')
PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
# NOTE: aria.agent itself is NOT pre-registered — its real
# __init__.py must execute for these tests to mean anything.
tools_pkg = types.ModuleType('aria.tools')
tools_pkg.__path__ = [os.path.join(PKG, 'tools')]
sys.modules['aria.tools'] = tools_pkg
_ps = types.ModuleType('psutil')
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules['psutil'] = _ps
for _name in ('aria.vision', 'aria.hardware', 'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)
_sp = types.ModuleType('aria.spotify')
_sp.tool_media_key = lambda *a, **k: ''
_sp.tool_spotify = lambda *a, **k: ''
sys.modules['aria.spotify'] = _sp

import aria.agent as agent
from aria.agent import brain


def test_busy_flag_delegates_live():
    brain.BUSY_PROCESSING = False
    assert agent.BUSY_PROCESSING is False
    brain.BUSY_PROCESSING = True   # what run_agent does at turn start
    assert agent.BUSY_PROCESSING is True
    brain.BUSY_PROCESSING = False  # and at turn end
    assert agent.BUSY_PROCESSING is False


def test_last_activity_delegates_live():
    brain.LAST_ACTIVITY = 4242.5
    assert agent.LAST_ACTIVITY == 4242.5
    brain.LAST_ACTIVITY = 5151.25  # brain's own per-turn stamp
    assert agent.LAST_ACTIVITY == 5151.25


def test_history_delegates_across_rebind():
    brain.CONVERSATION_HISTORY = []
    # main's history hook appends through the package reference —
    # the entry must land in brain's current list.
    agent.CONVERSATION_HISTORY.append({'parts': [{'text': 'hi'}]})
    assert brain.CONVERSATION_HISTORY == [{'parts': [{'text': 'hi'}]}]
    # Compaction REBINDS the list in brain; the package must follow
    # to the new object, not keep serving the old one.
    brain.CONVERSATION_HISTORY = brain.CONVERSATION_HISTORY[-1:]
    assert agent.CONVERSATION_HISTORY is brain.CONVERSATION_HISTORY
    agent.CONVERSATION_HISTORY.append({'parts': [{'text': 'yo'}]})
    assert len(brain.CONVERSATION_HISTORY) == 2


def test_package_surface_intact():
    assert callable(agent.run_agent)
    assert callable(agent.build_system_instruction)
    assert callable(agent.proactive_heartbeat_loop)
    assert callable(agent.idle_consolidation_loop)
    assert 'BUSY_PROCESSING' in dir(agent)
    try:
        agent.NO_SUCH_THING_XYZ
        raise AssertionError('unknown attribute did not raise')
    except AttributeError:
        pass
