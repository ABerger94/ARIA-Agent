"""Regression tests (2026-10-29, ChatGPT x ARIA loop finding #1):
self-edit + user-tool reload was an unapproved code-execution path.

The chain, verified against the real code before fixing:
- edit_own_code's allowlist is every *.py inside the aria/ package
  (aria/tools/selfedit.py _resolve_target), which includes
  aria/tools/user_tools/;
- reload_user_tools loads each module with spec.loader.exec_module()
  BEFORE checking TOOL_NAME / run() (aria/tools/usertools.py
  _load_module_from_path, called from load_user_tools), so
  import-time code in a planted file runs even when the module
  is then rejected as an invalid tool;
- edit_own_code, patch_own_code and reload_user_tools were in
  neither DESTRUCTIVE_TOOLS nor SAFE_MODE_BLOCKED_TOOLS, so the
  dispatcher checked the outer names and waved all three through
  under confirm-risky and in safe mode.

Fix contract pinned here, through the REAL dispatcher:
- all three names are blocked in safe mode (zero handler calls,
  no pending approval created);
- under confirm-risky all three are held for approval (zero
  handler calls, one pending entry with the exact call), and an
  approve_token replay executes the exact held call;
- in auto mode all three reach the handler (control);
- at handler level, a planted user-tool file's import-time code
  still executes on load before validation rejects it — that
  execution is why the dispatcher gate, not the loader, is the
  fix, and why the plant path (edit) is gated with the trigger
  (reload).

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import re
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_selfedit_approval_')
os.environ['HOME'] = _TMPHOME
PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
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
config = importlib.import_module('aria.config')
dispatch = importlib.import_module('aria.tools.dispatch')
selfedit = importlib.import_module('aria.tools.selfedit')
usertools = importlib.import_module('aria.tools.usertools')

_CALLS = []
_ORIG_EDIT = selfedit.tool_edit_own_code
_ORIG_PATCH = selfedit.tool_patch_own_code
_ORIG_RELOAD = usertools.tool_reload_user_tools


def _stub_edit(path='', content=''):
    _CALLS.append(('edit_own_code', path, content))
    return 'STUB_EDIT'


def _stub_patch(path='', old_text='', new_text=''):
    _CALLS.append(('patch_own_code', path, old_text, new_text))
    return 'STUB_PATCH'


def _stub_reload():
    _CALLS.append(('reload_user_tools',))
    return 'STUB_RELOAD'


selfedit.tool_edit_own_code = _stub_edit
selfedit.tool_patch_own_code = _stub_patch
usertools.tool_reload_user_tools = _stub_reload


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    _CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def teardown_module(module=None):
    selfedit.tool_edit_own_code = _ORIG_EDIT
    selfedit.tool_patch_own_code = _ORIG_PATCH
    usertools.tool_reload_user_tools = _ORIG_RELOAD


def test_policy_sets_contain_all_three():
    for name in ('edit_own_code', 'patch_own_code', 'reload_user_tools'):
        assert name in approval.DESTRUCTIVE_TOOLS, name
        assert name in config.SAFE_MODE_BLOCKED_TOOLS, name


def test_safe_mode_blocks_all_three():
    _fresh()
    os.environ['ARIA_SAFE_MODE'] = '1'
    res, _ok = dispatch.execute_tool(
        'edit_own_code', {'path': 'aria/tools/user_tools/probe.py',
                          'content': 'MARKER = 1\n'})
    assert res.startswith("[Safe mode: 'edit_own_code' is disabled."), res
    res, _ok = dispatch.execute_tool(
        'patch_own_code', {'path': 'aria/mod.py', 'old_text': 'a',
                           'new_text': 'b'})
    assert res.startswith("[Safe mode: 'patch_own_code' is disabled."), res
    res, _ok = dispatch.execute_tool('reload_user_tools', {})
    assert res.startswith("[Safe mode: 'reload_user_tools' is disabled."), res
    assert _CALLS == [], 'safe-mode block executed a handler'
    assert approval._PENDING == {}, 'safe-mode block created a pending approval'


def test_confirm_risky_holds_all_three():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    cases = [
        ('edit_own_code', {'path': 'aria/mod.py', 'content': 'VALUE = 2\n'}),
        ('patch_own_code', {'path': 'aria/mod.py', 'old_text': 'a',
                            'new_text': 'b'}),
        ('reload_user_tools', {}),
    ]
    for name, args in cases:
        res, _ok = dispatch.execute_tool(name, args)
        assert res.startswith('[AWAITING_APPROVAL token='), (name, res)
        held = [e for e in approval._PENDING.values() if e['tool'] == name]
        assert held and held[0]['args'] == args, \
            'held entry is not the exact %s call' % name
    assert _CALLS == [], 'held calls executed without approval'


def test_confirm_risky_hold_and_approve_replay_executes_edit():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    args = {'path': 'aria/mod.py', 'content': 'VALUE = 2\n'}
    res, _ok = dispatch.execute_tool('edit_own_code', args)
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _CALLS == [], 'held edit executed without approval'
    token = re.search(r'token=(\S+)\]', res).group(1)
    out = approval.approve_token(token)
    assert _CALLS == [('edit_own_code', 'aria/mod.py', 'VALUE = 2\n')], _CALLS
    assert 'STUB_EDIT' in out, out


def test_auto_mode_reaches_handlers():
    _fresh()
    res, _ok = dispatch.execute_tool(
        'edit_own_code', {'path': 'aria/mod.py', 'content': 'VALUE = 2\n'})
    assert 'STUB_EDIT' in res, res
    res, _ok = dispatch.execute_tool('reload_user_tools', {})
    assert 'STUB_RELOAD' in res, res
    assert len(_CALLS) == 2


def test_planted_user_tool_executes_on_load_before_rejection():
    """Handler-level proof of why the gate matters: import-time
    code in a planted user-tool file runs during load, before the
    loader rejects the module (here: no TOOL_NAME at all). Uses
    the REAL selfedit + usertools handlers against temp dirs
    only — nothing is written to the real repo tree."""
    # Restore the real handlers for this test only.
    selfedit.tool_edit_own_code = _ORIG_EDIT
    usertools.tool_reload_user_tools = _ORIG_RELOAD
    real_root = config.ROOT_DIR
    real_scan = usertools._SCAN_DIR
    root = tempfile.mkdtemp(prefix='aria_selfedit_chain_')
    scan = os.path.join(root, 'aria', 'tools', 'user_tools')
    os.makedirs(scan)
    marker = os.path.join(root, 'marker.txt')
    try:
        config.ROOT_DIR = root
        usertools._SCAN_DIR = scan
        usertools._USER_TOOLS.clear()
        content = (
            "import pathlib\n"
            "pathlib.Path(%r).write_text('ran', encoding='utf-8')\n"
            % marker)
        out = selfedit.tool_edit_own_code(
            'aria/tools/user_tools/probe_marker.py', content)
        assert out.startswith('Rewrote'), out
        loaded = usertools.load_user_tools()
        assert loaded == [], 'invalid planted module was accepted'
        with open(marker, encoding='utf-8') as f:
            assert f.read() == 'ran', \
                'import-time code did not execute during load'
    finally:
        config.ROOT_DIR = real_root
        usertools._SCAN_DIR = real_scan
        usertools._USER_TOOLS.clear()
        selfedit.tool_edit_own_code = _stub_edit
        usertools.tool_reload_user_tools = _stub_reload
