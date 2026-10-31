"""Shell Home V14 pinned groups + drag reorder (2026-10-31,
Alek's order): the pure entry model in aria/shell_home.py,
the additive ui_prefs migration surface, and the panel wiring
(grouped rendering with headers, Move up/down, Move to group,
drop persistence) with fake stores — no real ui.json except
the one hermetic migration test, which writes its own HOME.

V17-B (2026-10-31, Alek's ask — groups should run ACROSS, not
down): the grouped rendering changed from the single vertical
list to horizontal group shelves, Move up/down became Move
left/right (in-group), and drop persistence walks shelves.
The tests below were updated where that design legitimately
changed them; the pure V14 model (move_entry, entries_from_
grid) is unchanged and still covered.
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


# -- V17-B pure shelf model ---------------------------------------

def test_shelf_sections_round_trip():
    # Stored entries are always group-contiguous (every
    # writer regroups); the round trip is stated over that
    # form.
    entries = shell_home.regroup(shell_home.pin_entries(
        PATHS, {"/x/steam.lnk": "Games",
                "/x/discord.lnk": "Games",
                "/x/calc.lnk": "Tools"}))
    sections = shell_home.pin_sections(entries)
    assert [g for g, _m in sections] == ["", "Games", "Tools"]
    walk = [(g, [e["path"] for e in members])
            for g, members in sections]
    assert shell_home.entries_from_shelves(walk) == entries


def test_move_entry_in_group_never_crosses_boundary():
    entries = shell_home.regroup(shell_home.pin_entries(
        PATHS, {"/x/steam.lnk": "Games",
                "/x/discord.lnk": "Games"}))
    # Steam is the FIRST Games tile: moving left must NOT push
    # it into the ungrouped block above.
    moved = shell_home.move_entry_in_group(
        entries, "/x/steam.lnk", -1)
    assert [e["path"] for e in moved] == \
        [e["path"] for e in entries]
    # Discord slides left WITHIN Games.
    moved = shell_home.move_entry_in_group(
        entries, "/x/discord.lnk", -1)
    games = [e["path"] for e in moved if e["group"] == "Games"]
    assert games == ["/x/discord.lnk", "/x/steam.lnk"]
    # Calculator (last ungrouped) cannot move right into Games.
    moved = shell_home.move_entry_in_group(
        entries, "/x/calc.lnk", 1)
    assert [e["path"] for e in moved] == \
        ["/x/chrome.lnk", "/x/calc.lnk", "/x/steam.lnk",
         "/x/discord.lnk"]


def test_move_entry_to_section_drops_into_shelf():
    entries = shell_home.regroup(shell_home.pin_entries(
        PATHS, {"/x/steam.lnk": "Games",
                "/x/discord.lnk": "Games"}))
    moved = shell_home.move_entry_to_section(
        entries, "/x/calc.lnk", "Games", 0)
    calc = next(e for e in moved if e["path"] == "/x/calc.lnk")
    assert calc["group"] == "Games"
    games = [e["path"] for e in moved if e["group"] == "Games"]
    assert games == ["/x/calc.lnk", "/x/steam.lnk",
                     "/x/discord.lnk"]
    # A drop past the end clamps to the shelf's end.
    moved = shell_home.move_entry_to_section(
        entries, "/x/chrome.lnk", "Games", 99)
    games = [e["path"] for e in moved if e["group"] == "Games"]
    assert games[-1] == "/x/chrome.lnk"


# -- ui_prefs migration surface -----------------------------------

def test_ui_prefs_legacy_pins_migrate_ungrouped(tmp_path,
                                                monkeypatch):
    # V19: this file now sits at the LEGACY (home) location —
    # pre-V19 the store itself lived there. The canonical
    # store is the install root; the first read merges the
    # legacy prefs in, and the entry view is unchanged.
    home = tmp_path / "home"
    home.mkdir()
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(ui_prefs, "_INSTALL_ROOT",
                        str(install))
    ui_dir = home / "ARIA"
    ui_dir.mkdir()
    # A pre-V14 file: plain ordered path list, no group map.
    (ui_dir / "ui.json").write_text(json.dumps(
        {"pinned_apps": ["/x/steam.lnk", "/x/calc.lnk"]}))
    assert ui_prefs.get_pinned_entries() == [
        {"path": "/x/steam.lnk", "group": ""},
        {"path": "/x/calc.lnk", "group": ""}]
    # The merge landed at the canonical location.
    data = json.loads((install / "ui.json").read_text(
        encoding="utf-8"))
    assert data["pinned_apps"] == ["/x/steam.lnk",
                                   "/x/calc.lnk"]
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


def _shelf_items(panel):
    """[(group, [tile names])] as the shelves paint them."""
    out = []
    for group, lst in panel._shelf_lists:
        names = [lst.item(i).data(
            Qt.ItemDataRole.UserRole)["name"]
            for i in range(lst.count())]
        out.append((group, names))
    return out


def test_ungrouped_home_is_the_v8_grid():
    panel = _panel()
    assert panel.app_list.viewMode() == \
        panel.app_list.ViewMode.IconMode
    assert _items(panel) == [("app", a["name"]) for a in APPS]
    # V17-B hard constraint: no shelves, grid page showing.
    assert panel._shelf_lists == []
    assert panel.grid_stack.currentWidget() is panel.app_list


def test_grouped_home_renders_shelves():
    # V17-B replaces V14's test_grouped_home_renders_headers:
    # groups are horizontal IconMode shelves now, not header
    # rows in one vertical list.
    panel = _panel({"/x/steam.lnk": "Games",
                    "/x/discord.lnk": "Games"})
    assert panel.grid_stack.currentWidget() is \
        panel.shelf_scroll
    assert _shelf_items(panel) == [
        ("", ["Google Chrome", "Calculator"]),
        ("Games", ["Steam", "Discord"])]
    for _group, lst in panel._shelf_lists:
        assert lst.viewMode() == lst.ViewMode.IconMode
        assert lst.gridSize().width() == 120
        assert lst.gridSize().height() == 122


def test_move_pin_up_persists_order():
    panel = _panel()
    panel.move_pin(APPS[2], -1)  # Calculator left (was "up")
    assert panel._store["paths"] == \
        ["/x/chrome.lnk", "/x/calc.lnk", "/x/steam.lnk",
         "/x/discord.lnk"]


def test_move_pin_to_group_persists_and_renders():
    panel = _panel()
    panel.move_pin_to_group(APPS[1], "Games")
    assert panel._store["groups"] == {"/x/steam.lnk": "Games"}
    assert ("Games", ["Steam"]) in _shelf_items(panel)


def test_within_shelf_drag_persists_order():
    panel = _panel({"/x/steam.lnk": "Games",
                    "/x/discord.lnk": "Games"})
    # Simulate the painted result of a within-shelf drag:
    # Discord pulled before Steam on the Games shelf (take +
    # insert), then the same rowsMoved hook a real drop
    # fires.
    games = dict(panel._shelf_lists)["Games"]
    item = games.takeItem(1)  # Discord
    games.insertItem(0, item)
    panel._on_shelf_rows_moved()
    entries = shell_home.pin_entries(panel._store["paths"],
                                     panel._store["groups"])
    games_paths = [e["path"] for e in entries
                   if e["group"] == "Games"]
    assert games_paths == ["/x/discord.lnk", "/x/steam.lnk"]


def test_rerender_leaves_no_orphan_shelves():
    # Regression (V17-B capture catch): _paint_shelves used
    # deleteLater alone to clear the old shelves; the deferred
    # delete never landed and the stale lists stayed as orphan
    # children at first-render geometry, painting through the
    # new render after any second _refilter (e.g. an accent
    # change). Clearing now unparents immediately.
    from PyQt6.QtWidgets import QListWidget
    panel = _panel({"/x/steam.lnk": "Games",
                    "/x/discord.lnk": "Games"})
    panel._refilter()
    panel._refilter()
    lists = [w for w in panel.shelves_host.children()
             if isinstance(w, QListWidget)]
    assert len(lists) == len(panel._shelf_lists) == 2


def test_cross_shelf_drop_regroups_and_persists():
    panel = _panel({"/x/steam.lnk": "Games",
                    "/x/discord.lnk": "Games"})
    # The drop handler a shelf's mime drop calls: Calculator
    # dropped at the FRONT of the Games shelf.
    panel._cross_shelf_drop("/x/calc.lnk", "Games", 0)
    entries = shell_home.pin_entries(panel._store["paths"],
                                     panel._store["groups"])
    calc = next(e for e in entries
                if e["path"] == "/x/calc.lnk")
    assert calc["group"] == "Games"
    games = [e["path"] for e in entries
             if e["group"] == "Games"]
    assert games == ["/x/calc.lnk", "/x/steam.lnk",
                     "/x/discord.lnk"]
    # And the repaint shows it on the Games shelf.
    assert dict(_shelf_items(panel))["Games"] == \
        ["Calculator", "Steam", "Discord"]
