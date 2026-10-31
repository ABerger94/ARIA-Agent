"""Persisted audio input-device preference for the Audio panel.

Stored in audio.json at the install root (aria/data_dir.py, V20;
pre-V20 ~/ARIA/audio.json), following aria/approval.py's approval.json
precedent. The microphone backend (aria/speech.py's SoundDeviceSource)
already accepts a device index — it was simply never handed one. This
module is the missing knob: the Audio panel writes it, aria/main.py
reads it at each microphone open. Every read fails soft to None (the
system default): a broken prefs file must never silence the mic.
Output devices are listed for information only — speech output routes
through the OS default endpoint (comhost/SAPI), and this module does
not pretend otherwise.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from aria import data_dir


def _audio_file() -> str:
    return data_dir.path("audio.json")


def get_input_device():
    """Persisted input-device index, or None for the system default."""
    try:
        with open(_audio_file(), encoding="utf-8") as f:
            value = (json.load(f) or {}).get("input_device")
        return int(value) if value is not None else None
    except Exception:
        return None


def set_input_device(index) -> str:
    """Persist the input-device choice (None = system default)."""
    payload = {}
    try:
        with open(_audio_file(), encoding="utf-8") as f:
            payload = json.load(f) or {}
    except Exception:
        payload = {}
    payload["input_device"] = (int(index) if index is not None else None)
    payload["updated"] = datetime.now(timezone.utc).isoformat()
    data_dir.write_json_atomic(_audio_file(), payload)
    if index is None:
        return "Microphone: system default."
    return f"Microphone: device {index} (applies at the next mic open)."


def _devices(kind: str) -> list:
    """[{index, name, default}] for kind 'input' or 'output'.

    Lazy sounddevice import; any failure (no audio stack, no devices)
    returns an empty list rather than raising into a UI surface.
    """
    try:
        import sounddevice as sd
        defaults = sd.default.device
        default_idx = defaults[0] if kind == "input" else defaults[1]
        channel_key = ("max_input_channels" if kind == "input"
                       else "max_output_channels")
        found = []
        for i, dev in enumerate(sd.query_devices()):
            if int(dev.get(channel_key, 0)) > 0:
                found.append({"index": i, "name": str(dev.get("name", i)),
                              "default": i == default_idx})
        return found
    except Exception:
        return []


def list_input_devices() -> list:
    return _devices("input")


def list_output_devices() -> list:
    return _devices("output")
