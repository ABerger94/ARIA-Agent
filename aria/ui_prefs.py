"""Persisted UI accent preference for the Customize panel.

Stored in ~/ARIA/ui.json, following the same pattern as
aria/approval.py's approval.json and aria/audio_prefs.py's audio.json.
Reads fail soft to None (the default palette): a broken prefs file
must never cost the window its theme — or its startup.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _ui_file() -> str:
    return os.path.join(os.path.expanduser("~"), "ARIA", "ui.json")


def get_accent():
    """Persisted accent hex ('#rrggbb'), or None for the default palette."""
    try:
        with open(_ui_file(), encoding="utf-8") as f:
            value = (json.load(f) or {}).get("accent")
        if isinstance(value, str) and value.startswith("#") \
                and len(value) == 7:
            int(value[1:], 16)
            return value.lower()
        return None
    except Exception:
        return None


def get_centerpiece() -> str:
    """Dashboard centerpiece: 'orb' (default), 'holo', or 'map'
    (the memory constellation, Alek 2026-10-27)."""
    try:
        with open(_ui_file(), encoding="utf-8") as f:
            value = (json.load(f) or {}).get("centerpiece")
        return value if value in ("orb", "holo", "map", "orb3d", "room") else "orb"
    except Exception:
        return "orb"


def set_centerpiece(name: str) -> str:
    """Persist the centerpiece choice; unknown names collapse to 'orb'."""
    name = name if name in ("orb", "holo", "map", "orb3d", "room") else "orb"
    os.makedirs(os.path.dirname(_ui_file()), exist_ok=True)
    payload = {}
    try:
        with open(_ui_file(), encoding="utf-8") as f:
            payload = json.load(f) or {}
    except Exception:
        payload = {}
    payload["centerpiece"] = name
    payload["updated"] = datetime.now(timezone.utc).isoformat()
    with open(_ui_file(), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return f"Centerpiece: {name}."


def get_pet_pos():
    """Saved desktop-pet position [x, y], or None (default corner)."""
    try:
        with open(_ui_file(), encoding="utf-8") as f:
            pos = (json.load(f) or {}).get("pet_pos")
        if (isinstance(pos, list) and len(pos) == 2
                and all(isinstance(v, (int, float)) for v in pos)):
            return [int(pos[0]), int(pos[1])]
    except Exception:
        pass
    return None


def set_pet_pos(pos) -> None:
    """Persist the pet's parked position (soft-fail like the rest of
    this module — a preference must never break the window)."""
    try:
        payload = {}
        try:
            with open(_ui_file(), encoding="utf-8") as f:
                payload = json.load(f) or {}
        except Exception:
            payload = {}
        payload["pet_pos"] = [int(pos[0]), int(pos[1])]
        payload["updated"] = datetime.now(timezone.utc).isoformat()
        os.makedirs(os.path.dirname(_ui_file()), exist_ok=True)
        with open(_ui_file(), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


def get_shell_mode() -> bool:
    """ARIA OS Shell Mode (2026-10-30): when True, the dashboard
    boots fullscreen with Shell Home in front. Default False —
    the shell is opt-in, and the normal dashboard is untouched
    until he turns it on."""
    try:
        with open(_ui_file(), encoding="utf-8") as f:
            return (json.load(f) or {}).get("shell_mode") is True
    except Exception:
        return False


def set_shell_mode(enabled) -> str:
    """Persist the Shell Mode toggle (soft-fail like the rest of
    this module — a preference must never break the window)."""
    enabled = bool(enabled)
    try:
        payload = {}
        try:
            with open(_ui_file(), encoding="utf-8") as f:
                payload = json.load(f) or {}
        except Exception:
            payload = {}
        payload["shell_mode"] = enabled
        payload["updated"] = datetime.now(timezone.utc).isoformat()
        os.makedirs(os.path.dirname(_ui_file()), exist_ok=True)
        with open(_ui_file(), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass
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
    try:
        with open(_ui_file(), encoding="utf-8") as f:
            value = (json.load(f) or {}).get("pinned_apps")
        if isinstance(value, list):
            out = []
            for entry in value:
                path = str(entry or "").strip()
                if path and path not in out:
                    out.append(path)
            return out
    except Exception:
        pass
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
    try:
        payload = {}
        try:
            with open(_ui_file(), encoding="utf-8") as f:
                payload = json.load(f) or {}
        except Exception:
            payload = {}
        payload["pinned_apps"] = cleaned
        groups = payload.get("pin_groups")
        if isinstance(groups, dict):
            payload["pin_groups"] = {
                p: g for p, g in groups.items() if p in cleaned}
        payload["updated"] = datetime.now(timezone.utc).isoformat()
        os.makedirs(os.path.dirname(_ui_file()), exist_ok=True)
        with open(_ui_file(), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass
    return f"Pinned apps: {len(cleaned)}."


def get_pin_groups():
    """Shell Home pin groups (V14, 2026-10-31): {pinned .lnk
    path: group name}. Pins stored before V14 have no entry
    here — they are simply ungrouped; the migration is the
    absence of data, so existing pins and their order carry
    over untouched. Fails soft to {}."""
    try:
        with open(_ui_file(), encoding="utf-8") as f:
            value = (json.load(f) or {}).get("pin_groups")
        if isinstance(value, dict):
            out = {}
            for path, group in value.items():
                p = str(path or "").strip()
                g = str(group or "").strip()
                if p and g:
                    out[p] = g
            return out
    except Exception:
        pass
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
    try:
        payload = {}
        try:
            with open(_ui_file(), encoding="utf-8") as f:
                payload = json.load(f) or {}
        except Exception:
            payload = {}
        payload["pin_groups"] = cleaned
        payload["updated"] = datetime.now(timezone.utc).isoformat()
        os.makedirs(os.path.dirname(_ui_file()), exist_ok=True)
        with open(_ui_file(), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass
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
    try:
        with open(_ui_file(), encoding="utf-8") as f:
            value = (json.load(f) or {}).get("command_history")
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
    except Exception:
        pass
    return {}


def record_command_history(label) -> str:
    """Increment one palette pick's count (soft-fail like the
    rest of this module). Storage is pruned to the 300
    highest-count labels so the file cannot grow unboundedly
    over years of use."""
    label = str(label or "").strip()
    if not label:
        return "Command history: unchanged."
    try:
        payload = {}
        try:
            with open(_ui_file(), encoding="utf-8") as f:
                payload = json.load(f) or {}
        except Exception:
            payload = {}
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
        payload["updated"] = datetime.now(timezone.utc).isoformat()
        os.makedirs(os.path.dirname(_ui_file()), exist_ok=True)
        with open(_ui_file(), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass
    return f"Command history: {label}."


def set_accent(accent) -> str:
    """Persist the accent (None resets to the default palette)."""
    os.makedirs(os.path.dirname(_ui_file()), exist_ok=True)
    payload = {}
    try:
        with open(_ui_file(), encoding="utf-8") as f:
            payload = json.load(f) or {}
    except Exception:
        payload = {}
    payload["accent"] = accent
    payload["updated"] = datetime.now(timezone.utc).isoformat()
    with open(_ui_file(), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    if accent is None:
        return "UI accent: default palette."
    return f"UI accent: {accent}."
