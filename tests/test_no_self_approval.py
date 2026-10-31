"""Regression tests (2026-10-28, ChatGPT x ARIA loop pass 2
runner-up 2, fixed at Alek's order): the model could grant its
own approvals. `approve`, `deny` and `set_approval_mode` were
ordinary model-callable tools sitting in NEVER_GATED, so one
brain turn could run a destructive call, take the AWAITING
token, and approve it itself — or simply set the mode to auto.
The only restraint was a system-prompt sentence ("Never approve
your own pending actions without the user saying yes"), and a
prompt is not a gate.

Fix contract pinned here: those three names are NOT in the
tool registry and NOT in the model-facing schemas, so a model
turn calling them is rejected as unknown and nothing executes,
nothing is denied, and the mode does not move. The trusted
path still works: aria.approval.approve_token — the exact
function the dashboard confirm banner calls on a human click —
executes the stashed call. The read-only views
(list_pending_approvals, get_approval_mode) stay available.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import re
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_no_self_approval_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir_v20
_data_dir_v20._ROOT = os.path.join(_TMPHOME, 'ARIA')  # V20: state stores resolve via data_dir (install root), not HOME

tools_pkg = types.ModuleType('aria.tools')
tools_pkg.__path__ = [PKG + '/tools']
sys.modules['aria.tools'] = tools_pkg
agent_pkg = types.ModuleType('aria.agent')
agent_pkg.__path__ = [PKG + '/agent']
sys.modules['aria.agent'] = agent_pkg
_ps = types.ModuleType('psutil')
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules['psutil'] = _ps
for _name in ('aria.scheduler', 'aria.vision', 'aria.hardware',
              'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)

approval = importlib.import_module('aria.approval')
dispatch = importlib.import_module('aria.tools.dispatch')
schemas = importlib.import_module('aria.tools.schemas')

_CALLS = []
_ORIG_SEND_EMAIL = dispatch._REGISTRY.get('send_email')


def _stub_send_email(args):
    _CALLS.append(dict(args or {}))
    return 'STUB_EMAIL_SENT'


dispatch._REGISTRY['send_email'] = _stub_send_email

ARGS = {'to': 'nobody@example.com', 'subject': 't', 'body': 'b'}
REMOVED = ('approve', 'deny', 'set_approval_mode')
KEPT_READONLY = ('list_pending_approvals', 'get_approval_mode')


def _fresh():
    dispatch._TURN_CALLS.clear()
    _CALLS.clear()
    approval._PENDING.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('confirm-risky')


def teardown_module(module=None):
    if _ORIG_SEND_EMAIL is not None:
        dispatch._REGISTRY['send_email'] = _ORIG_SEND_EMAIL
    else:
        dispatch._REGISTRY.pop('send_email', None)


def _held_token():
    """Run a destructive call into the approval hold; return its token."""
    res, _ok = dispatch.execute_tool('send_email', dict(ARGS))
    assert 'AWAITING_APPROVAL' in res
    assert _CALLS == []
    m = re.search(r'token=([0-9a-f]+)', res)
    assert m, res
    return m.group(1)


# ------------------------------------------------------------ the bypass

def test_resolvers_are_not_registered_or_declared():
    for name in REMOVED:
        assert name not in dispatch._REGISTRY, name
    declared = {d['name'] for d in schemas.ALL_FUNCTION_DECLARATIONS}
    for name in REMOVED:
        assert name not in declared, name
    for name in KEPT_READONLY:
        assert name in dispatch._REGISTRY, name
        assert name in declared, name


def test_model_cannot_approve_its_own_pending_call():
    _fresh()
    token = _held_token()
    res, _ok = dispatch.execute_tool('approve', {'token': token})
    assert _CALLS == [], f'model self-approved and executed: {_CALLS}'
    assert 'STUB_EMAIL_SENT' not in res
    # The request is still waiting for an actual human.
    assert token in approval.list_pending()


def test_model_cannot_deny_or_relax_the_mode():
    _fresh()
    token = _held_token()
    dispatch.execute_tool('deny', {'token': token})
    assert token in approval.list_pending()
    dispatch.execute_tool(
        'set_approval_mode', {'mode': 'auto'})
    assert approval.get_mode() == 'confirm-risky'
    assert _CALLS == []


def test_trusted_banner_path_still_executes():
    """approve_token is the function the dashboard banner calls
    on a click — that path must keep working end to end."""
    _fresh()
    token = _held_token()
    out = approval.approve_token(token)
    assert _CALLS == [ARGS]
    assert 'STUB_EMAIL_SENT' in out
    assert token not in approval.list_pending()
