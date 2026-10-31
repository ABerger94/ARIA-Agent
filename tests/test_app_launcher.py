"""ARIA OS app launcher (2026-10-30): Start Menu enumeration,
ranked search, and the injectable launch seam.

Pure-module suite: dummy .lnk files in tmp roots stand in for a
Start Menu, and the opener is a recorder, so nothing real is
walked or launched."""
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)

import importlib
launcher = importlib.import_module("aria.app_launcher")


def _mk_lnk(root, *parts):
    path = os.path.join(root, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("dummy shortcut")
    return path


def _roots(tmp_path):
    user = str(tmp_path / "user")
    machine = str(tmp_path / "machine")
    _mk_lnk(user, "Google Chrome.lnk")
    _mk_lnk(user, "Apps", "Visual Studio Code.lnk")
    _mk_lnk(user, "Apps", "Calculator.lnk")
    _mk_lnk(user, "notes.txt")  # not a shortcut — ignored
    _mk_lnk(machine, "Google Chrome.lnk")   # duplicate name
    _mk_lnk(machine, "Steam.lnk")
    return [user, machine]


def test_enumerate_collects_dedupes_and_sorts(tmp_path):
    apps = launcher.enumerate_apps(_roots(tmp_path))
    names = [a["name"] for a in apps]
    assert names == ["Calculator", "Google Chrome", "Steam",
                     "Visual Studio Code"]
    # The per-user root wins the duplicate.
    chrome = [a for a in apps if a["name"] == "Google Chrome"][0]
    assert str(tmp_path / "user") in chrome["path"]


def test_enumerate_skips_missing_roots():
    assert launcher.enumerate_apps(["/no/such/dir", None, ""]) == []
    assert launcher.enumerate_apps([]) == []


def test_search_ranking():
    apps = [{"name": n, "path": n}
            for n in ("Google Chrome", "Chrome Beta",
                      "Chromium", "Steam")]
    assert [a["name"] for a in launcher.search_apps(apps, "chrome")] \
        == ["Chrome Beta", "Google Chrome"]
    assert [a["name"] for a in
            launcher.search_apps(apps, "google chrome")] \
        == ["Google Chrome"]
    assert launcher.search_apps(apps, "zzzz") == []


def test_search_empty_query_returns_sorted_head():
    apps = [{"name": "B", "path": "b"}, {"name": "A", "path": "a"}]
    out = launcher.search_apps(apps, "", limit=1)
    assert out == [{"name": "B", "path": "b"}]  # given order kept


def test_default_roots_from_env():
    env = {"APPDATA": r"C:\Users\X\AppData\Roaming",
           "PROGRAMDATA": r"C:\ProgramData"}
    roots = launcher.default_roots(env)
    assert len(roots) == 2
    assert roots[0].endswith(os.path.join(
        "Start Menu", "Programs"))
    assert launcher.default_roots({}) == []


def test_strip_launch_verb():
    assert launcher.strip_launch_verb("open Google Chrome") == \
        ("google chrome", True)
    assert launcher.strip_launch_verb("Google Chrome") == \
        ("google chrome", False)
    assert launcher.strip_launch_verb("open") == ("open", False)


def test_launch_uses_injected_opener():
    opened = []
    entry = {"name": "Steam", "path": "/x/Steam.lnk"}
    assert launcher.launch_app(entry, opener=opened.append) is True
    assert opened == ["/x/Steam.lnk"]


def test_launch_failures_are_soft():
    def boom(_p):
        raise RuntimeError("nope")
    assert launcher.launch_app({"name": "X", "path": "/x"},
                               opener=boom) is False
    assert launcher.launch_app({"name": "X"}, opener=boom) is False
    assert launcher.launch_app(None, opener=boom) is False


# -- V17: AppsFolder source (Get-StartApps) ------------------------

SPOTIFY_ID = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
GET_START_APPS_JSON = (
    '[{"Name": "Spotify", "AppID": "' + SPOTIFY_ID + '"},'
    ' {"Name": "Spotify", "AppID": "dupe-id"},'
    ' {"Name": "  ", "AppID": "blank-name"},'
    ' {"Name": "No Id", "AppID": ""},'
    ' {"Name": "Calculator", "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"}]'
)


def test_appsfolder_parse_dedupe_and_sort():
    apps = launcher.enumerate_appsfolder(
        runner=lambda: GET_START_APPS_JSON)
    assert [a["name"] for a in apps] == ["Calculator", "Spotify"]
    spotify = apps[1]
    assert spotify == {"name": "Spotify", "appid": SPOTIFY_ID}


def test_appsfolder_single_app_json_object():
    # Get-StartApps with ONE app emits a bare object, not a list.
    apps = launcher.enumerate_appsfolder(
        runner=lambda: '{"Name": "Spotify", "AppID": "x!y"}')
    assert apps == [{"name": "Spotify", "appid": "x!y"}]


def test_appsfolder_fail_soft_on_broken_source():
    def boom():
        raise RuntimeError("no powershell here")

    assert launcher.enumerate_appsfolder(runner=boom) == []
    assert launcher.enumerate_appsfolder(runner=lambda: "junk") == []
    assert launcher.enumerate_appsfolder(runner=lambda: "") == []
    assert launcher.enumerate_appsfolder(
        runner=lambda: '{"Name": 1}') == []


def test_merge_prefers_lnk_and_keeps_folder_only():
    lnk = [{"name": "Spotify", "path": "/x/Spotify.lnk"},
           {"name": "Steam", "path": "/x/Steam.lnk"}]
    folder = [{"name": "Spotify", "appid": SPOTIFY_ID},
              {"name": "Calculator", "appid": "calc!app"}]
    merged = launcher.merge_app_sources(lnk, folder)
    assert [a["name"] for a in merged] == \
        ["Calculator", "Spotify", "Steam"]
    # The .lnk twin wins: it carries the real icon.
    assert merged[1] == {"name": "Spotify",
                         "path": "/x/Spotify.lnk"}


def test_enumerate_all_apps_spotify_appears(tmp_path):
    roots = _roots(tmp_path)  # Chrome, VS Code, Calculator, Steam
    apps = launcher.enumerate_all_apps(
        roots, appsfolder_runner=lambda: GET_START_APPS_JSON)
    names = [a["name"] for a in apps]
    # Spotify exists ONLY in the AppsFolder source.
    assert "Spotify" in names
    spotify = next(a for a in apps if a["name"] == "Spotify")
    assert spotify["appid"] == SPOTIFY_ID
    # And enumeration still works whole when the source dies.
    def boom():
        raise RuntimeError("nope")
    apps = launcher.enumerate_all_apps(
        roots, appsfolder_runner=boom)
    assert [a["name"] for a in apps] == \
        ["Calculator", "Google Chrome", "Steam",
         "Visual Studio Code"]


def test_app_key_and_launch_routing_per_kind():
    lnk = {"name": "Steam", "path": "/x/Steam.lnk"}
    folder = {"name": "Spotify", "appid": SPOTIFY_ID}
    assert launcher.app_kind(lnk) == "lnk"
    assert launcher.app_kind(folder) == "appsfolder"
    assert launcher.app_key(lnk) == "/x/Steam.lnk"
    assert launcher.app_key(folder) == "appid:" + SPOTIFY_ID
    assert launcher.app_key({}) == ""
    assert launcher.launch_target(lnk) == "/x/Steam.lnk"
    assert launcher.launch_target(folder) == \
        "shell:AppsFolder\\" + SPOTIFY_ID
    opened = []
    assert launcher.launch_app(folder, opener=opened.append) \
        is True
    assert opened == ["shell:AppsFolder\\" + SPOTIFY_ID]
