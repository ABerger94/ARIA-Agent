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
