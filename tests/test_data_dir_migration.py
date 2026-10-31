"""V20 (2026-10-31): ONE canonical app-data location — the
install root — for ARIA's whole state-file family, with
legacy migration from ~/ARIA.

Alek's directive, looking at C:\\Users\\Allen\\ARIA while ARIA
runs from E:\\ARIA: "Did all of these files change to E:\\ARIA
as well? They need to all live in the E:\\ARIA folder with the
rest of her code." V19 had moved ui_prefs only; approval,
audio, bookmark, persona, screenwatch, reminder_gate, the
life-room files, routines/, logs/ and the first-run flag all
still resolved home-derived.

Pinned here:
- every resolver lands at the install root (aria.data_dir),
- first resolve migrates a real legacy file copy-if-missing:
  canonical wins when both exist, the legacy file is left
  byte-untouched, empty husks and *.tmp strays carry nothing,
  the append-only diary survives byte-for-byte, and his
  approval mode ('auto') survives the move,
- migration is idempotent,
- writes through the moved stores are atomic (no *.tmp
  litter, no truncate-then-dump).

Hermetic: data_dir's root is redirected to a temp "install"
and HOME to a temp legacy home; _true_root is pointed at the
same temp root so the auto-migration gate (skip-when-
redirected) stays open for the migration tests.
"""
import importlib
import json
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)
tools_pkg = types.ModuleType("aria.tools")
tools_pkg.__path__ = [os.path.join(PKG, "tools")]
sys.modules.setdefault("aria.tools", tools_pkg)
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
sys.modules.setdefault("aria.speech", types.ModuleType("aria.speech"))

import pytest

data_dir = importlib.import_module("aria.data_dir")
approval = importlib.import_module("aria.approval")
audio_prefs = importlib.import_module("aria.audio_prefs")
bookmark = importlib.import_module("aria.bookmark")
first_run = importlib.import_module("aria.first_run")
life_diary = importlib.import_module("aria.life_diary")
life_process = importlib.import_module("aria.life_process")
persona = importlib.import_module("aria.persona")
routines = importlib.import_module("aria.routines")
scheduler = importlib.import_module("aria.scheduler")
screenwatch = importlib.import_module("aria.screenwatch")
fileops = importlib.import_module("aria.tools.fileops")


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    """A temp install root + a temp legacy home, wired into
    data_dir so resolvers AND auto-migration both engage."""
    root = tmp_path / "install"
    root.mkdir()
    home = tmp_path / "home"
    legacy = home / "ARIA"
    legacy.mkdir(parents=True)
    monkeypatch.setattr(data_dir, "_ROOT", str(root))
    monkeypatch.setattr(data_dir, "_true_root", lambda: str(root))
    monkeypatch.setenv("HOME", str(home))
    data_dir._ATTEMPTED.clear()
    return types.SimpleNamespace(root=root, legacy=legacy,
                                 home=home)


def _seed(legacy_dir, name, content: bytes):
    p = legacy_dir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


# -- resolvers -------------------------------------------------


def test_resolvers_land_at_install_root(roots):
    r = str(roots.root)
    assert approval._approval_file() == os.path.join(
        r, "approval.json")
    assert audio_prefs._audio_file() == os.path.join(
        r, "audio.json")
    assert bookmark.default_path() == os.path.join(
        r, "bookmark.json")
    assert life_process.aria_dir() == r
    p = life_process.paths()
    assert p["dir"] == r
    assert p["state"] == os.path.join(r, "life_state.json")
    assert p["control"] == os.path.join(r, "life_control.json")
    assert p["diary"] == os.path.join(r, "life_diary.jsonl")
    assert p["journal_req"] == os.path.join(
        r, "life_journal_request.json")
    assert p["journal_resp"] == os.path.join(
        r, "life_journal_response.json")
    assert p["journal_budget"] == os.path.join(
        r, "life_journal_budget.json")
    assert life_diary.diary_path() == os.path.join(
        r, "life_diary.jsonl")
    assert routines._routines_dir() == os.path.join(
        r, "routines")
    assert fileops._routines_dir() == os.path.realpath(
        os.path.join(r, "routines"))
    assert scheduler._gate_store().path == os.path.join(
        r, "reminder_gate.json")


def test_import_time_constants_are_install_root():
    """screenwatch / first_run / persona bind their paths at
    import (their established test seams rebind the constants);
    the defaults must be the TRUE install root."""
    true_root = data_dir._true_root()
    assert screenwatch.WATCHES_PATH == os.path.join(
        true_root, "screen_watches.json")
    assert first_run._DONE_FLAG == os.path.join(
        true_root, ".first_run_done")
    assert persona._STORAGE_FILE == os.path.join(
        true_root, "persona.json")


def test_no_resolver_points_home(roots, monkeypatch):
    """With HOME moved somewhere empty, resolvers must not
    follow it (the pre-V20 failure: a different home context
    meant a different store)."""
    other = roots.home.parent / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("HOME", str(other))
    assert approval._approval_file().startswith(str(roots.root))
    assert life_process.aria_dir() == str(roots.root)
    assert routines._routines_dir().startswith(str(roots.root))


# -- migration ---------------------------------------------------


def test_approval_mode_survives_migration(roots):
    """His live case: approval.json holds mode 'auto' on C: —
    after the move the mode must still read 'auto', from E:."""
    legacy = _seed(roots.legacy, "approval.json",
                   b'{"mode": "auto"}')
    before = legacy.read_bytes()
    assert approval.get_mode() == "auto"
    canonical = roots.root / "approval.json"
    assert canonical.exists()
    assert json.loads(canonical.read_text(
        encoding="utf-8"))["mode"] == "auto"
    assert legacy.read_bytes() == before  # archive untouched


def test_migration_canonical_wins_when_both_exist(roots):
    _seed(roots.legacy, "approval.json", b'{"mode": "auto"}')
    canonical = roots.root / "approval.json"
    canonical.write_text('{"mode": "confirm-all"}',
                         encoding="utf-8")
    assert approval.get_mode() == "confirm-all"


def test_migration_husk_and_tmp_carry_nothing(roots):
    _seed(roots.legacy, "approval.json", b"")  # 0-byte husk
    assert approval.get_mode() == approval.DEFAULT_MODE
    assert not (roots.root / "approval.json").exists()
    # a *.tmp stray beside a real name is never carried, and
    # never substitutes for the real file
    _seed(roots.legacy, "life_journal_budget.json.tmp",
          b'{"real": "data"}')
    assert data_dir.migrate_file("life_journal_budget.json") \
        is False
    assert data_dir.migrate_file("x.json.tmp") is False
    assert not (roots.root / "life_journal_budget.json").exists()


def test_diary_migrates_byte_for_byte(roots):
    lines = [
        {"ts": "2026-10-31T08:31:00", "kind": "activity",
         "did": "napped"},
        {"ts": "2026-10-31T08:37:00", "kind": "journal",
         "text": "The plant is winning."},
        {"ts": "2026-10-31T08:39:00", "kind": "activity",
         "did": "read"},
    ]
    payload = ("\n".join(json.dumps(e) for e in lines)
               + "\n").encode("utf-8")
    _seed(roots.legacy, "life_diary.jsonl", payload)
    entries = life_diary.read_entries()
    assert len(entries) == 3
    canonical = roots.root / "life_diary.jsonl"
    assert canonical.read_bytes() == payload


def test_migration_is_idempotent(roots):
    _seed(roots.legacy, "approval.json", b'{"mode": "auto"}')
    assert approval.get_mode() == "auto"
    # he later changes his mind at the new location...
    approval.set_mode("confirm-risky")
    # ...and a fresh process-style re-resolve must NOT recopy
    # the stale legacy file over it
    data_dir._ATTEMPTED.clear()
    assert approval.get_mode() == "confirm-risky"
    assert json.loads((roots.root / "approval.json").read_text(
        encoding="utf-8"))["mode"] == "confirm-risky"


def test_routines_tree_migrates(roots):
    _seed(roots.legacy, os.path.join("routines", "morning.json"),
          b'{"name": "morning", "steps": []}')
    _seed(roots.legacy, os.path.join("routines", "evening.json"),
          b'{"name": "evening", "steps": []}')
    _seed(roots.legacy, os.path.join("routines", "junk.json.tmp"),
          b'{}')
    d = routines._routines_dir()
    names = sorted(os.listdir(d))
    assert names == ["evening.json", "morning.json"]
    # legacy tree untouched
    assert sorted(os.listdir(roots.legacy / "routines")) == [
        "evening.json", "junk.json.tmp", "morning.json"]


def test_logs_tree_migrates(roots):
    _seed(roots.legacy, os.path.join("logs", "silent-errors.log"),
          b"2026-10-31 [boot] ValueError: old\n")
    copied = data_dir.migrate_tree(
        "logs", root=str(roots.root),
        legacy=str(roots.legacy))
    assert copied == 1
    assert (roots.root / "logs" / "silent-errors.log") \
        .read_bytes() == b"2026-10-31 [boot] ValueError: old\n"
    # and a second pass copies nothing (canonical exists)
    assert data_dir.migrate_tree(
        "logs", root=str(roots.root),
        legacy=str(roots.legacy)) == 0


def test_first_run_flag_migrates(roots):
    """main.py's boot check resolves the flag through
    data_dir.path — a completed first run on C: must still
    count after the move (no wizard re-run)."""
    _seed(roots.legacy, ".first_run_done",
          b"first-run wizard completed (or skipped)\n")
    assert os.path.exists(data_dir.path(".first_run_done"))
    assert (roots.root / ".first_run_done").exists()


def test_persona_storage_migrates(roots):
    _seed(roots.legacy, "persona.json", b'{"persona": "sage"}')
    persona._ensure_migrated()
    canonical = roots.root / "persona.json"
    assert canonical.exists()
    assert json.loads(canonical.read_text(
        encoding="utf-8"))["persona"] == "sage"


def test_bookmark_and_audio_migrate(roots):
    _seed(roots.legacy, "bookmark.json",
          b'{"request": "the thing", "actions": []}')
    loaded = bookmark.load()
    assert loaded is not None
    assert loaded["request"] == "the thing"
    assert (roots.root / "bookmark.json").exists()
    _seed(roots.legacy, "audio.json", b'{"input_device": 3}')
    assert audio_prefs.get_input_device() == 3
    assert (roots.root / "audio.json").exists()


# -- writes ------------------------------------------------------


def test_moved_stores_write_atomically(roots):
    approval.set_mode("auto")
    assert approval.get_mode() == "auto"
    audio_prefs.set_input_device(2)
    assert audio_prefs.get_input_device() == 2
    leftovers = [n for n in os.listdir(roots.root)
                 if n.endswith(".tmp")]
    assert leftovers == []
