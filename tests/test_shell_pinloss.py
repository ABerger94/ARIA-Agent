"""Shell Home V18 (2026-10-31): the pin-loss regression suite.

Alek's live report on V17 (7935d0a6): "Pinned apps disappear
now when I go to group then." / "No apps stay pinned now."

Diagnosis (reproduced in this seat before the fix): storage
was never broken — ui_prefs, the pure pin model, and every
panel write path round-trip correctly through the real file
store (test_group_and_restart_survive_real_store passed even
before the fix). The pinned VIEW was masked: the search box's
text survives view changes, pin/group actions, and Shell
Home exit/re-open (the panel is one long-lived widget), and
_refilter lets a non-empty query override EVERY base view.
The V17 witness flow itself put the text there ("search
Spotify in All apps") — from then on the home showed the
stale search result instead of his pins, grouping never
reached the shelf page (grouped rendering exists only in the
pinned view), and every new pin landed in storage invisibly.
From his seat: pins disappear at grouping time, and no app
stays pinned.

The fix: navigation seams clear the transient search — the
panel being shown (Shell Home (re)opens on show), the
Pinned/All Apps toggle, and the arrangement actions (Move
left/right, Move to group), whose result lives on the home
they were masking. These tests drive the REAL default
ui_prefs providers over a temp HOME (the hermetic pattern
from test_shell_groups.py) — no lambda pin stores.
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
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import importlib
shell_panel = importlib.import_module("aria.shell_panel")
ui_prefs = importlib.import_module("aria.ui_prefs")

NOW = datetime(2026, 10, 31, 8, 55)
SM = (r"C:\Users\Allen\AppData\Roaming\Microsoft\Windows"
      r"\Start Menu\Programs")
APPS = [{"name": n, "path": SM + "\\" + n + ".lnk"} for n in
        ("Google Chrome", "Microsoft Edge", "File Explorer",
         "Command Prompt")]
SPOTIFY = {"name": "Spotify",
           "appid": "SpotifyAB.SpotifyMusic_1.0!Spotify"}
APPS = APPS + [SPOTIFY]
SPOTIFY_KEY = "appid:SpotifyAB.SpotifyMusic_1.0!Spotify"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The real ui_prefs store, hermetically redirected the
    V19 way: the canonical file is <install root>/ui.json
    (ui_prefs._INSTALL_ROOT), HOME is a separate empty dir
    (the legacy location holds nothing unless a test seeds
    it)."""
    home = tmp_path / "home"
    home.mkdir()
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(ui_prefs, "_INSTALL_ROOT",
                        str(install))
    return install / "ui.json"


def _panel():
    panel = shell_panel.ShellPanel(
        apps_provider=lambda: [dict(a) for a in APPS],
        now_provider=lambda: NOW)
    panel.resize(1600, 900)
    panel.show()
    _APP.processEvents()
    return panel


def _shelf_names(panel):
    return {g: [lst.item(i).text() for i in range(lst.count())]
            for g, lst in panel._shelf_lists}


def _read(store):
    return json.loads(store.read_text(encoding="utf-8"))


def _pin_all(panel):
    panel.set_view_all(True)
    for app in APPS:
        panel.toggle_pin(app)
    panel.set_view_all(False)
    _APP.processEvents()


# -- the storage round-trip (passed pre-fix; guards the fear) -

def test_group_and_restart_survive_real_store(store):
    panel = _panel()
    _pin_all(panel)
    panel.move_pin_to_group(APPS[0], "Media")
    _APP.processEvents()
    data = _read(store)
    assert len(data["pinned_apps"]) == 5
    assert data["pin_groups"] == {APPS[0]["path"]: "Media"}
    panel.close()

    panel2 = _panel()
    assert panel2.grid_stack.currentWidget() is panel2.shelf_scroll
    shelves = _shelf_names(panel2)
    assert shelves["Media"] == ["Google Chrome"]
    assert sorted(shelves[""]) == sorted(
        a["name"] for a in APPS[1:])
    assert panel2.count_label.text() == "5 pinned"
    panel2.close()


# -- the V18 defect: stale search masking the pinned home -----

def test_stale_search_unmasked_on_reopen(store):
    """The reported flow: pins exist, he searches (the V17
    witness step), pins + groups Spotify — then exits and
    re-opens Shell Home. The home must show his pins again,
    grouped — not the stale one-result search."""
    panel = _panel()
    _pin_all(panel)
    panel.search.setText("spotify")
    _APP.processEvents()
    # The trap, documented: only the search result is visible.
    assert [panel.app_list.item(i).text()
            for i in range(panel.app_list.count())] == ["Spotify"]
    panel.move_pin_to_group(SPOTIFY, "Media")
    _APP.processEvents()
    panel.hide()                      # exit Shell Home
    panel.show()                      # re-open it
    _APP.processEvents()
    assert panel.search.text() == ""
    assert panel.grid_stack.currentWidget() is panel.shelf_scroll
    shelves = _shelf_names(panel)
    assert shelves["Media"] == ["Spotify"]
    assert sorted(shelves[""]) == sorted(
        a["name"] for a in APPS[:4])
    assert panel.count_label.text() == "5 pinned"
    panel.close()


def test_view_toggle_clears_stale_search(store):
    """Asking for a base view (Pinned / All Apps) must show
    that view — a leftover query may not keep overriding it."""
    panel = _panel()
    _pin_all(panel)
    panel.search.setText("chrome")
    _APP.processEvents()
    panel.set_view_all(True)
    _APP.processEvents()
    assert panel.search.text() == ""
    assert panel.app_list.count() == len(APPS)
    panel.set_view_all(False)
    _APP.processEvents()
    assert panel.app_list.count() == len(APPS)  # all five pinned
    assert panel.grid_stack.currentWidget() is panel.app_list
    panel.close()


def test_group_action_reveals_the_home(store):
    """Grouping from a search result: the arrangement lives on
    the pinned home, so the action lands him there — shelves
    visible, the grouped tile on its shelf — instead of
    leaving the stale search on screen with no confirmation."""
    panel = _panel()
    _pin_all(panel)
    panel.search.setText("spotify")
    _APP.processEvents()
    panel.move_pin_to_group(SPOTIFY, "Media")
    _APP.processEvents()
    assert panel.search.text() == ""
    assert panel.grid_stack.currentWidget() is panel.shelf_scroll
    shelves = _shelf_names(panel)
    assert shelves["Media"] == ["Spotify"]
    assert sorted(shelves[""]) == sorted(
        a["name"] for a in APPS[:4])
    data = _read(store)
    assert data["pin_groups"] == {SPOTIFY_KEY: "Media"}
    panel.close()


# -- V19: the hostile-realistic flow V18's repros bypassed -----
# V18's fixtures handed the panel a fake app list and a fresh
# store. Alek's machine has: a REAL Start Menu tree, the REAL
# enumerate_all_apps merge (AppsFolder twins of .lnk apps
# included), a ui.json written by V14-era code at the LEGACY
# home location, and a process whose home can resolve
# differently between runs. His report on V18: pins still
# "don't stay pinned" — and the legacy file was empty, i.e.
# the write/read path, not the rendering. This test drives
# the whole flow through the real seams.

def test_real_enumeration_legacy_store_flow(tmp_path,
                                            monkeypatch):
    app_launcher = importlib.import_module(
        "aria.app_launcher")
    # (a) a fake Start Menu tree on disk, real structure.
    user_programs = tmp_path / "sm-user" / "Programs"
    machine_programs = tmp_path / "sm-machine" / "Programs"
    user_programs.mkdir(parents=True)
    machine_programs.mkdir(parents=True)
    names = ("Google Chrome", "Microsoft Edge",
             "File Explorer", "Command Prompt")
    for n in names:
        (user_programs / f"{n}.lnk").write_bytes(b"")
    # A machine-root twin of Chrome: the user root wins.
    (machine_programs / "Google Chrome.lnk").write_bytes(b"")

    # (b) fake Get-StartApps: twins of two .lnk apps + Spotify.
    folder_json = json.dumps([
        {"Name": "Google Chrome", "AppID": "Chrome"},
        {"Name": "Microsoft Edge", "AppID": "MSEdge"},
        {"Name": "Spotify",
         "AppID": "SpotifyAB.SpotifyMusic_1.0!Spotify"}])

    def enumerate_real():
        return app_launcher.enumerate_all_apps(
            [str(user_programs), str(machine_programs)],
            appsfolder_runner=lambda: folder_json)

    apps = enumerate_real()
    by_name = {a["name"]: a for a in apps}
    assert len(apps) == 5
    # The merge kept the .lnk twins (real paths), Spotify is
    # the AppsFolder entry.
    assert by_name["Google Chrome"]["path"].endswith(
        "Google Chrome.lnk")
    assert "appid" in by_name["Spotify"]

    # (c) the store: canonical at a fake install root, and a
    # V14-era ui.json at the LEGACY home location — accent,
    # four pins (the enumerated paths), one group, an
    # unrelated key.
    home = tmp_path / "home"
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(ui_prefs, "_INSTALL_ROOT",
                        str(install))
    pin_paths = [app_launcher.app_key(by_name[n])
                 for n in names]
    legacy = home / "ARIA" / "ui.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "accent": "#54d9e8",
        "pinned_apps": pin_paths,
        "pin_groups": {pin_paths[0]: "Media"},
        "unrelated_key": {"keep": True},
    }), encoding="utf-8")

    panel = shell_panel.ShellPanel(
        apps_provider=enumerate_real,
        now_provider=lambda: NOW)
    panel.resize(1600, 900)
    panel.show()
    _APP.processEvents()
    # The legacy prefs migrated and the grouped home renders.
    assert panel.grid_stack.currentWidget() is panel.shelf_scroll
    shelves = _shelf_names(panel)
    assert shelves["Media"] == ["Google Chrome"]
    assert sorted(shelves[""]) == sorted(names[1:])
    assert panel.count_label.text() == "4 pinned"
    data = _read(install / "ui.json")
    assert data["pinned_apps"] == pin_paths
    assert data["unrelated_key"] == {"keep": True}

    # Pin + group Spotify through the panel's real actions.
    spotify = next(a for a in panel._apps
                   if a["name"] == "Spotify")
    panel.toggle_pin(spotify)
    panel.move_pin_to_group(spotify, "Media")
    _APP.processEvents()
    panel.close()

    # Restart: a fresh panel over the same store.
    panel2 = shell_panel.ShellPanel(
        apps_provider=enumerate_real,
        now_provider=lambda: NOW)
    panel2.resize(1600, 900)
    panel2.show()
    _APP.processEvents()
    shelves = _shelf_names(panel2)
    assert shelves["Media"] == ["Google Chrome", "Spotify"]
    assert panel2.count_label.text() == "5 pinned"
    panel2.close()

    # The home switch: a later process whose home resolves
    # somewhere else entirely must STILL see every pin —
    # the exact loss Alek reported. (Pre-V19 the store WAS
    # the home path; this panel came up empty.)
    other_home = tmp_path / "other-home"
    other_home.mkdir()
    monkeypatch.setenv("HOME", str(other_home))
    panel3 = shell_panel.ShellPanel(
        apps_provider=enumerate_real,
        now_provider=lambda: NOW)
    panel3.resize(1600, 900)
    panel3.show()
    _APP.processEvents()
    assert panel3.count_label.text() == "5 pinned"
    shelves = _shelf_names(panel3)
    assert shelves["Media"] == ["Google Chrome", "Spotify"]
    panel3.close()
