"""Generated-write approval parity, through the REAL dispatcher
(2026-10-28, at Alek's order — the pass-2 residual / pass-3
runner-up, closed).

The gap: dev_agent and code_helper saved generated code by
calling builtins.tool_write_file directly. The dispatcher checks
approval only against the outer tool name, and neither generator
is in DESTRUCTIVE_TOOLS — but write_file is — so a direct
write_file call was held under confirm-risky while the identical
write via a generator landed ungated.

Fix contract pinned here (handler level lives in
tests/test_generated_write_parity.py):
- under confirm-risky, a dev_agent / code_helper save is HELD:
  the result is the AWAITING_APPROVAL text for a pending
  write_file entry carrying the exact filename + content, and
  zero writes reach the writer;
- approving that entry executes the exact write (the save
  completes on the user's tap, via approve_token's replay);
- in auto mode the save lands immediately, exactly once;
- a preauthorized outer call (routine / approve replay) rides
  through: its write is not re-held under confirm-risky.

write_file's registry entry is stubbed with a recorder that
answers with builtins' success-line shape; the provider chain is
stubbed at gaptools._provider_text. Run per-file (own process),
like every suite in this repo.
"""
import os
import sys
import types
import tempfile

_TMPHOME = tempfile.mkdtemp(prefix='aria_gen_write_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
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
gap = importlib.import_module('aria.tools.gaptools')

CODE = "print('generated')"
gap._provider_text = lambda s, p, role="code": \
    f"```python\n{CODE}\n```"

_WRITES = []
_ORIG_WRITE = dispatch._REGISTRY.get('write_file')


def _stub_write(args):
    args = dict(args or {})
    _WRITES.append(args)
    return (f"Successfully wrote {len(str(args.get('content', '')))} "
            f"characters to {args.get('filename', '')}.")


dispatch._REGISTRY['write_file'] = _stub_write


def _fresh():
    dispatch._TURN_CALLS.clear()
    _WRITES.clear()
    approval._PENDING.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def teardown_module(module=None):
    if _ORIG_WRITE is not None:
        dispatch._REGISTRY['write_file'] = _ORIG_WRITE
    else:
        dispatch._REGISTRY.pop('write_file', None)


def _held_write_entries():
    return [e for e in approval._PENDING.values()
            if e['tool'] == 'write_file']


def test_dev_agent_write_is_held_under_confirm_risky():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    res = dispatch.execute_tool(
        'dev_agent', {'description': 'say hi',
                      'output_path': 'gen.py'})[0]
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _WRITES == [], f'held write still landed: {_WRITES}'
    held = _held_write_entries()
    assert len(held) == 1, held
    assert held[0]['args'] == {'filename': 'gen.py',
                               'content': CODE}, held[0]['args']


def test_code_helper_write_is_held_under_confirm_risky():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    res = dispatch.execute_tool(
        'code_helper', {'action': 'write', 'description': 'say hi',
                        'output_path': 'gen.py'})[0]
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _WRITES == [], f'held write still landed: {_WRITES}'
    held = _held_write_entries()
    assert len(held) == 1, held
    assert held[0]['args'] == {'filename': 'gen.py',
                               'content': CODE}, held[0]['args']


def test_approving_the_held_write_executes_the_exact_save():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    dispatch.execute_tool(
        'dev_agent', {'description': 'say hi',
                      'output_path': 'gen.py'})
    held = _held_write_entries()
    assert len(held) == 1
    out = approval.approve_token(held[0]['token'])
    assert 'Successfully wrote' in out, out
    assert _WRITES == [{'filename': 'gen.py', 'content': CODE}], \
        _WRITES


def test_generated_write_lands_immediately_in_auto_mode():
    _fresh()
    res = dispatch.execute_tool(
        'dev_agent', {'description': 'say hi',
                      'output_path': 'gen.py'})[0]
    assert res.startswith('status=done'), res
    assert _WRITES == [{'filename': 'gen.py', 'content': CODE}], \
        _WRITES
    assert _held_write_entries() == []


def test_preauthorized_outer_call_write_is_not_reheld():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    res = dispatch.execute_tool(
        'dev_agent', {'description': 'say hi',
                      'output_path': 'gen.py'},
        preauthorized=True)[0]
    assert res.startswith('status=done'), res
    assert _WRITES == [{'filename': 'gen.py', 'content': CODE}], \
        _WRITES
    assert _held_write_entries() == []
