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
    """Dashboard centerpiece: 'orb' (default) or 'holo'."""
    try:
        with open(_ui_file(), encoding="utf-8") as f:
            value = (json.load(f) or {}).get("centerpiece")
        return value if value in ("orb", "holo") else "orb"
    except Exception:
        return "orb"


def set_centerpiece(name: str) -> str:
    """Persist the centerpiece choice; unknown names collapse to 'orb'."""
    name = name if name in ("orb", "holo") else "orb"
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
