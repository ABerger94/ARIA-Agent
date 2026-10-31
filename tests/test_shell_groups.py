"""Shell Home V14 pinned groups + drag reorder (2026-10-31,
Alek's order): the pure entry model in aria/shell_home.py,
the additive ui_prefs migration surface, and the panel wiring
(grouped rendering with headers, Move up/down, Move to group,
drop persistence) with fake stores — no real ui.json except
the one hermetic migration test, which writes its own HOME.
"""
import json
import os
import sys
import types
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)

import pytest
pytest.importorskip("PyQt6")
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import importlib
shell_home = importlib.import_module("aria.shell_home")
shell_panel = importlib.import_module("aria.shell_panel")
ui_prefs = importlib.import_module("aria.ui_prefs")

NOW = datetime(2026, 10, 31, 2, 0)
APPS = [{"name": "Google Chrome", "path": "/x/chrome.lnk"},
        {"name": "Steam", "path": "/x/steam.lnk"},
        {"name": "Calculator", "path": "/x/calc.lnk"},
        {"name": "Discord", "path": "/x/discord.lnk"}]
PATHS = [a["path"] for a in APPS]


# -- pure model ---------------------------------------------------

def test_pin_entries_merge_and_clean():
    entries = shell_home.pin_entries(
        ["/a", " ", "/b", "/a"], {"/b": " Games "})
    assert entries == [{"path": "/a", "group": ""},
                       {"path": "/b", "group": "Games"}]


def test_grouped_entries_first_appearance_order():
    entries = shell_home.pin_entries(
        PATHS, {"/x/steam.lnk": "Games",
                "/x/discord.lnk": "Games",
                "/x/calc.lnk": "Tools"})
    groups = shell_home.grouped_entries(entries)
    assert [g for g, _ in groups] == ["", "Games", "Tools"]
    assert [e["path"] for e in groups[1][1]] == \
        ["/x/steam.lnk", "/x/discord.lnk"]


def test_entries_from_grid_drag_walk_regroups():
    walk = [("header", ""), ("app", "/a"),
            ("header", "Games"), ("app", "/b"), ("app", "/c")]
    entries = shell_home.entries_from_grid(walk)
    assert entries == [{"path": "/a", "group": ""},
                       {"path": "/b", "group": "Games"},
                       {"path": "/c", "group": "Games"}]


def test_move_entry_and_set_entry_group():
    entries = shell_home.pin_entries(PATHS, {})
    moved = shell_home.move_entry(entries, "/x/calc.lnk", -1)
    assert [e["path"] for e in moved] == \
        ["/x/chrome.lnk", "/x/calc.lnk", "/x/steam.lnk",
         "/x/discord.lnk"]
    clamped = shell_home.move_entry(entries, "/x/chrome.lnk", -5)
    assert clamped[0]["path"] == "/x/chrome.lnk"
    grouped = shell_home.set_entry_group(
        entries, "/x/steam.lnk", "Games")
    grouped = shell_home.set_entry_group(
        grouped, "/x/discord.lnk", "Games")
    assert [e["path"] for e in grouped[-2:]] == \
        ["/x/steam.lnk", "/x/discord.lnk"]
    assert all(e["group"] == "Games" for e in grouped[-2:])


# -- ui_prefs migration surface -----------------------------------

def test_ui_prefs_legacy_pins_migrate_ungrouped(tmp_path,
                                                monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ui_dir = tmp_path / "ARIA"
    ui_dir.mkdir()
    # A pre-V14 file: plain ordered path list, no group map.
    (ui_dir / "ui.json").write_text(json.dumps(
        {"pinned_apps": ["/x/steam.lnk", "/x/calc.lnk"]}))
    assert ui_prefs.get_pinned_entries() == [
        {"path": "/x/steam.lnk", "group": ""},
        {"path": "/x/calc.lnk", "group": ""}]
    ui_prefs.set_pin_groups({"/x/calc.lnk": "Tools"})
    assert ui_prefs.get_pinned_entries() == [
        {"path": "/x/steam.lnk", "group": ""},
        {"path": "/x/calc.lnk", "group": "Tools"}]
    # Unpinning prunes the orphaned group in the same write.
    ui_prefs.set_pinned_apps(["/x/steam.lnk"])
    assert ui_prefs.get_pin_groups() == {}


# -- panel wiring ---------------------------------------------------

def _panel(groups=None, **overrides):
    store = {"paths": list(PATHS), "groups": dict(groups or {})}
    kwargs = dict(
        apps=APPS,
        on_launch=lambda app: None,
        on_command=lambda text: None,
        now_provider=lambda: NOW,
        briefing_provider=lambda: None,
        research_provider=lambda: "",
        weather_provider=lambda: None,
        pinned_provider=lambda: list(store["paths"]),
        pinned_writer=lambda paths: store.__setitem__(
            "paths", list(paths)),
        pin_groups_provider=lambda: dict(store["groups"]),
        pin_groups_writer=lambda g: store.__setitem__(
            "groups", dict(g)),
        approval_provider=lambda: [],
        reminder_provider=lambda: [],
        files_provider=lambda: [],
        history_provider=lambda: {},
        history_writer=lambda label: None,
        tasks_provider=lambda: [],
        today_remote_provider=lambda: {"event": None,
                                       "unread": None})
    kwargs.update(overrides)
    panel = shell_panel.ShellPanel(**kwargs)
    panel._store = store
    return panel


def _items(panel):
    out = []
    for i in range(panel.app_list.count()):
        item = panel.app_list.item(i)
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and "group_header" in data:
            out.append(("header", data["group_header"]))
        else:
            out.append(("app", data["name"]))
    return out


def test_ungrouped_home_is_the_v8_grid():
    panel = _panel()
    assert panel.app_list.viewMode() == \
        panel.app_list.ViewMode.IconMode
    assert _items(panel) == [("app", a["name"]) for a in APPS]


def test_grouped_home_renders_headers():
    panel = _panel({"/x/steam.lnk": "Games",
                    "/x/discord.lnk": "Games"})
    assert _items(panel) == [
        ("header", ""), ("app", "Google Chrome"),
        ("app", "Calculator"),
        ("header", "Games"), ("app", "Steam"),
        ("app", "Discord")]


def test_move_pin_up_persists_order():
    panel = _panel()
    panel.move_pin(APPS[2], -1)  # Calculator up
    assert panel._store["paths"] == \
        ["/x/chrome.lnk", "/x/calc.lnk", "/x/steam.lnk",
         "/x/discord.lnk"]


def test_move_pin_to_group_persists_and_renders():
    panel = _panel()
    panel.move_pin_to_group(APPS[1], "Games")
    assert panel._store["groups"] == {"/x/steam.lnk": "Games"}
    assert ("header", "Games") in _items(panel)


def test_drop_walk_persists_order_and_group():
    panel = _panel({"/x/steam.lnk": "Games",
                    "/x/discord.lnk": "Games"})
    # Simulate the painted result of a drag: Calculator moved
    # below the Games header (take + insert), then the same
    # rowsMoved hook a real drop fires.
    lst = panel.app_list
    idx = next(i for i in range(lst.count())
               if isinstance(lst.item(i).data(
                   Qt.ItemDataRole.UserRole), dict)
               and lst.item(i).data(
                   Qt.ItemDataRole.UserRole).get("name")
               == "Calculator")
    item = lst.takeItem(idx)
    lst.insertItem(lst.count(), item)
    panel._on_rows_moved()
    entries = shell_home.pin_entries(panel._store["paths"],
                                     panel._store["groups"])
    calc = next(e for e in entries
                if e["path"] == "/x/calc.lnk")
    assert calc["group"] == "Games"
    assert entries[-1]["path"] == "/x/calc.lnk"
