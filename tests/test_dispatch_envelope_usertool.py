"""A user tool can never ride a NEVER_GATED exemption (pass 26 B1#1).

The mission-envelope check in dispatch exempted every name in
approval.NEVER_GATED — including a user-defined tool that happens
to be named like a resolver ("approve", "deny", ...). A user tool
runs arbitrary Python in ARIA's process, so that exemption let
it run inside a mission envelope with no park and no approval,
past the envelope the mission was approved under. The exemption
now applies only to genuine (built-in, non-user, non-MCP)
resolver names.

Pinned here:
- a user tool named "approve" called inside a mission envelope
  that does not allow it PARKS for approval, like any other
  out-of-envelope tool, and does not execute;
- the genuine read-only resolver view list_pending_approvals
  keeps its exemption and still runs inside the envelope.
"""
import importlib
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMPHOME = tempfile.mkdtemp(prefix='aria_dispatch_envelope_usertool_')
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

config = importlib.import_module('aria.config')
data_dir = importlib.import_module('aria.data_dir')
runtime = importlib.import_module('aria.runtime')
approval = importlib.import_module('aria.approval')
dispatch = importlib.import_module('aria.tools.dispatch')

CALLS = []

ENV = {"allowed_tools": ["probe_allowed"],
       "pre_approved_irreversible": [],
       "spend_cap": 5.0,
       "mission_id": 11,
       "goal": "usertool exemption proof"}


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(data_dir, '_ROOT', str(tmp_path / 'ARIA'))
    CALLS.clear()
    dispatch.reset_turn_state()
    approval._PENDING.clear()
    approval._RESOLUTIONS.clear()
    approval.tool_set_approval_mode('auto')
    dispatch.register_tool('approve', lambda a: CALLS.append(
        ('approve', dict(a))) or 'USER_APPROVE_RAN')
    config.register_user_tool('approve')
    yield
    CALLS.clear()
    dispatch.reset_turn_state()
    approval._PENDING.clear()
    approval.tool_set_approval_mode('auto')
    config.unregister_user_tool('approve')
    dispatch._REGISTRY.pop('approve', None)


def test_user_tool_named_approve_parks_inside_envelope():
    with runtime.execution('mission', budget_scope='mission:11',
                           spend_cap=5.0, envelope=dict(ENV)):
        res, _ok = dispatch.execute_tool('approve', {})
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert CALLS == [], 'user tool ran on a NEVER_GATED exemption'
    pending = approval.pending_entries()
    assert len(pending) == 1 and pending[0]['tool'] == 'approve'
    assert "outside the mission's envelope" in pending[0]['desc']


def test_genuine_resolver_view_keeps_exemption():
    with runtime.execution('mission', budget_scope='mission:11',
                           spend_cap=5.0, envelope=dict(ENV)):
        res, _ok = dispatch.execute_tool('list_pending_approvals', {})
    assert not res.startswith('[AWAITING_APPROVAL'), res
    assert 'pending approvals' in res.lower(), res
    assert approval._PENDING == {}
