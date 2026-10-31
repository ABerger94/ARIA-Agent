"""The ONE canonical app-data location for ARIA (V20, 2026-10-31).

Every persisted state file ARIA owns lives at the INSTALL ROOT
— the directory aria/config.py calls ROOT_DIR (the parent of
this package), where aria.py and aria_keys.json live (E:\\ARIA
on Alek's machine). This module generalizes the fix V19 made
for ui_prefs alone (aria/ui_prefs.py) to the whole state-file
family: approval.json, audio.json, bookmark.json, persona.json,
screen_watches.json, reminder_gate.json, the life-room files
(life_state / life_control / life_diary / the journal pair +
budget), the routines directory, the logs directory, and the
first-run flag.

Pre-V20 those stores resolved through os.path.expanduser("~")/
ARIA — C:\\Users\\Allen\\ARIA, a home-derived directory almost
nothing else in the install uses. Alek, looking at that folder
on C: while ARIA runs from E:\\ARIA: "Did all of these files
change to E:\\ARIA as well? They need to all live in the
E:\\ARIA folder with the rest of her code." They had not, and
they do now.

Design (the ui_prefs V19 pattern, shared):

- The root is a module global read at call time, computed with
  config.ROOT_DIR's exact formula. Importing config here would
  drag its import side effects (keys load, workspace creation)
  into every store read, so the formula is duplicated, never
  the import. Tests redirect the root by rebinding _ROOT.
- Migration: the first time a state file is resolved in a
  process (and the root has NOT been redirected — migration is
  a property of the real install location), a real legacy file
  from ~/ARIA is COPIED to the canonical location when the
  canonical file is missing. Canonical wins when both exist;
  the legacy file is left byte-untouched as an archive; empty
  husks and *.tmp strays carry nothing. Migration is
  copy-if-missing, so it is naturally idempotent, and it never
  deletes anything.
- A failed migration is LOGGED (config.log_silent, lazily
  imported, + a console line) — never silent, never raised:
  state stores stay fail-soft for the app, exactly as before.

Already canonical before V20 (verified, untouched): the memory
SQLite DB, settings, mood, triggers, inbox, the schedule file
and aria_keys.json (all ROOT_DIR / WORKSPACE_DIR based).
NOT app data and deliberately left home-relative: user-folder
features (Downloads/Desktop lookups, the shortcut maker, shell
cwd expansion of paths HE supplies) and the tool workspace
(~/robot_workspace) — the latter is his separate call.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading

#: The install root, computed exactly as aria/config.py
#: computes ROOT_DIR. A module global read at call time so
#: tests can redirect every store hermetically.
_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))

_LOCK = threading.RLock()

#: (kind, name) migrations already attempted in this process;
#: the auto-migration in path()/subdir() runs once per name.
_ATTEMPTED = set()


def _true_root() -> str:
    """The install root from this file's location, ignoring
    any test redirection of _ROOT."""
    return os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))


def root() -> str:
    """The canonical app-data directory: the install root."""
    return _ROOT


def _redirected() -> bool:
    """True when a test/embedder has rebound _ROOT away from
    the real install root — auto-migration is skipped then
    (it belongs to the real install location only)."""
    return os.path.normpath(_ROOT) != os.path.normpath(
        _true_root())


def legacy_dir() -> str:
    """The pre-V20 location (~/ARIA). Read for migration
    only — never written again."""
    return os.path.join(os.path.expanduser("~"), "ARIA")


def legacy_path(name: str) -> str:
    return os.path.join(legacy_dir(), name)


def _log_failure(what, exc):
    """Make a data-dir failure VISIBLE: the silent-errors log
    via config.log_silent (lazy import — see _ROOT's note),
    with a console line as fallback. Never raises."""
    try:
        from aria.config import log_silent
        log_silent(f"data_dir {what}", exc)
        return
    except Exception:
        pass
    try:
        print(f"[ARIA] data_dir: {what} failed: "
              f"{type(exc).__name__}: {exc}", flush=True)
    except Exception:
        pass


def _announce(what):
    try:
        print(f"[ARIA] data_dir: {what}", flush=True)
    except Exception:
        pass


def migrate_file(name, *, root=None, legacy=None) -> bool:
    """Copy one legacy state file to the canonical location.

    Carries the file over only when ALL of these hold: the
    canonical file is missing, the legacy file exists, and it
    has real content (size > 0 — a 0-byte husk is the scar of
    an interrupted pre-V19 write and carries nothing). *.tmp
    strays are never carried. The legacy file is left
    byte-untouched. Returns True when a copy landed. Fail-soft
    but logged."""
    if name.endswith(".tmp"):
        return False
    canonical = os.path.join(
        root if root is not None else _ROOT, name)
    source = os.path.join(
        legacy if legacy is not None else legacy_dir(), name)
    if os.path.normpath(canonical) == os.path.normpath(source):
        return False
    try:
        if os.path.exists(canonical):
            return False
        if not os.path.isfile(source):
            return False
        if os.path.getsize(source) <= 0:
            return False
        os.makedirs(os.path.dirname(canonical), exist_ok=True)
        shutil.copyfile(source, canonical)
        _announce(f"migrated {name} from {source} to "
                  f"{canonical} (legacy left in place).")
        return True
    except Exception as exc:
        _log_failure(f"migration of {name}", exc)
        return False


def migrate_tree(subdir, *, root=None, legacy=None) -> int:
    """Copy-if-missing for a whole state subdirectory
    (routines/, logs/): every real file in the legacy subdir
    that the canonical subdir lacks is carried over, with
    migrate_file's rules per file. Returns the count copied.
    Fail-soft but logged."""
    copied = 0
    try:
        source_dir = os.path.join(
            legacy if legacy is not None else legacy_dir(),
            subdir)
        if not os.path.isdir(source_dir):
            return 0
        for fname in sorted(os.listdir(source_dir)):
            if fname.endswith(".tmp"):
                continue
            if not os.path.isfile(
                    os.path.join(source_dir, fname)):
                continue
            if migrate_file(os.path.join(subdir, fname),
                            root=root, legacy=legacy):
                copied += 1
        return copied
    except Exception as exc:
        _log_failure(f"migration of {subdir}/", exc)
        return copied


def _attempt_once(kind, name, fn):
    key = (kind, name)
    with _LOCK:
        if key in _ATTEMPTED:
            return
        _ATTEMPTED.add(key)
    fn()


def path(name: str) -> str:
    """The canonical path of a state file. The first resolve
    in a process also runs that file's one-time legacy
    migration (skipped when the root is test-redirected)."""
    p = os.path.join(_ROOT, name)
    if not _redirected():
        _attempt_once("file", name,
                      lambda: migrate_file(name))
    return p


def subdir(name: str) -> str:
    """The canonical path of a state subdirectory (routines/,
    logs/). Same one-time tree migration contract as path()."""
    p = os.path.join(_ROOT, name)
    if not _redirected():
        _attempt_once("tree", name,
                      lambda: migrate_tree(name))
    return p


def write_json_atomic(path, payload, indent=2):
    """Persist a JSON payload atomically: temp file in the
    SAME directory + os.replace, so the target is never seen
    truncated (the bookmark.py / ui_prefs pattern). Raises on
    failure — callers decide how to surface it."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
