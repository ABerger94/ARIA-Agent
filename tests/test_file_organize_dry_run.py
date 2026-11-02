"""Regression tests (2026-11-02, ChatGPT x ARIA loop pass 15 finding #1):
file_organize's dry_run flag disagreed between the approval seam and the
execution seam, so a malformed value moved files without approval.

Mechanism, confirmed against the code at 3e3e2e4d: approval._as_bool
mapped null (and unknown strings) to the preview default, so
needs_approval("file_organize", {"dry_run": None}) was False under
confirm-risky; the dispatch registry then ran bool(None) == False and
the handler executed shutil.move(). The same coercion turned "" / [] /
{} into executes, and turned an approved string "false" into a preview
(the approved move silently did not happen).

Fix contract pinned here, through the REAL dispatcher:
- needs_approval pauses for every present non-True dry_run value
  (False, null, "", [], {}, "false", 0, 1); only an omitted dry_run
  or an explicit boolean True is a preview;
- the handler rejects every non-boolean dry_run with an error and
  moves nothing, including on an approve-token replay of a held
  malformed call (trusted-routine replay rides preauthorized past
  the gate, so the handler is the load-bearing seam there);
- the legitimate paths are unchanged: omitted/True previews with no
  moves, False holds under confirm-risky and executes exactly once
  after approval, and moves files in auto mode.

Run per-file (own process), like every suite in this repo.
"""
import os
import re
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_file_organize_dry_run_')
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
              'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)
sys.modules['aria.vision'].get_face_frame_jpeg = lambda: None
sys.modules['aria.hud'].tool_show_commands = lambda: 'ok'
sys.modules['aria.speech'].edge_tts_bytes = lambda text: b''
import importlib
approval = importlib.import_module('aria.approval')
config = importlib.import_module('aria.config')
dispatch = importlib.import_module('aria.tools.dispatch')
fileops = importlib.import_module('aria.tools.fileops')

_MALFORMED = (None, '', [], {}, 'false', 'true', 0, 1, 0.0)


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def _make_dir():
    tmp = tempfile.mkdtemp(prefix='aria_organize_')
    for name in ('photo.jpg', 'doc.pdf', 'tune.mp3'):
        with open(os.path.join(tmp, name), 'wb') as f:
            f.write(b'x')
    return tmp


def _top_files(tmp):
    return sorted(f for f in os.listdir(tmp)
                  if os.path.isfile(os.path.join(tmp, f)))


def _extract_token(awaiting_str):
    m = re.search(r'token=([0-9a-f]{8})', awaiting_str)
    assert m, f'no token in: {awaiting_str[:200]}'
    return m.group(1)


def test_approval_classifies_only_true_or_omitted_as_preview():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    assert approval.needs_approval('file_organize', {}) is False
    assert approval.needs_approval('file_organize',
                                   {'dry_run': True}) is False
    assert approval.needs_approval('file_organize',
                                   {'dry_run': False}) is True
    for bad in _MALFORMED:
        assert approval.needs_approval('file_organize',
                                       {'dry_run': bad}) is True, bad


def test_handler_rejects_malformed_dry_run_without_moving():
    _fresh()
    for bad in _MALFORMED:
        tmp = _make_dir()
        out = fileops.tool_file_organize(tmp, dry_run=bad)
        assert 'must be true or false' in out, (bad, out)
        assert _top_files(tmp) == ['doc.pdf', 'photo.jpg', 'tune.mp3'], bad
        assert sorted(os.listdir(tmp)) == _top_files(tmp), bad  # no dirs


def test_dispatcher_null_dry_run_holds_then_rejects_on_approval():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    tmp = _make_dir()
    res, _ok = dispatch.execute_tool(
        'file_organize', {'directory': tmp, 'dry_run': None})
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _top_files(tmp) == ['doc.pdf', 'photo.jpg', 'tune.mp3']
    held = [e for e in approval._PENDING.values()
            if e['tool'] == 'file_organize']
    assert held and held[0]['args'] == {'directory': tmp, 'dry_run': None}
    out = approval.approve_token(_extract_token(res))
    assert 'must be true or false' in out, out
    assert _top_files(tmp) == ['doc.pdf', 'photo.jpg', 'tune.mp3'], \
        'approved malformed call moved files'


def test_legitimate_paths_unchanged():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    # Omitted dry_run previews without approval and moves nothing.
    tmp = _make_dir()
    res, _ok = dispatch.execute_tool('file_organize', {'directory': tmp})
    assert 'Dry run only' in res, res
    assert _top_files(tmp) == ['doc.pdf', 'photo.jpg', 'tune.mp3']
    # False holds, then executes exactly once on approval.
    dispatch._TURN_CALLS.clear()
    tmp = _make_dir()
    res, _ok = dispatch.execute_tool(
        'file_organize', {'directory': tmp, 'dry_run': False})
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _top_files(tmp) == ['doc.pdf', 'photo.jpg', 'tune.mp3']
    out = approval.approve_token(_extract_token(res))
    assert 'Organized 3/3' in out, out
    assert _top_files(tmp) == [], _top_files(tmp)
    # True previews in auto mode and moves nothing.
    _fresh()
    tmp = _make_dir()
    res, _ok = dispatch.execute_tool(
        'file_organize', {'directory': tmp, 'dry_run': True})
    assert 'Dry run only' in res, res
    assert _top_files(tmp) == ['doc.pdf', 'photo.jpg', 'tune.mp3']
