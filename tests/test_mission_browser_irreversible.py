"""Pass 27 #6: a mission envelope allowing browser_agent must not
let an irreversible click through without the grant.

_is_irreversible classified by tool name only, so an auto-mode
envelope allowing browser_agent executed a "Delete account"
click with an empty pre_approved_irreversible — the browser
guard's auto lane clicks with no approval at all. The
classification is now action-aware: click/press are
irreversible-class, reads/navigation/typing/scroll/screenshots
are not. Both the mission executor and the dispatch envelope
check use it.
"""
import importlib
import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMPHOME = tempfile.mkdtemp(prefix='aria_mission_browser_irrev_')
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
missions = importlib.import_module('aria.missions')

CALLS = []


def _stub_browser(a):
    CALLS.append(('browser_agent', dict(a)))
    return 'BROWSER_OK'


dispatch.register_tool('browser_agent', _stub_browser)

ENV = {"allowed_tools": ["browser_agent"],
       "pre_approved_irreversible": [],
       "spend_cap": 5.0,
       "mission_id": 7,
       "goal": "browser proof"}


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


def test_classification_is_action_aware():
    assert missions._is_irreversible(
        'browser_agent', {'action': 'click'})
    assert missions._is_irreversible(
        'browser_agent', {'action': 'press'})
    assert missions._is_irreversible(
        'browser_agent', {'action': 'Click'})
    for benign in ({'action': 'read'}, {'action': 'open'},
                   {'action': 'type'}, {'action': 'scroll'},
                   {'action': 'screenshot'}, {}, None):
        assert not missions._is_irreversible('browser_agent', benign), benign
    # Controls: name-based classification is unchanged.
    assert missions._is_irreversible('send_email')
    assert not missions._is_irreversible('read_file')


def test_dispatch_envelope_parks_browser_click_without_grant():
    with runtime.execution('mission', budget_scope='mission:7',
                           spend_cap=5.0, envelope=dict(ENV)):
        res, _ok = dispatch.execute_tool(
            'browser_agent', {'action': 'click', 'target': 'Delete account'},
            preauthorized=True)
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert CALLS == []


def test_dispatch_envelope_parks_browser_press_without_grant():
    with runtime.execution('mission', budget_scope='mission:7',
                           spend_cap=5.0, envelope=dict(ENV)):
        res, _ok = dispatch.execute_tool(
            'browser_agent', {'action': 'press', 'key': 'Enter'},
            preauthorized=True)
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert CALLS == []


def test_dispatch_envelope_runs_browser_read_freely():
    with runtime.execution('mission', budget_scope='mission:7',
                           spend_cap=5.0, envelope=dict(ENV)):
        res, _ok = dispatch.execute_tool(
            'browser_agent', {'action': 'read'}, preauthorized=True)
    assert res == 'BROWSER_OK', res
    assert CALLS == [('browser_agent', {'action': 'read'})]


def test_dispatch_envelope_preapproved_click_runs():
    env = dict(ENV)
    env['pre_approved_irreversible'] = ['browser_agent']
    with runtime.execution('mission', budget_scope='mission:7',
                           spend_cap=5.0, envelope=env):
        res, _ok = dispatch.execute_tool(
            'browser_agent', {'action': 'click', 'target': 'Delete account'},
            preauthorized=True)
    assert res == 'BROWSER_OK', res
    assert CALLS == [('browser_agent', {'action': 'click',
                                        'target': 'Delete account'})]
