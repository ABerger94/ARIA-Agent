"""Windows SMTC (System Media Transport Controls) — the OS's own
now-playing source, and the fix for two Spotify complaints
(Alek, 2026-10-27): the dashboard said "Spotify Standby" instead
of what was actually playing, and play/pause was one blind media
key that toggled — and could land on the wrong app entirely,
since it went to whichever session Windows felt like, after
stealing focus to the Spotify window.

SMTC gives, per app session: the real title / artist / album,
the real playback status (playing vs paused — the window title
cannot tell those apart), and explicit TryPlay / TryPause /
TrySkip calls aimed at THAT session. No focus steal, no toggle
guessing.

Everything here is optional and defensive: `winsdk` is a
Windows-only wheel (pinned in requirements.txt). Off Windows,
without the wheel, or on any WinRT error, callers get None /
False and fall back to the older window-title + media-key path.
Reads are cached behind a background refresh so the UI thread
never blocks on a WinRT round-trip.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

_TTL = 1.5
_LOCK = threading.Lock()
_MANAGER = None
_MANAGER_FAILED = False
_CACHE = {"time": 0.0, "data": None}
_REFRESHING = False

# GlobalSystemMediaTransportControlsSessionPlaybackStatus
_STATUS = {4: "playing", 5: "paused", 3: "stopped",
           2: "changing", 1: "open", 0: "closed"}

_ACTIONS = {
    "play": "try_play_async",
    "pause": "try_pause_async",
    "play_pause": "try_toggle_play_pause_async",
    "next": "try_skip_next_async",
    "previous": "try_skip_previous_async",
    "stop": "try_stop_async",
}


def _load_manager():
    """The SMTC session manager, or None when WinRT / winsdk is
    unavailable. Imported lazily: this module must import cleanly
    on any platform."""
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager)
    return GlobalSystemMediaTransportControlsSessionManager.request_async().get()


def _manager():
    global _MANAGER, _MANAGER_FAILED
    if _MANAGER is not None or _MANAGER_FAILED:
        return _MANAGER
    try:
        _MANAGER = _load_manager()
    except Exception:
        _MANAGER_FAILED = True
    return _MANAGER


def available() -> bool:
    return _manager() is not None


def _spotify_session(manager):
    """The Spotify session specifically — never just "the current
    session", which might be a browser video."""
    try:
        sessions = list(manager.get_sessions() or [])
    except Exception:
        return None
    for session in sessions:
        try:
            if "spotify" in (session.source_app_user_model_id or "").lower():
                return session
        except Exception:
            continue
    return None


def _read_now_playing() -> Optional[dict]:
    """One synchronous SMTC read. None when there is no backend
    or no Spotify session."""
    manager = _manager()
    if manager is None:
        return None
    session = _spotify_session(manager)
    if session is None:
        return None
    try:
        props = session.try_get_media_properties_async().get()
        info = session.get_playback_info()
        raw_status = info.playback_status
        status = _STATUS.get(int(raw_status), "open")
        track = (props.title or "").strip()
        artist = (props.artist or "").strip()
        album = (props.album_title or "").strip()
    except Exception:
        return None
    title = f"{artist} - {track}" if artist and track else track
    return {
        "running": True,
        "source": "smtc",
        "status": status,
        "playing": status == "playing",
        "title": title,
        "track": track,
        "artist": artist,
        "album": album,
    }


def _refresh():
    global _REFRESHING
    try:
        data = _read_now_playing()
        with _LOCK:
            _CACHE["time"] = time.time()
            _CACHE["data"] = data
    finally:
        with _LOCK:
            _REFRESHING = False


def now_playing() -> Optional[dict]:
    """The cached Spotify now-playing dict, refreshed in the
    background when stale — never blocks the caller."""
    global _REFRESHING
    now = time.time()
    with _LOCK:
        fresh = now - _CACHE["time"] < _TTL
        data = _CACHE["data"]
        start = not fresh and not _REFRESHING
        if start:
            _REFRESHING = True
    if start:
        threading.Thread(target=_refresh, daemon=True).start()
    return data


def control(action: str) -> bool:
    """An explicit transport call on the Spotify session:
    play / pause / play_pause / next / previous / stop. True only
    when SMTC accepted the call. Synchronous — call it from a
    worker thread, never the UI thread."""
    method = _ACTIONS.get(str(action or "").lower().strip())
    if method is None:
        return False
    manager = _manager()
    if manager is None:
        return False
    session = _spotify_session(manager)
    if session is None:
        return False
    try:
        return bool(getattr(session, method)().get())
    except Exception:
        return False
