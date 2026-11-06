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
    worth copying credentials off the machine implicitly. The
    same rule applies INSIDE settings.json: MCP server env and
    headers (API keys, auth headers) live there verbatim, so the
    mirrored copy is redacted — the local file is untouched, the
    mirror gets markers where values were.

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
import os
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

_failed = False   # the once-per-process incident latch
_COPY_LOCK = threading.Lock()
_QUEUE_LOCK = threading.Lock()
_QUEUED_PROJECTS = set()
_WORKER = None


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


_REDACTED = "[redacted — re-enter after restore]"
_SECRET_KEYS = {"api_key", "api-key", "token", "secret",
                "password", "passwd"}


def _is_secret_key(low: str) -> bool:
    """A key that names a credential: exactly a secret name, or
    ending in one after a separator (some_api_key, my-token)."""
    return any(low == name or low.endswith("_" + name)
               or low.endswith("-" + name)
               for name in _SECRET_KEYS)


# Credential-like CLI flags and URL query parameters (pass 26
# B3#7): MCP configs carry args lists and url strings, so a
# credential can hide as the value after --api-key or as
# ?access_token=... — neither is an env/headers key or a
# credential-named key, so _redact_settings used to miss them.
_SECRET_FLAG_SEGMENTS = frozenset({
    "token", "secret", "password", "passwd", "auth", "key",
    "credential", "credentials", "private", "apikey"})
_CREDENTIAL_PARAMS = frozenset({
    "access_token", "api_key", "apikey", "token", "auth",
    "password", "passwd", "secret", "client_secret",
    "client-secret", "access_key", "session", "bearer"})


def _is_credential_flag(flag: str) -> bool:
    """True when a CLI flag names a credential: any dash-separated
    segment of the flag is a secret word (--api-key, --token,
    --github-token). Segment matching (not substring) so
    --author does not count."""
    name = str(flag).strip().lower().lstrip("-").replace("_", "-")
    return any(seg in _SECRET_FLAG_SEGMENTS
               for seg in name.split("-") if seg)


def _is_credential_param(name: str) -> bool:
    """True when a URL query parameter names a credential."""
    low = str(name).lower()
    return low in _CREDENTIAL_PARAMS or _is_secret_key(low)


def _scrub_url_text(text: str) -> str:
    """Replace credential material inside a URL-valued string:
    query parameters naming a credential, and a password in the
    userinfo. Returns the input unchanged when there is nothing
    to scrub (so benign URLs are never re-encoded)."""
    if "://" not in text:
        return text
    try:
        from urllib.parse import (urlsplit, urlunsplit, parse_qsl,
                                  urlencode, quote)
        parts = urlsplit(text)
        if not parts.netloc:
            return text
        netloc = parts.netloc
        query = parts.query
        changed = False
        if parts.password is not None:
            try:
                port = parts.port
            except ValueError:
                port = None
            host = parts.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            if port:
                host = f"{host}:{port}"
            userinfo = ""
            if parts.username:
                userinfo = parts.username + ":" + quote(_REDACTED, safe="")
            else:
                userinfo = ":" + quote(_REDACTED, safe="")
            netloc = userinfo + "@" + host
            changed = True
        if query:
            pairs = parse_qsl(query, keep_blank_values=True)
            scrubbed = [(k, _REDACTED if _is_credential_param(k) else v)
                        for k, v in pairs]
            if scrubbed != pairs:
                query = urlencode(scrubbed, doseq=True)
                changed = True
        if not changed:
            return text
        return urlunsplit((parts.scheme, netloc, parts.path, query,
                           parts.fragment))
    except Exception:
        return text


def _redact_settings(obj) -> None:
    """Strip secrets from a parsed settings object, in place.
    MCP server env/headers are redacted per value (key names
    stay, so a restore shows what to re-enter); any key named
    like a credential is redacted wholesale. Non-secret settings
    pass through untouched."""
    if isinstance(obj, dict):
        for key, value in list(obj.items()):
            low = str(key).lower()
            if low in ("env", "headers") and isinstance(value, dict):
                for sub in value:
                    value[sub] = _REDACTED
            elif _is_secret_key(low):
                obj[key] = _REDACTED
            elif isinstance(value, str):
                # A URL-valued setting can carry credentials in
                # its query string or userinfo (pass 26 B3#7).
                obj[key] = _scrub_url_text(value)
            else:
                _redact_settings(value)
    elif isinstance(obj, list):
        prev_flag = False
        for i, item in enumerate(obj):
            if not isinstance(item, str):
                prev_flag = False
                _redact_settings(item)
                continue
            if prev_flag and not item.startswith("-"):
                # The value after a credential flag (pass 26 B3#7).
                obj[i] = _REDACTED
                prev_flag = False
                continue
            prev_flag = False
            if item.startswith("--"):
                if "=" in item:
                    flag, _, _val = item.partition("=")
                    if _is_credential_flag(flag):
                        obj[i] = flag + "=" + _REDACTED
                elif _is_credential_flag(item):
                    prev_flag = True
                continue
            if "://" in item:
                obj[i] = _scrub_url_text(item)
                continue
            if "=" in item:
                key, _, _val = item.partition("=")
                if _is_secret_key(key.strip().lower()):
                    obj[i] = key + "=" + _REDACTED


def _mirror_redacted_settings(slot: Path, temp: str) -> None:
    """Write a secret-free copy of settings.json to `temp`.
    Raises OSError when the file is not a JSON object — an
    unreadable settings file is not mirrored at all rather than
    copied verbatim with unknown contents."""
    import json
    try:
        data = json.loads(slot.read_text(encoding="utf-8"))
    except Exception as e:
        raise OSError(f"settings.json is not valid JSON — not mirrored: {e}")
    if not isinstance(data, dict):
        raise OSError("settings.json is not an object — not mirrored")
    _redact_settings(data)
    Path(temp).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


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
        from aria import workspaces
        dest = Path(dest_dir)
        if workspaces.current() != 'default':
            dest = dest / 'projects' / workspaces.current()
        with _COPY_LOCK:
            dest.mkdir(parents=True, exist_ok=True)
            for slot in slots or []:
                slot = Path(slot)
                if not slot.is_file():
                    continue
                target = dest / slot.name
                if target.resolve() == slot.resolve():
                    continue
                fd, temp = tempfile.mkstemp(prefix='.aria-mirror-', dir=dest)
                os.close(fd)
                try:
                    with slot.open('rb') as stream:
                        is_sqlite = stream.read(16) == b'SQLite format 3\x00'
                    if is_sqlite:
                        deadline = time.monotonic() + 10
                        def progress(*args):
                            if time.monotonic() > deadline:
                                raise TimeoutError('Database backup exceeded 10 seconds')
                        # A raw copy can omit WAL contents or tear across a
                        # transaction. SQLite supplies a consistent snapshot.
                        with closing(sqlite3.connect(slot.resolve().as_uri() + '?mode=ro', uri=True)) as source:
                            with closing(sqlite3.connect(temp)) as snapshot:
                                source.backup(snapshot, pages=128, progress=progress)
                                if snapshot.execute('PRAGMA quick_check').fetchone() != ('ok',):
                                    raise OSError('Database snapshot did not pass quick_check')
                    else:
                        if slot.name == "settings.json":
                            # Redacted, not raw: settings.json
                            # carries MCP credentials verbatim.
                            # (Size verification does not apply —
                            # the redacted copy is shorter by
                            # design; the write itself is the check.)
                            _mirror_redacted_settings(slot, temp)
                        else:
                            shutil.copyfile(slot, temp)
                            if Path(temp).stat().st_size != slot.stat().st_size:
                                raise OSError(f'mirror copy of {slot.name} is short')
                    os.replace(temp, target)
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
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
    deliberately NOT in this list: secrets never mirror. The
    settings file mirrors with its secrets redacted (see
    _mirror_redacted_settings), never verbatim."""
    from aria import config, memory, workspaces
    return [
        Path(memory.db_path()),
        Path(config.SETTINGS_FILE),
        Path(workspaces.path('chat_history.md', memory.CHAT_LOG_FILE)),
    ]


def snapshot_and_mirror() -> bool:
    """Mirror the current backup_slots() now. Call sites that
    write one of the mirrored stores use this after the write
    lands; the mirror itself never raises."""
    return mirror_backups(backup_slots())


def queue_snapshot() -> None:
    """Coalesce saves on one daemon; a slow synced drive never blocks a save."""
    global _WORKER
    try:
        if not mirror_dir():
            return
        from aria import workspaces
        with _QUEUE_LOCK:
            _QUEUED_PROJECTS.add(workspaces.current())
            if _WORKER is not None:
                return
            _WORKER = threading.Thread(target=_drain_snapshots, daemon=True,
                                       name='aria-backup-mirror')
            try:
                _WORKER.start()
            except Exception:
                _WORKER = None
                raise
    except Exception as exc:
        _log_failure(f'could not queue a mirror snapshot: {exc}')


def _drain_snapshots():
    global _WORKER
    from aria import workspaces
    while True:
        with _QUEUE_LOCK:
            if not _QUEUED_PROJECTS:
                _WORKER = None
                return
            project = _QUEUED_PROJECTS.pop()
        try:
            with workspaces.use(project):
                snapshot_and_mirror()
        except Exception as exc:
            _log_failure(f'could not snapshot project {project}: {exc}')


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
    if "clear" in params and params["clear"] is not False:
        # Only a literal bool True clears (pass 26 B3#1): the
        # string "false" is truthy in Python, so the old
        # `if params.get("clear"):` cleared the mirror while
        # needs_approval() read it as False and never parked the
        # call. Any other present value is rejected outright —
        # the mirror is untouched, even on an approved replay.
        if params["clear"] is not True:
            return _failed_out(
                "clear must be a boolean true or false — the "
                "mirror was not changed")
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
