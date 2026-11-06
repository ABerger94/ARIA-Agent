"""Malformed tool arguments must never become executable defaults
(pass 25, pass 23 runner-up reconfirmed in pass 24).

Before the fix, both provider parsers replaced invalid JSON or
non-object args with {} — and {} then reached handlers with
their defaults: media_key's schema had 'required': [], so a
malformed call toggled playback via the registry lambda's
play_pause default. Now the parsers preserve the failure as an
_args_parse_error marker the dispatcher rejects before
validation, gating, or any handler runs, and media_key requires
an action. Zero handler invocations is the proof in every case.

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import types
import tempfile

_TMPHOME = tempfile.mkdtemp(prefix='aria_malformed_args_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir_v20
_data_dir_v20._ROOT = os.path.join(_TMPHOME, 'ARIA')

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
from aria.tools.sandbox import missing_required_args  # noqa: E402

_CALLS = []
_ORIG_MEDIA_KEY = dispatch._REGISTRY.get('media_key')
_ORIG_FAKE = dispatch._REGISTRY.get('fake_probe')


def _stub_media_key(args):
    _CALLS.append(('media_key', dict(args or {})))
    return 'STUB_MEDIA_KEY_SENT'


def _stub_fake_probe(args):
    _CALLS.append(('fake_probe', dict(args or {})))
    return 'STUB_PROBE_RAN'


dispatch._REGISTRY['media_key'] = _stub_media_key
dispatch._REGISTRY['fake_probe'] = _stub_fake_probe


def _fresh():
    dispatch._TURN_CALLS.clear()
    _CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def teardown_module(module=None):
    if _ORIG_MEDIA_KEY is not None:
        dispatch._REGISTRY['media_key'] = _ORIG_MEDIA_KEY
    else:
        dispatch._REGISTRY.pop('media_key', None)
    if _ORIG_FAKE is not None:
        dispatch._REGISTRY['fake_probe'] = _ORIG_FAKE
    else:
        dispatch._REGISTRY.pop('fake_probe', None)


def test_parse_error_marker_never_executes():
    _fresh()
    res, _ok = dispatch.execute_tool(
        'fake_probe', {'_args_parse_error': True})
    assert _CALLS == [], f'malformed call executed the handler: {_CALLS}'
    assert '[Argument error:' in res, res
    assert 'could not be parsed' in res, res
    assert 'STUB_PROBE_RAN' not in res


def test_parse_error_marker_rejected_even_in_auto_mode():
    # Auto mode skips approvals by design; the parse-error
    # rejection is not an approval — it must hold everywhere.
    _fresh()
    res, _ok = dispatch.execute_tool(
        'media_key', {'_args_parse_error': True})
    assert _CALLS == [], f'malformed media_key pressed a key: {_CALLS}'
    assert '[Argument error:' in res, res


def test_media_key_empty_args_are_rejected():
    _fresh()
    assert missing_required_args('media_key', {}) == ['action']
    res, _ok = dispatch.execute_tool('media_key', {})
    assert _CALLS == [], f'empty media_key pressed a key: {_CALLS}'
    assert '[Argument error:' in res, res
    assert 'requires action' in res, res


def test_media_key_with_action_still_executes():
    # Control: the rejection above is about the missing action,
    # not a dead dispatcher.
    _fresh()
    res, _ok = dispatch.execute_tool('media_key', {'action': 'mute'})
    assert 'STUB_MEDIA_KEY_SENT' in res, res
    assert len(_CALLS) == 1
    assert _CALLS[0][1] == {'action': 'mute'}
