"""ui_prefs store location + durability (V19, 2026-10-31).

Alek's live report on V18: "It still says no pinned apps when
I pin an app" — and his ui.json at C:\\Users\\Allen\\ARIA
"is also empty", and "why are they saving to C drive? ARIA
files live in E:\\ARIA". The store resolved through
os.path.expanduser("~")/ARIA — a home-derived directory
nothing else in the install uses — while aria/config.py's
ROOT_DIR (aria_keys.json, workspace/, soul.md) is the
install root. Writers swallowed every exception behind a
success string and wrote truncate-then-dump, so a write
could die silently or leave a corrupt husk that every read
fail-softed to defaults forever.

This suite pins the new contract, hermetically (the install
root and HOME are both redirected to tmp dirs):
- the canonical file is <install root>/ui.json, and HOME
  changes cannot lose prefs;
- a real legacy home file migrates (merge, canonical wins,
  legacy untouched, husk migrates nothing);
- writes are atomic — a dump dying mid-write leaves the
  previous good file standing, with no temp litter;
- a failed write is LOGGED (visible), never silent, while
  the read contract stays fail-soft;
- interleaved writers cannot clobber each other's keys.
"""
import json
import os
import sys
import threading
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)

import importlib
import pytest

ui_prefs = importlib.import_module("aria.ui_prefs")

PINS = ["/x/Google Chrome.lnk", "/x/Steam.lnk"]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """install/ = the fake install root (canonical store),
    home/ = the fake HOME (legacy store lives under it)."""
    install = tmp_path / "install"
    home = tmp_path / "home"
    install.mkdir()
    home.mkdir()
    monkeypatch.setattr(ui_prefs, "_INSTALL_ROOT",
                        str(install))
    monkeypatch.setenv("HOME", str(home))
    return install, home


def _canonical(install):
    return install / "ui.json"


def _legacy(home):
    legacy = home / "ARIA" / "ui.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    return legacy


# -- canonical location ---------------------------------------

def test_canonical_location_is_install_root(store):
    install, home = store
    assert ui_prefs._ui_file() == str(install / "ui.json")
    ui_prefs.set_pinned_apps(PINS)
    assert _canonical(install).exists()
    assert not (home / "ARIA" / "ui.json").exists()
    assert ui_prefs.get_pinned_apps() == PINS


def test_resolver_matches_config_root_dir_formula():
    """No redirection: _ui_file() is the install root computed
    exactly as aria/config.py computes ROOT_DIR — the parent
    of the aria package, next to aria_keys.json."""
    expected = os.path.join(
        os.path.dirname(os.path.dirname(
            os.path.abspath(ui_prefs.__file__))), "ui.json")
    assert ui_prefs._ui_file() == expected


def test_home_change_cannot_lose_pins(store, tmp_path,
                                       monkeypatch):
    """The hostile case from the live failure: prefs written
    under one home must survive the process's home resolving
    differently later (auto-start / scheduled-task context)."""
    install, home = store
    ui_prefs.set_pinned_apps(PINS)
    other_home = tmp_path / "other-home"
    other_home.mkdir()
    monkeypatch.setenv("HOME", str(other_home))
    assert ui_prefs.get_pinned_apps() == PINS


# -- legacy migration ------------------------------------------

def test_legacy_prefs_migrate_on_first_read(store):
    install, home = store
    legacy = _legacy(home)
    legacy.write_text(json.dumps({
        "accent": "#54d9e8",
        "pinned_apps": PINS,
        "pin_groups": {PINS[1]: "Games"},
        "unrelated_key": {"keep": True},
    }), encoding="utf-8")
    before = legacy.read_bytes()
    assert ui_prefs.get_pinned_apps() == PINS
    assert ui_prefs.get_pin_groups() == {PINS[1]: "Games"}
    assert ui_prefs.get_accent() == "#54d9e8"
    data = json.loads(_canonical(install).read_text(
        encoding="utf-8"))
    assert data["pinned_apps"] == PINS
    assert data["unrelated_key"] == {"keep": True}
    # The legacy file is left in place, byte-identical.
    assert legacy.read_bytes() == before


def test_migration_canonical_values_win(store):
    install, home = store
    _legacy(home).write_text(json.dumps({
        "accent": "#111111", "pinned_apps": PINS}),
        encoding="utf-8")
    _canonical(install).write_text(json.dumps({
        "accent": "#222222"}), encoding="utf-8")
    assert ui_prefs.get_accent() == "#222222"   # canonical wins
    assert ui_prefs.get_pinned_apps() == PINS   # legacy fills in
    data = json.loads(_canonical(install).read_text(
        encoding="utf-8"))
    assert data["accent"] == "#222222"
    assert data["pinned_apps"] == PINS


def test_legacy_husk_migrates_nothing(store):
    """His reported C: file: an empty husk from an old
    interrupted write. Nothing to recover — and nothing may
    crash; new writes simply land at the install root."""
    install, home = store
    _legacy(home).write_bytes(b"")
    assert ui_prefs.get_pinned_apps() == []
    ui_prefs.set_pinned_apps(PINS)
    assert ui_prefs.get_pinned_apps() == PINS
    assert json.loads(_canonical(install).read_text(
        encoding="utf-8"))["pinned_apps"] == PINS


def test_legacy_corrupt_migrates_nothing(store):
    install, home = store
    _legacy(home).write_text('{"pinned_apps": ["partial',
                             encoding="utf-8")
    assert ui_prefs.get_pinned_apps() == []
    ui_prefs.set_pinned_apps(PINS)
    assert ui_prefs.get_pinned_apps() == PINS


def test_migration_runs_once_per_process(store):
    install, home = store
    legacy = _legacy(home)
    legacy.write_text(json.dumps({"pinned_apps": PINS}),
                      encoding="utf-8")
    assert ui_prefs.get_pinned_apps() == PINS
    # A later legacy edit is NOT re-merged this process.
    legacy.write_text(json.dumps(
        {"pinned_apps": ["/x/other.lnk"]}), encoding="utf-8")
    assert ui_prefs.get_pinned_apps() == PINS


def test_redirected_ui_file_skips_migration(store, tmp_path,
                                            monkeypatch):
    """Tests/embedders that redirect _ui_file itself opt out
    of migration (test_ui_panels / test_customize rely on a
    clean redirected store even when a real legacy file
    exists on the machine)."""
    install, home = store
    _legacy(home).write_text(json.dumps(
        {"pinned_apps": PINS}), encoding="utf-8")
    target = tmp_path / "elsewhere" / "ui.json"
    target.parent.mkdir()
    monkeypatch.setattr(ui_prefs, "_ui_file",
                        lambda: str(target))
    assert ui_prefs.get_pinned_apps() == []
    ui_prefs.set_pinned_apps(["/x/a.lnk"])
    assert json.loads(target.read_text(encoding="utf-8"))[
        "pinned_apps"] == ["/x/a.lnk"]


# -- atomic writes ----------------------------------------------

def test_interrupted_write_leaves_previous_payload(store,
                                                   monkeypatch):
    install, home = store
    ui_prefs.set_pinned_apps(PINS)
    canonical = _canonical(install)
    good = canonical.read_bytes()
    real_dump = json.dump

    def dying_dump(obj, f, **kw):
        f.write('{"pinned_apps": ["partial')
        raise OSError("disk died mid-write")

    monkeypatch.setattr(json, "dump", dying_dump)
    ui_prefs.set_pinned_apps(["/x/new.lnk"])
    monkeypatch.setattr(json, "dump", real_dump)
    assert canonical.read_bytes() == good
    assert ui_prefs.get_pinned_apps() == PINS
    # No temp litter left behind.
    assert [p.name for p in install.iterdir()] == ["ui.json"]


def test_broken_canonical_file_reads_soft_and_recovers(store):
    install, home = store
    _canonical(install).write_bytes(b"")
    assert ui_prefs.get_pinned_apps() == []
    assert ui_prefs.get_accent() is None
    ui_prefs.set_pinned_apps(PINS)
    assert ui_prefs.get_pinned_apps() == PINS


# -- visible failures --------------------------------------------

def test_failed_write_is_logged_not_silent(store, monkeypatch):
    install, home = store
    _canonical(install).mkdir()  # a directory: replace must fail
    seen = []
    monkeypatch.setattr(
        ui_prefs, "_log_failure",
        lambda what, exc: seen.append((what, exc)))
    result = ui_prefs.set_pinned_apps(PINS)
    assert result == f"Pinned apps: {len(PINS)}."  # fail-soft
    assert seen and seen[0][0] == "write"
    assert ui_prefs.get_pinned_apps() == []


def test_log_failure_never_raises(store):
    ui_prefs._log_failure("write", OSError("boom"))


# -- writer races --------------------------------------------------

def test_interleaved_writers_keep_each_others_keys(store):
    install, home = store
    errors = []

    def hammer(fn, values):
        try:
            for v in values:
                fn(v)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=hammer, args=(
            ui_prefs.set_pinned_apps,
            [PINS, ["/x/a.lnk"], PINS])),
        threading.Thread(target=hammer, args=(
            ui_prefs.set_pin_groups,
            [{PINS[0]: "Media"}, {}, {PINS[0]: "Media"}])),
        threading.Thread(target=hammer, args=(
            ui_prefs.set_accent,
            ["#111111", "#222222", "#333333"])),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    data = json.loads(_canonical(install).read_text(
        encoding="utf-8"))
    assert data["pinned_apps"] in (PINS, ["/x/a.lnk"])
    assert data["accent"] in ("#111111", "#222222", "#333333")
    assert "pin_groups" in data
    # And the public reads agree with the file.
    assert ui_prefs.get_pinned_apps() == data["pinned_apps"]
