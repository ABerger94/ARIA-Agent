"""Desktop GUI approvals bind to the foreground window (pass 25 P1#10).

Before this fix an approval stored only tool + args + desc: a
gui_click was coordinates, a gui_type was a char count, and the
replay clicked / typed into whatever happened to be on screen
up to 10 minutes later — no foreground-window capture anywhere.

Fix contract pinned here, through the real dispatcher (the GUI
handlers are stubbed: on this Linux host the real handlers
no-op as PyAutoGUI-unavailable, so the stub is what proves the
gate — zero stub calls means zero clicks):
- parking a gui_click / gui_type records the foreground window
  (winctl.foreground_window) and names it in the banner desc;
- approving while the same window is foreground executes the
  exact held call;
- approving after the window changed (or when it can no longer
  be read) refuses: nothing executes, the resolution is
  journaled as 'refused', and the model is told to ask again;
- when no window can be read at park time (a platform with no
  window concept), the approval has no binding and proceeds as
  before — the fix does not break GUI tools where there is
  nothing to bind to;
- the binding survives the pending-approval file round-trip.

Run per-file (own process), like every suite in this repo.
"""
import json
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_gui_binding_')
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
config = importlib.import_module('aria.config')
dispatch = importlib.import_module('aria.tools.dispatch')
winctl = importlib.import_module('aria.tools.winctl')
builtins_mod = importlib.import_module('aria.tools.builtins')

_GUI_CALLS = []
_ORIG_CLICK = builtins_mod.tool_gui_click
_ORIG_TYPE = builtins_mod.tool_gui_type
_ORIG_FG = winctl.foreground_window


def _stub_click(x, y):
    _GUI_CALLS.append(('click', x, y))
    return 'STUB_CLICK'


def _stub_type(text):
    _GUI_CALLS.append(('type', text))
    return 'STUB_TYPE'


builtins_mod.tool_gui_click = _stub_click
builtins_mod.tool_gui_type = _stub_type

NOTEPAD = {"hwnd": 11, "title": "Notepad", "process": "notepad.exe"}
CHROME = {"hwnd": 22, "title": "Google Chrome", "process": "chrome.exe"}


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    approval._RESOLUTIONS.clear()
    _GUI_CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')
    winctl.foreground_window = _ORIG_FG


def teardown_module(module=None):
    builtins_mod.tool_gui_click = _ORIG_CLICK
    builtins_mod.tool_gui_type = _ORIG_TYPE
    winctl.foreground_window = _ORIG_FG


def _park_click():
    approval.tool_set_approval_mode('confirm-risky')
    res, _ok = dispatch.execute_tool('gui_click', {'x': 10, 'y': 20})
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _GUI_CALLS == [], 'held click executed without approval'
    token = res.split('token=', 1)[1].split(']', 1)[0]
    return token


def test_park_records_foreground_window():
    _fresh()
    winctl.foreground_window = lambda: dict(NOTEPAD)
    token = _park_click()
    held = approval._PENDING[token]
    assert held['gui_binding'] == NOTEPAD, held
    assert held['desc'] == "click at screen coordinates (10, 20) in window 'Notepad'", held
    assert approval.pending_entries()[0]['gui_binding'] == NOTEPAD


def test_approve_replay_executes_when_window_unchanged():
    _fresh()
    winctl.foreground_window = lambda: dict(NOTEPAD)
    token = _park_click()
    out = approval.approve_token(token)
    assert _GUI_CALLS == [('click', 10, 20)], _GUI_CALLS
    assert 'STUB_CLICK' in out, out


def test_approve_replay_refuses_when_window_changed():
    _fresh()
    winctl.foreground_window = lambda: dict(NOTEPAD)
    token = _park_click()
    winctl.foreground_window = lambda: dict(CHROME)
    out = approval.approve_token(token)
    assert _GUI_CALLS == [], 'click executed into the wrong window'
    assert out.startswith('[Approval refused:'), out
    assert 'Notepad' in out and 'Google Chrome' in out, out
    refused = [r for r in approval._RESOLUTIONS
               if r['verdict'] == 'refused']
    assert refused and refused[0]['tool'] == 'gui_click', \
        approval._RESOLUTIONS


def test_approve_replay_refuses_when_window_unreadable():
    _fresh()
    winctl.foreground_window = lambda: dict(NOTEPAD)
    token = _park_click()
    winctl.foreground_window = lambda: None
    out = approval.approve_token(token)
    assert _GUI_CALLS == []
    assert out.startswith('[Approval refused:'), out
    assert 'could not be read' in out, out


def test_no_binding_at_park_proceeds():
    # A platform with no window concept has nothing to bind to —
    # the approval proceeds as before (control).
    _fresh()
    winctl.foreground_window = lambda: None
    token = _park_click()
    assert approval._PENDING[token]['gui_binding'] is None
    out = approval.approve_token(token)
    assert _GUI_CALLS == [('click', 10, 20)], _GUI_CALLS
    assert 'STUB_CLICK' in out, out


def test_binding_survives_pending_file_round_trip():
    _fresh()
    winctl.foreground_window = lambda: dict(NOTEPAD)
    token = _park_click()
    approval._save_pending()
    with open(approval._pending_file(), encoding='utf-8') as f:
        saved = json.load(f)['entries']
    assert saved and saved[0].get('gui_binding') == NOTEPAD, saved
    # Restore into a fresh in-memory table and refuse on a changed
    # window — the binding outlives a restart.
    approval._PENDING.clear()
    approval._pending_loaded = False
    assert approval.load_pending() == 1
    assert approval._PENDING[token]['gui_binding'] == NOTEPAD
    winctl.foreground_window = lambda: dict(CHROME)
    out = approval.approve_token(token)
    assert _GUI_CALLS == []
    assert out.startswith('[Approval refused:'), out
