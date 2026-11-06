"""Routine packs and nested execution have structural bounds
(pass 26 B4#9).

import_pack read every ZIP member fully materialized with no
member-count, per-member-size, or total budget, and
_valid_routine_data accepted any step list — a zip bomb or a
10,000-step routine imported without complaint. And run_routine
re-entered itself through dispatch with no depth counter or
visited set, so a self-recursive routine burned ~1000 frames to
a RecursionError.

Pinned here:
- a pack member larger than the per-member cap is refused
  before it is read, and a pack over the total-bytes budget is
  refused with nothing imported;
- a routine with more than 500 steps, or with a step that is not
  a dict with a string tool, is refused at import;
- a self-recursive routine is refused cleanly (typed FAILED,
  no RecursionError), and nesting past 5 routine levels is
  refused the same way.
"""
import importlib
import json
import os
import sys
import tempfile
import types
import zipfile

_TMPHOME = tempfile.mkdtemp(prefix='aria_routine_pack_bounds_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, 'ARIA')
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
config = importlib.import_module('aria.config')
dispatch = importlib.import_module('aria.tools.dispatch')
routines = importlib.import_module('aria.routines')
from aria.outcome import FAILED, normalize  # noqa: E402

_MARKER_CALLS = []


def _stub_marker(args):
    _MARKER_CALLS.append(dict(args or {}))
    return 'STUB_MARKER'


dispatch._REGISTRY['test_marker'] = _stub_marker

MIB = 1024 * 1024


def _make_pack(path, members):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps({
            "format": routines.PACK_FORMAT, "version": 1,
            "routines": [m[:-5] for m in members]}))
        for name, data in members.items():
            z.writestr(name, data if isinstance(data, bytes)
                       else json.dumps(data))


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    _MARKER_CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def _write_routine(name, steps, trusted=True):
    path = routines._routine_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({"name": name, "trusted": trusted, "steps": steps}, f)


def _routine_data(name, steps):
    return {"name": name, "created": "2026-11-06",
            "trusted": False, "steps": steps}


# -- pack bounds -------------------------------------------------------

def test_zip_bomb_member_refused_before_read(tmp_path):
    _fresh()
    pack = str(tmp_path / 'bomb.zip')
    _make_pack(pack, {'big.json': b'\0' * (2 * MIB)})
    out = routines.import_pack(pack)
    assert 'refused' in out.lower(), out
    assert not os.path.exists(routines._routine_path('big'))


def test_pack_total_budget_refused_with_nothing_imported(tmp_path):
    _fresh()
    members = {f'r{i}.json': b'\0' * MIB for i in range(11)}
    # Each member is exactly at the per-member cap, but together
    # they exceed the 10 MiB total budget.
    pack = str(tmp_path / 'big.zip')
    _make_pack(pack, members)
    out = routines.import_pack(pack)
    assert 'refused' in out.lower(), out
    for i in range(11):
        assert not os.path.exists(routines._routine_path(f'r{i}')), \
            f'r{i} imported despite the refused pack'


def test_oversize_and_misshapen_routines_refused(tmp_path):
    _fresh()
    huge = _routine_data(
        'big', [{"tool": "test_marker", "args": {}}] * 10000)
    bad_step = _routine_data('badstep', [{"tool": "test_marker"},
                                         {"args": {}}])
    bad_tool = _routine_data('badtool', [{"tool": 42, "args": {}}])
    assert routines._valid_routine_data(huge) is False
    assert routines._valid_routine_data(bad_step) is False
    assert routines._valid_routine_data(bad_tool) is False
    assert routines._valid_routine_data(
        _routine_data('ok', [{"tool": "test_marker", "args": {}}])) is True
    pack = str(tmp_path / 'bad.zip')
    _make_pack(pack, {'big.json': huge, 'badstep.json': bad_step,
                      'badtool.json': bad_tool})
    out = routines.import_pack(pack)
    assert 'rejected: big.json' in out, out
    assert 'badstep.json' in out, out
    assert 'badtool.json' in out, out
    for name in ('big', 'badstep', 'badtool'):
        assert not os.path.exists(routines._routine_path(name))


# -- nesting bounds ----------------------------------------------------

def test_self_recursive_routine_refused_cleanly():
    _fresh()
    _write_routine('loopy', [
        {"tool": "run_routine",
         "args": {"name": "loopy", "params_json": "{}"}},
        {"tool": "test_marker", "args": {}},
    ])
    out = routines.tool_run_routine('loopy')
    assert normalize(out).status == FAILED, out
    assert 'self-recurs' in out.lower() or 'already running' in out.lower(), out
    assert 'RecursionError' not in out, out
    assert _MARKER_CALLS == [], 'later step ran after the refusal'


def test_nesting_depth_cap_refuses_deep_chains():
    _fresh()
    names = [f'chain{i}' for i in range(6)]
    for k, name in enumerate(names):
        nxt = ({"tool": "run_routine",
                "args": {"name": names[k + 1], "params_json": "{}"}}
               if k + 1 < len(names)
               else {"tool": "test_marker", "args": {}})
        _write_routine(name, [nxt])
    out = routines.tool_run_routine('chain0')
    assert normalize(out).status == FAILED, out
    assert _MARKER_CALLS == [], 'marker ran past the depth cap'


def test_depth_cap_message_names_the_cap():
    # The innermost refusal names the depth cap; the chain test
    # above pins the observable stop, this pins the message
    # (per-level 200-char previews bury it in a deep chain).
    _fresh()
    _write_routine('deep', [{"tool": "test_marker", "args": {}}])
    token = routines._ROUTINE_DEPTH.set(routines.MAX_ROUTINE_DEPTH)
    try:
        out = routines.run_routine_impl('deep')
    finally:
        routines._ROUTINE_DEPTH.reset(token)
    assert normalize(out).status == FAILED, out
    assert 'depth' in out.lower(), out
    assert _MARKER_CALLS == []
