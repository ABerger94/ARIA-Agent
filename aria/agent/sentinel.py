"""Runaway-loop sentinel for the agent tool loop.

Tracks tool-call signatures in a sliding 10-second window. If the identical
tool signature fires more than _TRIP_COUNT times inside the window, the turn
is paused for operator confirmation instead of spinning forever.

Pure logic (no I/O) so it is unit-testable. The brain loop calls
sentinel.record() before each execute_tool(); on trip it breaks the turn,
raises the HUD alert, and arms a cooldown so an immediate "continue" does
not re-trip on the stale window.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from typing import Any, Dict, Optional

_WINDOW_S = 10.0
_TRIP_COUNT = 5
_COOLDOWN_S = 60.0

_calls: deque = deque()  # (timestamp, signature)
_tripped: Optional[Dict[str, Any]] = None
_cooldown_until: float = 0.0
_enabled: bool = True


def _signature(name: str, args: Any) -> str:
    try:
        canon = json.dumps(args, sort_keys=True, default=str)
    except Exception:
        canon = str(args)
    return hashlib.sha256(f"{name}|{canon}".encode("utf-8")).hexdigest()[:16]


def _prune(now: float) -> None:
    while _calls and now - _calls[0][0] > _WINDOW_S:
        _calls.popleft()


def record(name: str, args: Any, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Record a tool call. Returns trip info dict when the sentinel trips,
    otherwise None. Never raises."""
    global _tripped, _cooldown_until
    try:
        if not _enabled:
            return None
        t = now if now is not None else time.time()
        if t < _cooldown_until:
            return None
        sig = _signature(name, args)
        _prune(t)
        _calls.append((t, sig))
        same = sum(1 for _, s in _calls if s == sig)
        if same > _TRIP_COUNT:
            _tripped = {
                "tool": name,
                "signature": sig,
                "count": same,
                "window_s": _WINDOW_S,
                "at": t,
            }
            _cooldown_until = t + _COOLDOWN_S
            _calls.clear()
            return dict(_tripped)
        return None
    except Exception:
        return None


def reset() -> None:
    """New user turn: clear the window and any trip state."""
    global _tripped
    _calls.clear()
    _tripped = None


def trip_info() -> Optional[Dict[str, Any]]:
    return dict(_tripped) if _tripped else None


def status() -> Dict[str, Any]:
    now = time.time()
    return {
        "enabled": _enabled,
        "tripped": _tripped is not None,
        "cooldown_s": max(0.0, _cooldown_until - now),
        "window_s": _WINDOW_S,
        "trip_count": _TRIP_COUNT,
    }


def set_enabled(on: bool) -> bool:
    global _enabled
    _enabled = bool(on)
    if not _enabled:
        reset()
    return _enabled


def is_enabled() -> bool:
    return _enabled
