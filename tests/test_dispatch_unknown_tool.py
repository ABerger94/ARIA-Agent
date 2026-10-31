"""Regression tests (2026-10-27, ChatGPT x ARIA loop finding #1):
the dispatcher's fuzzy tool-name recovery used to EXECUTE the closest
real tool directly, bypassing every check that ran against the
requested (misspelled) name — argument validation, safe mode, and
approval. A typo'd `send_emai` ran the real send_email handler with no
approval under confirm-risky and no block under safe mode.

Fix contract pinned here: unknown names are rejected, _closest_tool is
suggestion-only, and ZERO handler invocations happen for a misspelled
name in every mode. The correctly-spelled controls prove the gates
themselves still work, so a green run can't be an artifact of the
whole dispatcher being broken.

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import types
import tempfile

_TMPHOME = tempfile.mkdtemp(prefix='aria_unknown_tool_')
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
import importlib
approval = importlib.import_module('aria.approval')
dispatch = importlib.import_module('aria.tools.dispatch')

_CALLS = []
_ORIG_SEND_EMAIL = dispatch._REGISTRY.get('send_email')


def _stub_send_email(args):
    _CALLS.append(dict(args or {}))
    return 'STUB_EMAIL_SENT'


dispatch._REGISTRY['send_email'] = _stub_send_email

ARGS = {'to': 'nobody@example.com', 'subject': 't', 'body': 'b'}


def _fresh():
    dispatch._TURN_CALLS.clear()
    _CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)


def teardown_module(module=None):
    if _ORIG_SEND_EMAIL is not None:
        dispatch._REGISTRY['send_email'] = _ORIG_SEND_EMAIL
    else:
        dispatch._REGISTRY.pop('send_email', None)


# ------------------------------------------------------------ the bypass

def test_misspelled_destructive_never_executes_confirm_risky():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    res, _ok = dispatch.execute_tool('send_emai', dict(ARGS))
    assert _CALLS == [], f'fuzzy match executed the handler: {_CALLS}'
    assert 'STUB_EMAIL_SENT' not in res
    assert 'Unknown tool: send_emai' in res, res
    assert "Did you mean 'send_email'" in res, res
    assert 'NOT run' in res, res
    # Control: the correct spelling IS gated under confirm-risky.
    res2, _ = dispatch.execute_tool('send_email', dict(ARGS))
    assert '[AWAITING_APPROVAL token=' in res2, res2
    assert _CALLS == [], 'control call executed without approval'


def test_misspelled_blocked_tool_never_executes_in_safe_mode():
    _fresh()
    approval.tool_set_approval_mode('auto')
    os.environ['ARIA_SAFE_MODE'] = '1'
    try:
        res, _ok = dispatch.execute_tool('send_emai', dict(ARGS))
        assert _CALLS == [], f'safe-mode bypass: handler ran: {_CALLS}'
        assert 'Unknown tool: send_emai' in res, res
        # Control: the correct spelling IS hard-blocked in safe mode.
        res2, _ = dispatch.execute_tool('send_email', dict(ARGS))
        assert '[Safe mode:' in res2, res2
        assert _CALLS == []
    finally:
        os.environ.pop('ARIA_SAFE_MODE', None)


def test_misspelled_with_missing_args_never_executes():
    _fresh()
    approval.tool_set_approval_mode('auto')
    res, _ok = dispatch.execute_tool('send_emai', {})
    assert _CALLS == [], f'handler ran with no args: {_CALLS}'
    assert 'Unknown tool: send_emai' in res, res


def test_misspelled_never_executes_even_in_auto_mode():
    _fresh()
    approval.tool_set_approval_mode('auto')
    res, _ok = dispatch.execute_tool('send_emai', dict(ARGS))
    assert _CALLS == [], f'auto-mode fuzzy execution: {_CALLS}'
    assert 'Unknown tool: send_emai' in res, res
    # Control: the correct spelling DOES execute in auto mode — the
    # rejection above is about the unknown name, not a dead dispatcher.
    res2, _ = dispatch.execute_tool('send_email', dict(ARGS))
    assert 'STUB_EMAIL_SENT' in res2, res2
    assert len(_CALLS) == 1


def test_gibberish_name_rejected_without_suggestion():
    _fresh()
    approval.tool_set_approval_mode('auto')
    res, _ok = dispatch.execute_tool('zzzz_no_such_tool', {})
    assert res.startswith('Unknown tool: zzzz_no_such_tool'), res
    assert 'Did you mean' not in res, res
