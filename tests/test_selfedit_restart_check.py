"""Self-edits arm the post-edit restart check (pass 25 P2#18).

_RESTART_NOTE promises a validate/restore/restart check after a
self-edit, but the check lived only in brain.run_agent's finally
— approved, Live and delegated edits never ran it. Dispatch is
the one funnel every path shares, so a successful self-edit arms
the registered check there. The check itself is registered by
brain at import; these tests drive the dispatch seam with a fake
check (never a real restart timer) and pin brain's idempotency
with the timer stubbed.
"""
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_selfedit_restart_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
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
_spotify.tool_media_key = lambda *a, **k: None
_spotify.tool_spotify = lambda *a, **k: None
sys.modules['aria.spotify'] = _spotify
import importlib
config = importlib.import_module('aria.config')
approval = importlib.import_module('aria.approval')
selfedit = importlib.import_module('aria.tools.selfedit')
dispatch = importlib.import_module('aria.tools.dispatch')
brain = importlib.import_module('aria.agent.brain')


def _layout(monkeypatch):
    """A temp repo layout; edits land here, never in the real repo."""
    root = tempfile.mkdtemp(prefix='aria_selfedit_restart_repo_')
    os.makedirs(os.path.join(root, 'aria'))
    with open(os.path.join(root, 'aria', 'mod.py'), 'w') as f:
        f.write('VALUE = 1\n')
    with open(os.path.join(root, 'aria.py'), 'w') as f:
        f.write('# launcher\n')
    soul = os.path.join(root, 'soul.md')
    with open(soul, 'w') as f:
        f.write('# soul\n')
    monkeypatch.setattr(config, 'ROOT_DIR', root)
    monkeypatch.setattr(config, 'SOUL_PATH', soul)
    return root


def _wire(monkeypatch):
    monkeypatch.setattr(
        dispatch, '_REGISTRY', dict(dispatch._REGISTRY))
    dispatch._REGISTRY['edit_own_code'] = lambda a: \
        selfedit.tool_edit_own_code(a.get('path', ''), a.get('content', ''))
    dispatch._REGISTRY['patch_own_code'] = lambda a: \
        selfedit.tool_patch_own_code(a.get('path', ''), a.get('old_text', ''),
                                     a.get('new_text', ''))
    dispatch._REGISTRY['test_echo'] = lambda a: 'echo:' + str(a.get('text', ''))
    approval.tool_set_approval_mode('auto')
    dispatch._TURN_CALLS.clear()


def _fake_check(monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, '_RESTART_CHECK',
                        lambda: calls.append(1))
    return calls


def test_successful_edit_arms_restart_check(monkeypatch):
    _layout(monkeypatch)
    _wire(monkeypatch)
    calls = _fake_check(monkeypatch)
    out, _ = dispatch.execute_tool(
        'edit_own_code', {'path': 'aria/mod.py', 'content': 'VALUE = 2\n'})
    assert 'Rewrote' in out, out
    assert len(calls) == 1


def test_successful_patch_arms_restart_check(monkeypatch):
    _layout(monkeypatch)
    _wire(monkeypatch)
    calls = _fake_check(monkeypatch)
    out, _ = dispatch.execute_tool(
        'patch_own_code', {'path': 'aria/mod.py', 'old_text': 'VALUE = 1',
                           'new_text': 'VALUE = 2'})
    assert 'Patched' in out, out
    assert len(calls) == 1


def test_refused_edit_does_not_arm_restart_check(monkeypatch):
    _layout(monkeypatch)
    _wire(monkeypatch)
    calls = _fake_check(monkeypatch)
    out, _ = dispatch.execute_tool(
        'edit_own_code', {'path': '/etc/passwd', 'content': 'x'})
    assert 'refused' in out.lower(), out
    assert calls == []


def test_other_tools_do_not_arm_restart_check(monkeypatch):
    _layout(monkeypatch)
    _wire(monkeypatch)
    calls = _fake_check(monkeypatch)
    dispatch.execute_tool('test_echo', {'text': 'hi'})
    assert calls == []


def test_brain_check_is_idempotent(monkeypatch):
    # The check announces again on a second call (run_agent's
    # finally still speaks the restart) but never re-arms the
    # timer. Timer + restart process are stubbed: no real restart.
    armed_before = brain._SELF_EDIT_RESTART_ARMED
    monkeypatch.setattr(brain, '_SELF_EDIT_RESTART_ARMED', False)
    monkeypatch.setattr(brain, '_snapshot_hashes',
                        lambda: {'aria/mod.py': 'deadbeef'})
    monkeypatch.setattr(brain, '_BOOT_HASHES', {'aria/mod.py': 'cafef00d'})
    monkeypatch.setattr(brain, '_smoke_import_changed', lambda changed: [])
    timers = []

    class FakeTimer:
        def __init__(self, delay, fn):
            timers.append((delay, fn))
            self.daemon = True

        def start(self):
            pass

    monkeypatch.setattr(brain.threading, 'Timer', FakeTimer)
    said1, said2 = [], []
    try:
        brain._maybe_restart_after_self_edit(said1.append)
        assert brain._SELF_EDIT_RESTART_ARMED is True
        assert any('restarting now' in m for m in said1), said1
        assert len(timers) == 1
        brain._maybe_restart_after_self_edit(said2.append)
        assert any('restarting now' in m for m in said2), said2
        assert len(timers) == 1  # no second timer armed
    finally:
        monkeypatch.setattr(brain, '_SELF_EDIT_RESTART_ARMED', armed_before)
