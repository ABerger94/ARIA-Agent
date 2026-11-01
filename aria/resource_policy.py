"""Measured desktop resource profiles; no guessed resource savings."""
from __future__ import annotations

import os
import threading
import time

PROFILES = ('full', 'balanced', 'minimal')
_START = time.monotonic()
_READY = None
_LOCK = threading.Lock()
_LAST = (0.0, {})
_PROCESS = None


def profile():
    from aria import config
    value = str(config.get_setting('resource_profile', 'balanced')).lower()
    return value if value in PROFILES else 'balanced'


def interval(*, active=False, minimized=False, battery=False):
    value = profile()
    if minimized:
        return {'full': 250, 'balanced': 1000, 'minimal': 1500}[value]
    if active:
        return 50 if value == 'minimal' else 30
    return {'full': 30, 'balanced': 150 if battery else 100, 'minimal': 300}[value]


def optional_enabled(component):
    return not (profile() == 'minimal' and component in {'life', 'face_tracking', 'loopback'})


def mark_ready():
    global _READY
    if _READY is None:
        try:
            import psutil
            _READY = time.time() - psutil.Process().create_time()
        except Exception:
            _READY = time.monotonic() - _START


def metrics():
    """At most one psutil sample per second, never a blocking CPU sample."""
    global _LAST, _PROCESS
    with _LOCK:
        now = time.monotonic()
        if now - _LAST[0] < 1:
            return dict(_LAST[1])
        import psutil
        if _PROCESS is None:
            _PROCESS = psutil.Process(os.getpid())
        battery = psutil.sensors_battery()
        value = {'rss_mb': round(_PROCESS.memory_info().rss / 1024**2, 1),
                 'cpu_percent': _PROCESS.cpu_percent(None),
                 'startup_s': round(_READY, 2) if _READY is not None else None,
                 'battery_percent': battery.percent if battery else None,
                 'on_battery': bool(battery and not battery.power_plugged)}
        _LAST = (now, value)
        return dict(value)
