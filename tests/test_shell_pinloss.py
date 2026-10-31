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
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path / "ARIA" / "ui.json"


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
