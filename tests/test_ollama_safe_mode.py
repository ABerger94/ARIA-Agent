"""Pass 36 #14: OLLAMA_SAFE_MODE is defined but never read — the
spec's read-only promise for the local model did not exist in
code. Now: declarations are filtered per actual local attempt
(read-only allowlist), the same capability set is enforced at
dispatch, and a turn a local leg serves refuses action tools at
the call site. A user approval (preauthorized replay) still
executes. This is a behaviour change: local action tools are
unavailable by default.

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import types
import tempfile

_TMPHOME = tempfile.mkdtemp(prefix='aria_safe_mode_')
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
              'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)
_spotify = types.ModuleType('aria.spotify')
_spotify.tool_media_key = lambda *a, **k: ('', True)
_spotify.tool_spotify = lambda *a, **k: ('', True)
sys.modules['aria.spotify'] = _spotify
import importlib
import pytest

from aria import tool_policy
from aria.tools import schemas as _schemas
from aria.tools import dispatch as _dispatch


def _set_mode(monkeypatch, mode):
    """Pin the effective provider mode for the safe-mode probes:
    the helpers import aria.agent.providers.provider_mode lazily,
    so patching the attribute on the real module is enough."""
    providers = importlib.import_module('aria.agent.providers')
    monkeypatch.setattr(providers, 'provider_mode', lambda: mode)
    return providers


# ------------------------------------------------------------ policy

def test_safe_mode_classification():
    assert tool_policy.safe_mode_declared('read_file') is True
    assert tool_policy.safe_mode_declared('write_file') is False
    assert tool_policy.safe_mode_declared('send_email') is False
    assert tool_policy.safe_mode_declared('load_toolkit') is True
    # research is args-aware: list/read replay-safe, run effectful.
    assert tool_policy.safe_mode_allowed(
        'research', {'action': 'list'}) is True
    assert tool_policy.safe_mode_allowed(
        'research', {'action': 'run', 'topic': 'x'}) is False


def test_declarations_filtered_only_in_safe_mode():
    full = _schemas.get_toolkit_declarations({'core'})
    names = [d['name'] for d in full[0]['function_declarations']]
    assert 'write_file' in names  # control: the unfiltered set has it
    safe = _schemas.get_toolkit_declarations({'core'}, safe_mode=True)
    safe_names = [d['name'] for d in safe[0]['function_declarations']]
    assert 'write_file' not in safe_names
    assert 'send_email' not in safe_names
    assert 'read_file' in safe_names
    assert 'load_toolkit' in safe_names
    # Shape-preserving: one group, load_toolkit still appended.
    assert len(safe) == 1 and safe[0]['function_declarations'][-1]['name'] == 'load_toolkit'


def test_filter_declarations_helper_drops_empty_groups():
    decls = [{"function_declarations": [
        {"name": "read_file"}, {"name": "write_file"}]},
        {"function_declarations": [{"name": "send_email"}]}]
    out = _schemas.filter_declarations_for_safe_mode(decls)
    assert out == [{"function_declarations": [{"name": "read_file"}]}]


# ------------------------------------------------------------ dispatch

_CALLS = []


def _stub_handler(name):
    def _h(args):
        _CALLS.append((name, dict(args or {})))
        return f'STUB_{name.upper()}'
    return _h


@pytest.fixture(autouse=True)
def _stub_tools():
    _orig = {}
    for name in ('send_email', 'read_file'):
        _orig[name] = _dispatch._REGISTRY.get(name)
        _dispatch._REGISTRY[name] = _stub_handler(name)
    _dispatch._TURN_CALLS.clear()
    _CALLS.clear()
    yield
    for name, orig in _orig.items():
        if orig is not None:
            _dispatch._REGISTRY[name] = orig
        else:
            _dispatch._REGISTRY.pop(name, None)


def test_dispatch_blocks_action_tool_in_local_only(monkeypatch):
    _set_mode(monkeypatch, 'local_only')
    res, _ = _dispatch.execute_tool(
        'send_email', {'to': 'nobody@example.com', 'subject': 't',
                       'body': 'b'})
    assert 'OLLAMA_SAFE_MODE' in res, res
    assert _CALLS == [], "the handler must never run under safe mode"


def test_dispatch_allows_read_only_in_local_only(monkeypatch):
    _set_mode(monkeypatch, 'local_only')
    res, _ = _dispatch.execute_tool('read_file', {'filename': 'notes.txt'})
    assert res == 'STUB_READ_FILE', res
    assert _CALLS == [('read_file', {'filename': 'notes.txt'})]


def test_dispatch_preauthorized_replay_still_executes(monkeypatch):
    _set_mode(monkeypatch, 'local_only')
    res, _ = _dispatch.execute_tool(
        'send_email', {'to': 'nobody@example.com', 'subject': 't',
                       'body': 'b'}, preauthorized=True)
    assert res == 'STUB_SEND_EMAIL', res
    assert len(_CALLS) == 1


def test_dispatch_does_not_block_in_auto_mode(monkeypatch):
    _set_mode(monkeypatch, 'auto')
    # In auto mode the safe-mode refusal must not fire (the call
    # may park for approval or execute — either way it is not the
    # OLLAMA_SAFE_MODE refusal, and the handler is not refused by
    # this gate).
    res, _ = _dispatch.execute_tool(
        'send_email', {'to': 'nobody@example.com', 'subject': 't',
                       'body': 'b'})
    assert 'OLLAMA_SAFE_MODE' not in res, res


# ------------------------------------------------------------ brain

def test_brain_safe_mode_active_only_in_local_only(monkeypatch):
    brain = importlib.import_module('aria.agent.brain')
    _set_mode(monkeypatch, 'local_only')
    assert brain._ollama_safe_mode_active() is True
    _set_mode(monkeypatch, 'auto')
    assert brain._ollama_safe_mode_active() is False
