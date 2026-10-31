"""Persisted UI preferences for the dashboard + Shell Home.

Stored in ui.json at the INSTALL ROOT — the directory
aria/config.py calls ROOT_DIR (the parent of this package),
where aria.py and aria_keys.json live (E:\\ARIA on Alek's
machine). Reads fail soft to defaults: a broken prefs file
must never cost the window its theme — or its startup.

V19 (2026-10-31, Alek's live report on V18 — "It still says
no pinned apps when I pin an app", his ui.json "is also
empty", "why are they saving to C drive? ARIA files live in
E:\\ARIA"): pre-V19 this file resolved through
os.path.expanduser("~")/ARIA — C:\\Users\\Allen\\ARIA, a
directory nothing else in the install uses. Any process
whose home resolved differently (auto-start / scheduled-task
contexts) read and wrote a DIFFERENT ui.json, writers
swallowed every exception behind a success string, and the
write itself was truncate-then-dump: a write that died
mid-dump left a corrupt husk that every later read fail-
softed to defaults, forever, invisibly. Three fixes:

- The resolver is install-relative (config.ROOT_DIR's exact
  formula), never home-derived. A one-time migration merges
  any real prefs from the legacy home location (keys the
  canonical file already has win; the legacy file is left in
  place, untouched).
- Every write is atomic: temp file in the same directory +
  os.replace (the bookmark.py / life_state pattern), so an
  interrupted write leaves the previous good file standing.
- Writers funnel through one locked read-modify-write, and
  a failed write is LOGGED (config.log_silent + a console
  line) instead of swallowed — prefs stay fail-soft for the
  window, but a dying write is never silent again.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone

#: The install root, computed exactly as aria/config.py
#: computes ROOT_DIR (importing config here would drag its
#: import side effects — keys load, workspace creation — into
#: every prefs read). A module global read at call time so
#: tests can redirect the store hermetically.
_INSTALL_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))

#: Serializes read-modify-write across this process's writers
#: (accent vs pins vs groups can no longer clobber each other).
_LOCK = threading.RLock()

#: The (canonical, legacy) path pair migration already ran
#: for in this process; migration is once per pair.
_MIGRATED_PAIR = None


def _ui_file() -> str:
    return os.path.join(_INSTALL_ROOT, "ui.json")


def _legacy_ui_file() -> str:
    """The pre-V19 location (~/ARIA/ui.json). Read once, for
    migration only — never written again."""
    return os.path.join(os.path.expanduser("~"), "ARIA",
                        "ui.json")


def _log_failure(what, exc):
    """Make a prefs failure VISIBLE: the silent-errors log via
    config.log_silent (lazy import — see _INSTALL_ROOT's
    note), with a console line as fallback. Never raises."""
    try:
        from aria.config import log_silent
        log_silent(f"ui_prefs {what}", exc)
        return
    except Exception:
        pass
    try:
        print(f"[ARIA] ui_prefs: {what} failed: "
              f"{type(exc).__name__}: {exc}", flush=True)
    except Exception:
        pass


def _read_payload_at(path):
    """Parse the prefs file at *path* to a dict; None when it
    is missing, unparseable, or not a JSON object."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_payload(payload):
    """Atomically persist the whole payload: temp file in the
    SAME directory + os.replace, so the target is never seen
    truncated. Raises on failure — callers surface it."""
    path = _ui_file()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _ensure_migrated():
    """One-time merge of the legacy home prefs (see the module
    docstring). Canonical values win per key; the legacy file
    is left untouched. A legacy file that is missing, empty or
    corrupt migrates nothing — his reported case: the C: file
    was an empty husk, so the fix there is simply that writes
    land at the install root from now on. Skipped when
    _ui_file has been redirected away from the canonical path
    (tests, embedders): migration is a property of the real
    install location. Fail-soft but logged."""
    global _MIGRATED_PAIR
    try:
        new = _ui_file()
        canonical = os.path.join(_INSTALL_ROOT, "ui.json")
        if new != canonical:
            return
        legacy = _legacy_ui_file()
        pair = (new, legacy)
        if _MIGRATED_PAIR == pair or new == legacy:
            return
        _MIGRATED_PAIR = pair
        legacy_payload = _read_payload_at(legacy)
        if not legacy_payload:
            return
        current = _read_payload_at(new) or {}
        merged = dict(legacy_payload)
        merged.update(current)
        if merged != current:
            _write_payload(merged)
            try:
                print(f"[ARIA] ui_prefs: migrated prefs from "
                      f"{legacy} to {new} "
                      f"({len(merged)} keys).", flush=True)
            except Exception:
                pass
    except Exception as exc:
        _log_failure("legacy migration", exc)


def _read_payload():
    """The current prefs dict ({} when the file is missing or
    broken — the fail-soft read contract, unchanged)."""
    _ensure_migrated()
    return _read_payload_at(_ui_file()) or {}


def _update_payload(mutator):
    """Locked read-modify-write for every setter: *mutator*
    edits the payload dict in place, the whole payload is
    then written atomically. Returns True when the write
    landed; a failed write is logged, never raised, never
    silent."""
    with _LOCK:
        _ensure_migrated()
        payload = _read_payload_at(_ui_file()) or {}
        mutator(payload)
        payload["updated"] = datetime.now(
            timezone.utc).isoformat()
        try:
            _write_payload(payload)
            return True
        except Exception as exc:
            _log_failure("write", exc)
            return False


def get_accent():
    """Persisted accent hex ('#rrggbb'), or None for the default palette."""
    value = _read_payload().get("accent")
    if isinstance(value, str) and value.startswith("#") \
            and len(value) == 7:
        try:
            int(value[1:], 16)
        except ValueError:
            return None
        return value.lower()
    return None


def get_centerpiece() -> str:
    """Dashboard centerpiece: 'orb' (default), 'holo', or 'map'
    (the memory constellation, Alek 2026-10-27)."""
    value = _read_payload().get("centerpiece")
    return value if value in ("orb", "holo", "map", "orb3d", "room") else "orb"


def set_centerpiece(name: str) -> str:
    """Persist the centerpiece choice; unknown names collapse to 'orb'."""
    name = name if name in ("orb", "holo", "map", "orb3d", "room") else "orb"

    def apply(payload):
        payload["centerpiece"] = name

    _update_payload(apply)
    return f"Centerpiece: {name}."


def get_pet_pos():
    """Saved desktop-pet position [x, y], or None (default corner)."""
    pos = _read_payload().get("pet_pos")
    if (isinstance(pos, list) and len(pos) == 2
            and all(isinstance(v, (int, float)) for v in pos)):
        return [int(pos[0]), int(pos[1])]
    return None


def set_pet_pos(pos) -> None:
    """Persist the pet's parked position (soft-fail like the rest of
    this module — a preference must never break the window)."""
    try:
        xy = [int(pos[0]), int(pos[1])]
    except Exception:
        return

    def apply(payload):
        payload["pet_pos"] = xy

    _update_payload(apply)


def get_shell_mode() -> bool:
    """ARIA OS Shell Mode (2026-10-30): when True, the dashboard
    boots fullscreen with Shell Home in front. Default False —
    the shell is opt-in, and the normal dashboard is untouched
    until he turns it on."""
    return _read_payload().get("shell_mode") is True


def set_shell_mode(enabled) -> str:
    """Persist the Shell Mode toggle (soft-fail like the rest of
    this module — a preference must never break the window)."""
    enabled = bool(enabled)

    def apply(payload):
        payload["shell_mode"] = enabled

    _update_payload(apply)
    return f"Shell Mode: {'on' if enabled else 'off'}."


def get_pinned_apps():
    """Shell Home pinned apps (V8, 2026-10-30): the ordered list
    of pin KEYS Alek pinned to the home grid. Pin identity is
    app_launcher.app_key — the .lnk path for a Start Menu app
    (every pre-V17 pin), and appid:<AppID> for an AppsFolder
    app (V17: Store apps like Spotify have no .lnk path to
    store). Fails soft to [] (no pins = the shell's empty
    state, never a broken home), drops blanks and duplicates,
    preserves pin order."""
    value = _read_payload().get("pinned_apps")
    if isinstance(value, list):
        out = []
        for entry in value:
            path = str(entry or "").strip()
            if path and path not in out:
                out.append(path)
        return out
    return []


def set_pinned_apps(paths) -> str:
    """Persist the pinned .lnk paths in pin order (soft-fail
    like the rest of this module — a preference must never
    break the window). V14: the pin_groups map is pruned to
    the surviving paths in the same write, so an unpinned app
    leaves no orphaned group behind."""
    cleaned = []
    for entry in paths or []:
        path = str(entry or "").strip()
        if path and path not in cleaned:
            cleaned.append(path)

    def apply(payload):
        payload["pinned_apps"] = cleaned
        groups = payload.get("pin_groups")
        if isinstance(groups, dict):
            payload["pin_groups"] = {
                p: g for p, g in groups.items() if p in cleaned}

    _update_payload(apply)
    return f"Pinned apps: {len(cleaned)}."


def get_pin_groups():
    """Shell Home pin groups (V14, 2026-10-31): {pinned .lnk
    path: group name}. Pins stored before V14 have no entry
    here — they are simply ungrouped; the migration is the
    absence of data, so existing pins and their order carry
    over untouched. Fails soft to {}."""
    value = _read_payload().get("pin_groups")
    if isinstance(value, dict):
        out = {}
        for path, group in value.items():
            p = str(path or "").strip()
            g = str(group or "").strip()
            if p and g:
                out[p] = g
        return out
    return {}


def set_pin_groups(groups) -> str:
    """Persist the {path: group} map (soft-fail). Blank groups
    and blank paths drop out — ungrouped is the absence of an
    entry, never an empty string stored."""
    cleaned = {}
    for path, group in (groups or {}).items():
        p = str(path or "").strip()
        g = str(group or "").strip()
        if p and g:
            cleaned[p] = g

    def apply(payload):
        payload["pin_groups"] = cleaned

    _update_payload(apply)
    return f"Pin groups: {len(cleaned)}."


def get_pinned_entries():
    """The V14 entry view of the pins: [{path, group}] in pin
    order, merging the ordered path list with the group map.
    This is the migration surface — a pre-V14 ui.json (plain
    path list, no map) yields every pin ungrouped, order and
    membership exactly preserved."""
    groups = get_pin_groups()
    return [{"path": p, "group": groups.get(p, "")}
            for p in get_pinned_apps()]


def get_command_history():
    """Shell Home command-palette history (V12, 2026-10-31):
    {candidate label: times chosen}, used to boost his frequent
    picks in the palette ranking. Fails soft to {} — no history
    means unboosted ranking, never a broken palette."""
    value = _read_payload().get("command_history")
    if isinstance(value, dict):
        out = {}
        for key, count in value.items():
            label = str(key or "").strip()
            try:
                n = int(count)
            except (TypeError, ValueError):
                continue
            if label and n > 0:
                out[label] = n
        return out
    return {}


def record_command_history(label) -> str:
    """Increment one palette pick's count (soft-fail like the
    rest of this module). Storage is pruned to the 300
    highest-count labels so the file cannot grow unboundedly
    over years of use."""
    label = str(label or "").strip()
    if not label:
        return "Command history: unchanged."

    def apply(payload):
        history = payload.get("command_history")
        if not isinstance(history, dict):
            history = {}
        try:
            history[label] = int(history.get(label, 0)) + 1
        except (TypeError, ValueError):
            history[label] = 1
        if len(history) > 300:
            history = dict(sorted(
                history.items(), key=lambda kv: -int(kv[1]))[:300])
        payload["command_history"] = history

    _update_payload(apply)
    return f"Command history: {label}."


def set_accent(accent) -> str:
    """Persist the accent (None resets to the default palette)."""

    def apply(payload):
        payload["accent"] = accent

    _update_payload(apply)
    if accent is None:
        return "UI accent: default palette."
    return f"UI accent: {accent}."
