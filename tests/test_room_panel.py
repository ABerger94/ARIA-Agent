"""ARIA's Room panel (Alek, 2026-10-29): renders every anim/mode
from a fixture state without error, caption follows the state,
polling runs only while visible, missing state paints the quiet
room, and ui_prefs round-trips the 'room' centerpiece.
"""
import json
import os
import sys
import types
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PyQt6')
from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

_APP = QApplication.instance() or QApplication([])

from aria.room_panel import RoomWidget

BASE = {
    "version": 1, "updated_at": "2026-10-29T14:00:00-04:00",
    "mode": "active", "pos_x": 0.4, "facing": 1, "anim": "read",
    "activity": {"name": "read", "label": "Reading at the chair"},
    "drives": {"energy": 71, "curiosity": 34,
               "restlessness": 12, "comfort": 58},
    "mood": "content", "current_thought": "the plant is winning.",
    "plant": {"thirst": 20, "growth": 3},
    "day": {"date": "2026-10-29", "did": ["read"]},
}


def _state(**kw):
    s = json.loads(json.dumps(BASE))
    s.update(kw)
    return s


@pytest.mark.parametrize("anim,act", [
    ("walk", "pace"), ("idle", "stretch"), ("read", "read"),
    ("write", "write_journal"), ("water", "water_plant"),
    ("think", "look_window"), ("tinker", "tinker"),
    ("listen", "listen_music"), ("sleep", "sleep"),
])
def test_renders_every_anim(tmp_path, anim, act):
    w = RoomWidget(state_path=str(tmp_path / "life_state.json"))
    w.set_state(_state(anim=anim, activity={"name": act, "label": "X"}))
    w.resize(760, 380)
    w.show()
    _APP.processEvents()
    grab = w.canvas.grab()
    assert not grab.isNull()
    w.close()


def test_renders_asleep_and_missing_state(tmp_path):
    w = RoomWidget(state_path=str(tmp_path / "nope.json"))
    w.resize(760, 380)
    w.show()
    _APP.processEvents()
    assert not w.canvas.grab().isNull()       # quiet room, no state
    assert "quiet" in w.caption.text()
    w.set_state(_state(mode="asleep", anim="sleep",
                       activity={"name": "sleep", "label": "Asleep"}))
    assert not w.canvas.grab().isNull()
    w.close()


def test_caption_and_thought_follow_state(tmp_path):
    w = RoomWidget(state_path=str(tmp_path / "life_state.json"))
    w.set_state(_state())
    assert w.caption.text() == "Reading at the chair"
    assert "plant is winning" in w.thought.text()
    w.set_state(_state(current_thought=""))
    assert w.thought.text() == ""
    w.close()


def test_poll_reads_state_file_and_timer_only_when_visible(tmp_path):
    path = tmp_path / "life_state.json"
    path.write_text(json.dumps(_state()), encoding="utf-8")
    w = RoomWidget(state_path=str(path))
    assert not w._timer.isActive()
    w.show()
    _APP.processEvents()
    assert w._timer.isActive()
    assert w.state()["activity"]["name"] == "read"
    assert w.caption.text() == "Reading at the chair"
    w.hide()
    _APP.processEvents()
    assert not w._timer.isActive()
    w.close()


def test_default_state_path_is_install_root(tmp_path, monkeypatch):
    # V20: the room's state file resolves via aria.data_dir
    # (the install root), not ~/ARIA.
    import importlib
    data_dir = importlib.import_module('aria.data_dir')
    monkeypatch.setattr(data_dir, '_ROOT', str(tmp_path))
    from aria.room_panel import default_state_path
    assert default_state_path() == str(
        tmp_path / 'life_state.json')


def test_ui_prefs_room_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from aria import ui_prefs
    assert ui_prefs.set_centerpiece("room") == "Centerpiece: room."
    assert ui_prefs.get_centerpiece() == "room"
    ui_prefs.set_centerpiece("bogus")
    assert ui_prefs.get_centerpiece() == "orb"
