"""Shell Home V17 part A (2026-10-31, Alek's ask — "some apps
like Spotify aren't showing"): AppsFolder apps in the panel.
An AppsFolder entry ({name, appid}, no .lnk path) renders in
the grid, pins under the key appid:<AppID> in the same ordered
pin store and group map, resolves in pinned_view, and launches
through the launcher's AppsFolder URI routing. Fake stores
only — no real ui.json, no real enumeration.
"""
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
app_launcher = importlib.import_module("aria.app_launcher")
shell_home = importlib.import_module("aria.shell_home")
shell_panel = importlib.import_module("aria.shell_panel")

NOW = datetime(2026, 10, 31, 7, 30)
SPOTIFY_ID = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
SPOTIFY = {"name": "Spotify", "appid": SPOTIFY_ID}
SPOTIFY_KEY = "appid:" + SPOTIFY_ID
CHROME = {"name": "Google Chrome", "path": "/x/chrome.lnk"}
APPS = [CHROME, SPOTIFY]
KEYS = [CHROME["path"], SPOTIFY_KEY]


def _panel(paths=(), groups=None, **overrides):
    store = {"paths": list(paths), "groups": dict(groups or {})}
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


def _tile_names(panel):
    return [panel.app_list.item(i).data(
        Qt.ItemDataRole.UserRole)["name"]
        for i in range(panel.app_list.count())
        if isinstance(panel.app_list.item(i).data(
            Qt.ItemDataRole.UserRole), dict)
        and "group_header" not in panel.app_list.item(i).data(
            Qt.ItemDataRole.UserRole)]


def test_pinned_view_resolves_appid_pin():
    out = shell_home.pinned_view(APPS, [SPOTIFY_KEY])
    assert out == [SPOTIFY]
    out = shell_home.pinned_view(
        APPS, [SPOTIFY_KEY, CHROME["path"]])
    assert out == [SPOTIFY, CHROME]  # pin order, not app order


def test_pin_round_trip_for_appid_entry():
    panel = _panel()
    assert panel.pin_action_label(SPOTIFY) == "Pin to Home"
    panel.toggle_pin(SPOTIFY)
    assert panel._store["paths"] == [SPOTIFY_KEY]
    assert panel.pin_action_label(SPOTIFY) == "Remove from Home"
    # The pinned home renders the tile.
    assert _tile_names(panel) == ["Spotify"]
    # And unpinning removes the key again.
    panel.toggle_pin(SPOTIFY)
    assert panel._store["paths"] == []


def test_appid_pin_carries_a_group():
    panel = _panel(paths=KEYS)
    panel.move_pin_to_group(SPOTIFY, "Music")
    assert panel._store["groups"] == {SPOTIFY_KEY: "Music"}
    names = _tile_names(panel)
    assert "Spotify" in names and "Google Chrome" in names


def test_launch_of_appid_entry_uses_appsfolder_uri():
    opened = []
    panel = _panel(on_launch=None)
    panel._on_launch = None
    # Route through the REAL launch_app with a recorder opener
    # by launching the entry the panel would hand it.
    assert app_launcher.launch_app(
        SPOTIFY, opener=opened.append) is True
    assert opened == ["shell:AppsFolder\\" + SPOTIFY_ID]
    # The panel's own launch path hands the entry over whole.
    got = []
    panel2 = _panel(on_launch=got.append)
    panel2._launch(SPOTIFY)
    assert got == [SPOTIFY]
