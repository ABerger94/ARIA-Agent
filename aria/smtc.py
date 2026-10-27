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


def _sessions(manager):
    try:
        return list(manager.get_sessions() or [])
    except Exception:
        return []


def _spotify_session(manager):
    """The Spotify session specifically — never just "the current
    session", which might be a browser video."""
    for session in _sessions(manager):
        try:
            if "spotify" in (session.source_app_user_model_id or "").lower():
                return session
        except Exception:
            continue
    return None


_APP_LABELS = (
    ("spotify", "Spotify"),
    ("zunevideo", "Movies & TV"),
    ("microsoft.media.player", "Movies & TV"),
    ("wmplayer", "Windows Media Player"),
    ("msedge", "Edge"),
    ("chrome", "Chrome"),
    ("firefox", "Firefox"),
    ("vlc", "VLC"),
    ("netflix", "Netflix"),
    ("plex", "Plex"),
    ("itunes", "iTunes"),
    ("applemusic", "Apple Music"),
    ("tidal", "TIDAL"),
    ("deezer", "Deezer"),
    ("youtube", "YouTube Music"),
)


def app_label(app_id: str) -> str:
    """A human name for a session's source app id."""
    raw = str(app_id or "")
    low = raw.lower()
    for needle, label in _APP_LABELS:
        if needle in low:
            return label
    tail = raw.split("_", 1)[0].split(".")[-1].strip()
    return tail or "Media"


def _session_info(session) -> Optional[dict]:
    try:
        props = session.try_get_media_properties_async().get()
        info = session.get_playback_info()
        status = _STATUS.get(int(info.playback_status), "open")
        track = (props.title or "").strip()
        artist = (props.artist or "").strip()
        album = (props.album_title or "").strip()
        app_id = session.source_app_user_model_id or ""
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
        "app": app_label(app_id),
        "app_id": app_id,
        "is_spotify": "spotify" in str(app_id).lower(),
    }


def _active_session(manager):
    """The session Windows considers current; failing that, a
    playing one; failing that, the first with a title."""
    try:
        current = manager.get_current_session()
    except Exception:
        current = None
    if current is not None:
        return current
    infos = []
    for session in _sessions(manager):
        info = _session_info(session)
        if info:
            infos.append((session, info))
    for session, info in infos:
        if info["playing"]:
            return session
    for session, info in infos:
        if info["track"]:
            return session
    return infos[0][0] if infos else None


def _read_all() -> dict:
    """One manager pass → {'spotify': info|None, 'active': info|None}."""
    manager = _manager()
    if manager is None:
        return {"spotify": None, "active": None}
    spotify = _spotify_session(manager)
    active = _active_session(manager)
    return {
        "spotify": _session_info(spotify) if spotify is not None else None,
        "active": _session_info(active) if active is not None else None,
    }


def _read_now_playing() -> Optional[dict]:
    """One synchronous SMTC read of the Spotify session. None
    when there is no backend or no Spotify session."""
    return _read_all()["spotify"]


def _refresh():
    global _REFRESHING
    try:
        data = _read_all()
        with _LOCK:
            _CACHE["time"] = time.time()
            _CACHE["data"] = data
    finally:
        with _LOCK:
            _REFRESHING = False


def _cached() -> dict:
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
    return data or {"spotify": None, "active": None}


def now_playing() -> Optional[dict]:
    """The cached SPOTIFY now-playing dict, refreshed in the
    background when stale — never blocks the caller."""
    return _cached()["spotify"]


def now_playing_any() -> Optional[dict]:
    """The cached now-playing dict for whatever session is
    active — a movie in Movies & TV / VLC, a browser video,
    Spotify, anything SMTC can see (Alek, 2026-10-27)."""
    return _cached()["active"]


def _control_on(session, action: str) -> bool:
    method = _ACTIONS.get(str(action or "").lower().strip())
    if method is None or session is None:
        return False
    try:
        return bool(getattr(session, method)().get())
    except Exception:
        return False


def control(action: str) -> bool:
    """An explicit transport call on the SPOTIFY session:
    play / pause / play_pause / next / previous / stop. True only
    when SMTC accepted the call. Synchronous — call it from a
    worker thread, never the UI thread."""
    manager = _manager()
    if manager is None:
        return False
    return _control_on(_spotify_session(manager), action)


def control_active(action: str) -> bool:
    """The same explicit call, aimed at the ACTIVE session —
    whatever is actually playing (movie, browser, Spotify)."""
    manager = _manager()
    if manager is None:
        return False
    return _control_on(_active_session(manager), action)
