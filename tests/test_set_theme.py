"""set_theme — the voice path to the dashboard palettes
(2026-11-04, Alek's bug report).

The five palettes were mouse-only (Customize dialog / Settings
buttons): aria/tools had no theme tool at all, so a spoken
"change the theme to earthy minimal" had no action to perform
and ARIA confirmed a change that never happened. This suite
pins the fix:

* palettes.resolve_palette_name — exact id/name, tolerant
  token scoring ('earthy mineral' still lands on Earthy
  Minimal), ties are ambiguous, gibberish is unknown;
* the set_theme dispatch — a hit writes the prefs and the
  result NAMES the palette; unknown/ambiguous change nothing
  and list all five; never a success-shaped error;
* the ui_prefs palette-change listeners — they fire with the
  id after a landed write, and a raising listener cannot
  break the write;
* the dashboard subscription — a prefs write re-applies the
  theme on the live window through apply_accent (the
  Customize dialog's own apply path).

Run per-file (own process), like every suite in this repo:
    python3 -m pytest tests/test_set_theme.py
"""
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_set_theme_")
os.environ["HOME"] = _TMPHOME
os.environ["QT_QPA_PLATFORM"] = "offscreen"

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, "ARIA")

for _sub in ("tools", "agent"):
    _m = types.ModuleType(f"aria.{_sub}")
    _m.__path__ = [os.path.join(PKG, _sub)]
    sys.modules[f"aria.{_sub}"] = _m

_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules["psutil"] = _ps

# Heavy leaves dispatch's registry build would otherwise drag
# in (same stub set as test_dispatch_unknown_tool.py).
for _name in ("aria.scheduler", "aria.vision", "aria.hardware",
              "aria.spotify", "aria.hud", "aria.speech"):
    sys.modules[_name] = types.ModuleType(_name)

import importlib

import pytest

palettes = importlib.import_module("aria.palettes")
ui_prefs = importlib.import_module("aria.ui_prefs")
dispatch = importlib.import_module("aria.tools.dispatch")
dispatch.set_spine_hook(lambda *a, **k: None)
dispatch.set_log_hook(lambda *a, **k: None)

ALL_FIVE = ["Apple Modern", "Vibrant Sunset", "Ocean Breeze",
            "Earthy Minimal", "Midnight Neon"]


@pytest.fixture
def prefs(tmp_path, monkeypatch):
    """Hermetic ui.json for ui_prefs (the test_palettes
    redirect: _ui_file is read at call time)."""
    target = tmp_path / "ui.json"
    monkeypatch.setattr(ui_prefs, "_ui_file", lambda: str(target))
    return target


def _run_theme(name):
    dispatch.reset_turn_state("set-theme-test")
    res, _flag = dispatch.execute_tool(
        "set_theme", {"name": name}, preauthorized=True)
    return res


# --- resolution -------------------------------------------------

def test_resolve_exact_id_and_name():
    assert palettes.resolve_palette_name("earthy_minimal") \
        == ("hit", "earthy_minimal")
    assert palettes.resolve_palette_name("EARTHY_MINIMAL") \
        == ("hit", "earthy_minimal")
    assert palettes.resolve_palette_name("Earthy Minimal") \
        == ("hit", "earthy_minimal")
    assert palettes.resolve_palette_name("  midnight   neon ") \
        == ("hit", "midnight_neon")
    assert palettes.resolve_palette_name("APPLE MODERN") \
        == ("hit", "apple_modern")


def test_resolve_unique_token_and_subset():
    assert palettes.resolve_palette_name("ocean") \
        == ("hit", "ocean_breeze")
    assert palettes.resolve_palette_name("neon") \
        == ("hit", "midnight_neon")
    assert palettes.resolve_palette_name("the ocean breeze") \
        == ("hit", "ocean_breeze")


def test_resolve_fuzzy_near_miss_still_hits():
    # Alek's own phrasing: 'mineral' is not a palette token,
    # but 'earthy' scores 1 against Earthy Minimal and 0
    # against everything else — a unique max wins.
    assert palettes.resolve_palette_name("earthy mineral") \
        == ("hit", "earthy_minimal")


def test_resolve_tie_is_ambiguous():
    status, match = palettes.resolve_palette_name("modern neon")
    assert status == "ambiguous"
    assert set(match) == {"apple_modern", "midnight_neon"}


def test_resolve_gibberish_is_unknown():
    assert palettes.resolve_palette_name("zanzibar quantum") \
        == ("unknown", [])
    assert palettes.resolve_palette_name("") == ("unknown", [])
    assert palettes.resolve_palette_name(None) == ("unknown", [])


# --- dispatch ---------------------------------------------------

def test_dispatch_hit_writes_prefs_and_names_palette(prefs):
    res = _run_theme("earthy mineral")
    assert res == "Theme set to Earthy Minimal."
    assert ui_prefs.get_palette() == "earthy_minimal"
    # id + hero-as-accent land together, as the dialog writes.
    assert ui_prefs.get_accent() == "#c96f4f"


def test_dispatch_exact_name_hit(prefs):
    res = _run_theme("Ocean Breeze")
    assert res == "Theme set to Ocean Breeze."
    assert ui_prefs.get_palette() == "ocean_breeze"


def test_dispatch_unknown_changes_nothing_and_lists_five(prefs):
    ui_prefs.set_palette("ocean_breeze")
    res = _run_theme("zanzibar quantum")
    assert res.startswith("Theme NOT changed")
    for name in ALL_FIVE:
        assert name in res
    assert ui_prefs.get_palette() == "ocean_breeze"
    assert ui_prefs.get_accent() == "#3ba7f2"


def test_dispatch_ambiguous_changes_nothing(prefs):
    ui_prefs.set_palette("midnight_neon")
    res = _run_theme("modern neon")
    assert res.startswith("Theme NOT changed")
    assert "Apple Modern" in res and "Midnight Neon" in res
    assert ui_prefs.get_palette() == "midnight_neon"


def test_dispatch_declared_and_in_core_toolkit():
    from aria.tools import schemas
    decls = schemas.get_tool_decls_by_name()
    assert "set_theme" in decls
    assert "set_theme" in dispatch.get_registered_tools()
    assert "set_theme" in schemas.TOOLKITS["core"]["tools"]


# --- ui_prefs listeners ------------------------------------------

def test_listener_fires_with_id_after_write(prefs):
    events = []
    cb = ui_prefs.on_palette_change(events.append)
    try:
        res = dispatch.tool_set_theme({"name": "vibrant sunset"})
        assert res == "Theme set to Vibrant Sunset."
        assert events == ["vibrant_sunset"]
        ui_prefs.set_palette(None)
        assert events == ["vibrant_sunset", None]
    finally:
        ui_prefs.remove_palette_change(cb)


def test_listener_does_not_fire_on_failed_resolution(prefs):
    events = []
    cb = ui_prefs.on_palette_change(events.append)
    try:
        res = dispatch.tool_set_theme({"name": "bogus words here"})
        assert res.startswith("Theme NOT changed")
        assert events == []
    finally:
        ui_prefs.remove_palette_change(cb)


def test_raising_listener_never_breaks_the_write(prefs):
    events = []

    def boom(pid):
        raise RuntimeError("listener exploded")

    cb_boom = ui_prefs.on_palette_change(boom)
    cb_rec = ui_prefs.on_palette_change(events.append)
    try:
        res = dispatch.tool_set_theme({"name": "earthy minimal"})
        # The write landed, the result is the factual success,
        # and the listener AFTER the raising one still ran.
        assert res == "Theme set to Earthy Minimal."
        assert ui_prefs.get_palette() == "earthy_minimal"
        assert events == ["earthy_minimal"]
    finally:
        ui_prefs.remove_palette_change(cb_boom)
        ui_prefs.remove_palette_change(cb_rec)


# --- dashboard subscription --------------------------------------

def test_dashboard_restyles_live_on_prefs_write(monkeypatch,
                                                tmp_path):
    pytest.importorskip("PyQt6")
    import numpy as np
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    target = tmp_path / "ui.json"
    monkeypatch.setattr(ui_prefs, "_ui_file", lambda: str(target))

    # Module stubs mirroring tests/test_dashboard.py —
    # create_dashboard imports these lazily, at build time.
    calls = []
    data = dict(cpu=25, mem=40, battery="80%", uptime_s=600,
                active_provider="Ollama",
                subsystems=[("Vision", "ONLINE", True)],
                task_active=0, scheduled=1, voice_enabled=True,
                volume=.5)
    frame = types.ModuleType("aria.frame")
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: "10m"
    frame.spotify_data = lambda: {"title": "Test track"}
    frame.media_data = lambda: {"title": "Test track"}
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
    frame.toggle_voice = lambda: calls.append("voice")
    frame.receive_file = lambda p: calls.append(p)
    hud = types.ModuleType("aria.hud")
    hud.CURRENT_STATE = "idle"
    hud.SUBTITLE_TEXT = "Ready"
    hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = calls.append
    hud._dispatch_media_action = calls.append
    hud.set_hud_volume = calls.append
    config = types.ModuleType("aria.config")
    config.redact = str
    config.log_silent = lambda *args: None
    config.get_setting = lambda key, default=None: default
    for name, mod in [("aria.frame", frame), ("aria.hud", hud),
                      ("aria.config", config)]:
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(sys.modules["aria"],
                            name.rsplit(".", 1)[1], mod,
                            raising=False)
    queue = types.ModuleType("aria.agent.taskqueue")
    queue.get_queue = lambda: types.SimpleNamespace(
        get_all_statuses=lambda: [])
    scheduler = types.ModuleType("aria.scheduler")
    scheduler.sched_list = lambda: []
    monkeypatch.setitem(sys.modules, "aria.agent.taskqueue", queue)
    monkeypatch.setitem(sys.modules, "aria.scheduler", scheduler)
    speech = types.ModuleType("aria.speech")
    speech.VOICE_ENABLED = True
    monkeypatch.setitem(sys.modules, "aria.speech", speech)
    monkeypatch.setattr(sys.modules["aria"], "speech", speech,
                        raising=False)
    callbacks = types.SimpleNamespace(
        ptt_press=lambda: None, ptt_release=lambda: None,
        interrupt=lambda: None, toggle_whisper=lambda: None,
        submit=lambda t: None, request_stop=lambda: None,
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3),
                                      dtype=np.uint8))

    from aria.dashboard import create_dashboard
    window = create_dashboard(callbacks)
    try:
        seen = []
        # Spy on the dialog's own apply path: theme_finished
        # must reach the window through apply_accent.
        window.apply_accent = seen.append
        ui_prefs.set_palette("earthy_minimal")
        app.processEvents()
        assert seen == ["#c96f4f"]  # Earthy Minimal's hero
        ui_prefs.set_palette(None)
        app.processEvents()
        # Cleared palette => the default resolution (Midnight
        # Neon's hero) is what gets applied.
        assert seen[-1] == "#22d3ee"
    finally:
        window.close()
        app.processEvents()
