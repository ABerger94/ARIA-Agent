# Ported from Mark-LVI-ARIA core/backup_mirror.py +
# actions/set_backup_mirror.py (commit 61a47b0e) — adapted only in
# module references (aria.config settings instead of
# memory.config_manager, Agent's own stores instead of LVI's).
# See LICENSES/NOTICE.md.
"""
aria/backup_mirror.py — the backups, copied off the machine.

WHY THIS EXISTS
    The stores whose loss hurts keep their data on one disk, in
    one folder tree. A torn write can be cured by a local backup;
    a dead drive, a stolen laptop, or a folder deleted in a
    spring clean takes the original and every local copy with it.

    So: a mirror directory the user chooses (setting
    "backup_mirror_dir") — a folder synced by something else: a
    Google Drive or OneDrive folder, a NAS mount, a second drive.
    Mirrored files land there flat, by basename, and restoring is
    copying them back by hand — no code path in this app reads
    the mirror, which is deliberate: a backup the app can read is
    a backup the app can corrupt.

THE STORES THAT MIRROR (backup_slots())
    workspace/aria_memory.db, workspace/settings.json, and
    workspace/chat_history.md. ROOT_DIR/aria_keys.json is
    EXCLUDED — secrets never mirror: a synced folder is exactly
    the wrong place for the key file, and no convenience here is
    worth copying credentials off the machine implicitly.

THE RULES
    * NEVER BREAK THE PRIMARY PATH. Mirroring runs after the
      real data has landed, swallows every failure, and reports
      a broken mirror exactly once per process (the latch);
      after that it degrades silently — a dead mirror must not
      become a per-save incident generator, and must never cost
      a save.
    * A COPY IS VERIFIED BEFORE IT COUNTS. Sizes must match
      after the copy; a short copy is a failed mirror, not a
      small one.
"""

from __future__ import annotations

import shutil
from pathlib import Path

_failed = False   # the once-per-process incident latch


# ── Settings / config seams (small helpers tests can monkeypatch) ──

def _get_setting(key, default=None):
    """aria.config.get_setting, lazily — any failure reads as the
    default, so a settings lookup can never break a save."""
    try:
        from aria.config import get_setting
        return get_setting(key, default)
    except Exception:
        return default


def _set_setting(key, value):
    """aria.config.set_setting, lazily; failures are swallowed
    (the caller verifies by re-reading, which is the honest check)."""
    try:
        from aria.config import set_setting
        return set_setting(key, value)
    except Exception:
        return None


def _config_paths():
    """(WORKSPACE_DIR, ROOT_DIR) from aria.config, lazily, with a
    filesystem-derived fallback so this module loads anywhere."""
    try:
        from aria import config
        return str(config.WORKSPACE_DIR), str(config.ROOT_DIR)
    except Exception:
        root = Path(__file__).resolve().parent.parent
        return str(root / "workspace"), str(root)


def _log_failure(msg: str) -> None:
    """The once-per-process incident line, via aria.config.add_log
    when available. Guarded twice: logging a mirror failure must
    never become a failure of its own."""
    try:
        from aria.config import add_log
        add_log(f"[backup_mirror] {msg}")
    except Exception:
        pass


# ── Outcome seam (aria.outcome is a sibling port; lazy + fallback) ──

def _outcome_module():
    try:
        import importlib
        return importlib.import_module("aria.outcome")
    except Exception:
        return None


def _render_facts(facts) -> str:
    parts = []
    for k, v in (facts or {}).items():
        if v is None:
            continue
        if isinstance(v, bool):
            text = "true" if v else "false"
        else:
            text = str(v).replace('"', "'").replace("\n", " ").strip()
        parts.append(f'{k}="{text}"' if (" " in text or not text)
                     else f"{k}={text}")
    return (" " + " ".join(parts)) if parts else ""


def _done(**facts) -> str:
    mod = _outcome_module()
    if mod is not None:
        return mod.done("set_backup_mirror", **facts)
    return f"status=done action=set_backup_mirror{_render_facts(facts)}"


def _failed_out(reason, **facts) -> str:
    mod = _outcome_module()
    if mod is not None:
        return mod.failed("set_backup_mirror", reason, **facts)
    return (f"status=failed action=set_backup_mirror"
            f"{_render_facts(facts)}"
            f' reason="{reason}"'
            " -- This did NOT happen. Tell the user so in English, "
            "with the reason. Do not claim any part of it succeeded.")


def _unverified(reason, **facts) -> str:
    mod = _outcome_module()
    if mod is not None:
        return mod.unverified("set_backup_mirror", reason, **facts)
    return (f"status=unverified action=set_backup_mirror"
            f"{_render_facts(facts)}"
            f' reason="{reason}"'
            " -- NOT confirmed. Tell the user plainly that you asked "
            "and cannot confirm it worked, and say what they can "
            "check. Do NOT say it is done.")


# ── The mirror ─────────────────────────────────────────────────────

def mirror_dir() -> str:
    """The configured mirror directory, or '' when unset. Read
    through aria.config's get_setting('backup_mirror_dir', '');
    any failure reads as unset — a mirror lookup must never
    break the save that triggered it."""
    try:
        value = _get_setting("backup_mirror_dir", "")
        return str(value or "")
    except Exception:
        return ""


def mirror_backups(slots) -> bool:
    """Copy every existing file in `slots` into the mirror
    directory, by basename, verifying sizes after the copy.
    True when the mirror is unset (nothing to do — not a
    failure) or every copy landed and verified; False when a
    copy failed. Never raises."""
    global _failed
    dest_dir = mirror_dir()
    if not dest_dir:
        return True
    try:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        for slot in slots or []:
            slot = Path(slot)
            if not slot.exists():
                continue
            target = dest / slot.name
            shutil.copyfile(slot, target)
            if target.stat().st_size != slot.stat().st_size:
                raise OSError(
                    f"mirror copy of {slot.name} is short "
                    f"({target.stat().st_size} of "
                    f"{slot.stat().st_size} bytes)")
        return True
    except Exception as e:
        if not _failed:
            _failed = True
            _log_failure(
                f"could not mirror backups to {dest_dir!r}: {e} "
                f"— the on-machine files are unaffected; further "
                f"failures this run are silent")
        return False


def backup_slots():
    """The files whose loss hurts, as Paths (existing or not):
    the memory DB, the settings file, and the chat history —
    all under WORKSPACE_DIR. ROOT_DIR/aria_keys.json is
    deliberately NOT in this list: secrets never mirror."""
    workspace, _root = _config_paths()
    ws = Path(workspace)
    return [
        ws / "aria_memory.db",
        ws / "settings.json",
        ws / "chat_history.md",
    ]


def snapshot_and_mirror() -> bool:
    """Mirror the current backup_slots() now. Call sites that
    write one of the mirrored stores use this after the write
    lands; the mirror itself never raises."""
    return mirror_backups(backup_slots())


def reset_latch_for_tests() -> None:
    global _failed
    _failed = False


# ── The setting's tool surface ─────────────────────────────────────

def tool_set_backup_mirror(args=None) -> str:
    """Set (clear=true / path=...) or report the mirror folder.

    Ported LVI semantics: clearing and setting both verify by
    re-reading the setting; setting mirrors the current slots
    immediately and reports honestly — done only when the first
    copies landed and verified, unverified when the copy failed,
    done-with-a-note when no backups exist yet to copy."""
    params = args or {}
    if params.get("clear"):
        _set_setting("backup_mirror_dir", "")
        if mirror_dir() == "":
            return _done(mirror="off") + (
                "\nBackup mirroring is off. The on-machine backup "
                "rotations are unchanged.")
        return _failed_out(
            "the mirror setting did not clear on re-read")

    path = str(params.get("path") or "").strip()
    if not path:
        current = mirror_dir()
        if current:
            return _done(mirror=current) + (
                f"\nBackups are mirrored to {current}. Pass a new "
                f"path to move the mirror, or clear=true to stop.")
        return _failed_out(
            "no mirror folder is set and no path was given — name "
            "a folder (a Drive or OneDrive folder works well)")

    _set_setting("backup_mirror_dir", path)
    if mirror_dir() != path:
        return _failed_out(
            "the mirror setting did not save on re-read")

    slots = backup_slots()
    ok = mirror_backups(slots)
    any_backups = any(Path(p).exists() for p in slots)
    if not any_backups:
        return _done(mirror=path) + (
            f"\nBackups will mirror to {path}. No backups exist "
            f"yet to copy — the first snapshot each store writes "
            f"will land there.")
    if not ok:
        return _unverified(
            "the mirror folder was saved but the first copy to it "
            "failed — it may be unwritable; the failure is in the "
            "incident log", mirror=path)
    return _done(mirror=path) + (
        f"\nBackups now mirror to {path} — the current rotations "
        f"were copied there and verified.")


# Alias matching the LVI action's handler name.
set_backup_mirror = tool_set_backup_mirror
