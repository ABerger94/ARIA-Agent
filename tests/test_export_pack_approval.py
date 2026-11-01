"""Regression tests (2026-11-01, ChatGPT x ARIA loop pass 13, fixing
pass 12's runner-up): routine export overwrote arbitrary files with
no approval. export_routine_pack was model-callable and in neither
policy set, and export_pack makedirs()d the destination's parent and
opened ZipFile in 'w' mode — one call silently truncated whatever
file already sat at dest_path.

Fix contract pinned here, through the REAL dispatcher:
- under confirm-risky the call PAUSES for approval, and while it
  waits NOTHING is created: no destination file, no parent dir;
  the pending description names the exact destination, the routine
  names, and whether it would replace an existing file;
- the pending entry freezes the resolved destination (an empty
  dest_path is resolved to the default path at request time, not
  re-derived with a fresh timestamp at replay time), so the
  approve-token replay writes exactly the approved file, once;
- denial and expiry execute nothing;
- in auto mode an existing destination is REFUSED with its bytes
  untouched by default, and replaced only with overwrite=true —
  built in a temp file first, with no temp leftovers;
- an untrusted routine's export step pauses the routine (later
  steps do not run); a trusted routine still exports in auto mode.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import json
import os
import re
import sys
import tempfile
import time
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_export_pack_')
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
routines = importlib.import_module('aria.routines')


def _seed_routine(name='morning_setup', trusted=False, steps=None):
    rdir = routines._routines_dir()
    os.makedirs(rdir, exist_ok=True)
    if steps is None:
        steps = [{'tool': 'noop', 'args': {}}]
    data = {'name': name, 'steps': steps, 'created': '2026-11-01',
            'trusted': trusted}
    with open(os.path.join(rdir, f'{name}.json'), 'w') as f:
        json.dump(data, f)
    return data


def _fresh(mode):
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    approval.tool_set_approval_mode(mode)
    os.environ.pop('ARIA_SAFE_MODE', None)


def _token_of(awaiting_text):
    m = re.search(r'\[AWAITING_APPROVAL token=([^\]]+)\]', awaiting_text)
    assert m, f'no token in: {awaiting_text[:300]}'
    return m.group(1)


def _pack_dest(tmp_path):
    return str(tmp_path / 'subdir' / 'pack.zip')


# ------------------------------------------------------------ the gate

def test_confirm_risky_pauses_and_creates_nothing(tmp_path):
    _fresh('confirm-risky')
    _seed_routine()
    dest = _pack_dest(tmp_path)
    res, _ok = dispatch.execute_tool(
        'export_routine_pack',
        {'names_csv': 'morning_setup', 'dest_path': dest})
    assert '[AWAITING_APPROVAL token=' in res, res
    assert dest in res, res  # the description names the exact file
    assert 'morning_setup' in res, res
    assert not os.path.exists(dest), 'destination written before approval'
    assert not os.path.exists(os.path.dirname(dest)), \
        'parent dir created while awaiting approval'


def test_approve_writes_exactly_the_frozen_file_once(tmp_path):
    _fresh('confirm-risky')
    _seed_routine()
    dest = _pack_dest(tmp_path)
    res, _ok = dispatch.execute_tool(
        'export_routine_pack',
        {'names_csv': 'morning_setup', 'dest_path': dest})
    token = _token_of(res)
    # The pending entry froze the destination: replay cannot move it.
    assert approval._PENDING[token]['args']['dest_path'] == \
        os.path.abspath(dest)
    out = approval.approve_token(token)
    assert 'Exported 1 routine(s)' in out, out
    assert os.path.exists(dest)
    import zipfile
    with zipfile.ZipFile(dest) as z:
        assert 'morning_setup.json' in z.namelist()
        assert json.loads(z.read('manifest.json'))['routines'] == \
            ['morning_setup']


def test_empty_dest_is_frozen_at_request_time(tmp_path):
    _fresh('confirm-risky')
    _seed_routine()
    res, _ok = dispatch.execute_tool(
        'export_routine_pack', {'names_csv': 'morning_setup', 'dest_path': ''})
    token = _token_of(res)
    frozen = approval._PENDING[token]['args']['dest_path']
    assert frozen, 'empty dest was not frozen into the pending entry'
    assert frozen in res, 'description does not name the frozen file'
    out = approval.approve_token(token)
    assert 'Exported 1 routine(s)' in out, out
    assert os.path.exists(frozen), 'replay wrote somewhere other than frozen'


def test_deny_and_expiry_execute_nothing(tmp_path):
    _fresh('confirm-risky')
    _seed_routine()
    dest = _pack_dest(tmp_path)
    res, _ok = dispatch.execute_tool(
        'export_routine_pack',
        {'names_csv': 'morning_setup', 'dest_path': dest})
    out = approval.deny_token(_token_of(res))
    assert 'Denied' in out, out
    assert not os.path.exists(dest)

    res2, _ok = dispatch.execute_tool(
        'export_routine_pack',
        {'names_csv': 'morning_setup', 'dest_path': dest})
    token2 = _token_of(res2)
    approval._PENDING[token2]['created_ts'] = time.time() - 601
    out2 = approval.approve_token(token2)
    assert 'unknown or expired' in out2, out2
    assert not os.path.exists(dest), 'expired approval still executed'


# ------------------------------------------------------------ exclusive creation

def test_auto_existing_file_refused_bytes_unchanged(tmp_path):
    _fresh('auto')
    _seed_routine()
    dest = _pack_dest(tmp_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    sentinel = b'KEEP ME - NOT A PACK'
    with open(dest, 'wb') as f:
        f.write(sentinel)
    res, _ok = dispatch.execute_tool(
        'export_routine_pack',
        {'names_csv': 'morning_setup', 'dest_path': dest})
    assert 'already exists' in res, res
    with open(dest, 'rb') as f:
        assert f.read() == sentinel


def test_auto_overwrite_replaces_via_temp_no_leftovers(tmp_path):
    _fresh('auto')
    _seed_routine()
    dest = _pack_dest(tmp_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(b'OLD BYTES')
    res, _ok = dispatch.execute_tool(
        'export_routine_pack',
        {'names_csv': 'morning_setup', 'dest_path': dest,
         'overwrite': True})
    assert 'Exported 1 routine(s)' in res, res
    import zipfile
    assert zipfile.is_zipfile(dest)
    leftovers = [n for n in os.listdir(os.path.dirname(dest))
                 if n.startswith('.routines-pack-')]
    assert leftovers == [], f'temp build files left behind: {leftovers}'


def test_no_match_creates_nothing(tmp_path):
    _fresh('auto')
    dest = _pack_dest(tmp_path)
    assert routines.export_pack(['ghost'], dest) == \
        '[No matching routines to export.]'
    assert not os.path.exists(dest)
    assert not os.path.exists(os.path.dirname(dest))


# ------------------------------------------------------------ routine replay

def test_untrusted_routine_export_step_pauses(tmp_path):
    _fresh('confirm-risky')
    dest = _pack_dest(tmp_path)
    _seed_routine(
        steps=[{'tool': 'export_routine_pack',
                'args': {'names_csv': 'morning_setup',
                         'dest_path': dest}},
               {'tool': 'list_routines', 'args': {}}],
        trusted=False)
    out = routines.run_routine_impl('morning_setup')
    assert 'PAUSED' in out, out
    assert not os.path.exists(dest), 'untrusted export ran without approval'


def test_trusted_routine_still_exports_in_auto(tmp_path):
    _fresh('auto')
    dest = _pack_dest(tmp_path)
    _seed_routine(
        steps=[{'tool': 'export_routine_pack',
                'args': {'names_csv': 'morning_setup',
                         'dest_path': dest}}],
        trusted=True)
    out = routines.run_routine_impl('morning_setup')
    assert 'Exported 1 routine(s)' in out, out
    assert os.path.exists(dest)
