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
    except Exception as exc:
        _MANAGER_FAILED = True
        # Say it ONCE, where Alek can see it (the Activity tab):
        # a silently-dead backend is how "nothing playing" shipped
        # while Spotify played. The window fallback keeps working.
        try:
            from aria import config as _config
            _config.add_log(
                f"Media sessions (SMTC) unavailable ({exc.__class__.__name__}) "
                f"— media readout is on the window fallback. If winsdk "
                f"was meant to be installed, the install did not take.",
                level="warn")
        except Exception:
            pass
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
    ("microsoft.media.player", "Media Player"),
    ("mediaplayer", "Media Player"),
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


# The audio-session process behind an SMTC app id, for the volume
# backend's per-app slider (aria/comhost.py matches pycaw sessions
# by process name). Best-effort: an app id that literally ends in
# .exe names itself; packaged players map by hand below.
_APP_PROCESSES = (
    ("spotify", "spotify.exe"),
    ("zunevideo", "Video.UI.exe"),
    ("microsoft.media.player", "MediaPlayer.exe"),
    ("mediaplayer", "MediaPlayer.exe"),
    ("wmplayer", "wmplayer.exe"),
    ("video.ui", "Video.UI.exe"),
    ("vlc", "vlc.exe"),
    ("msedge", "msedge.exe"),
    ("chrome", "chrome.exe"),
    ("firefox", "firefox.exe"),
    ("itunes", "iTunes.exe"),
    ("plex", "Plex.exe"),
    ("tidal", "TIDAL.exe"),
    ("deezer", "Deezer.exe"),
)


def app_process(app_id: str) -> str:
    """The likely .exe behind an SMTC source app id, or "" when
    unknown (the volume backend then keeps its Spotify default)."""
    raw = str(app_id or "").strip()
    low = raw.lower()
    for needle, exe in _APP_PROCESSES:
        if needle in low:
            return exe
    tail = raw.replace("\\", "/").rsplit("/", 1)[-1]
    if tail.lower().endswith(".exe"):
        return tail
    return ""


# ---------------- album art ----------------
# SMTC media properties carry a thumbnail stream reference. The
# bytes are fetched on the background refresh thread (never the
# UI thread) and cached by (app, track, artist, album): a track
# change refetches, a repeat read is free. Failures back off —
# some apps hand out a thumbnail reference that never opens.
_ART_CACHE: dict = {}
_ART_FAIL: dict = {}
_ART_RETRY_S = 30.0
_ART_MAX_BYTES = 2_000_000


def _fetch_art(ref) -> Optional[bytes]:
    """Open an SMTC thumbnail reference and read its bytes.
    Windows/WinRT only; every failure is a quiet None."""
    try:
        stream = ref.open_read_async().get()
        size = int(stream.size)
        if size <= 0 or size > _ART_MAX_BYTES:
            return None
        from winsdk.windows.storage.streams import (
            Buffer, DataReader, InputStreamOptions)
        buf = Buffer(size)
        try:
            options = InputStreamOptions.NONE
        except Exception:
            options = InputStreamOptions.PARTIAL
        stream.read_async(buf, size, options).get()
        reader = DataReader.from_buffer(buf)
        n = int(reader.unconsumed_buffer_length)
        if n <= 0:
            return None
        data = reader.read_bytes(n)
        if not data:
            data = bytearray(n)
            reader.read_bytes(data)
        return bytes(data) or None
    except Exception:
        return None


def _art_for(props, key) -> Optional[bytes]:
    if key in _ART_CACHE:
        return _ART_CACHE[key]
    ref = getattr(props, "thumbnail", None)
    if ref is None:
        return None
    now = time.monotonic()
    if now - _ART_FAIL.get(key, -_ART_RETRY_S - 1) < _ART_RETRY_S:
        return None
    data = _fetch_art(ref)
    if data:
        _ART_CACHE[key] = data
        if len(_ART_CACHE) > 24:
            _ART_CACHE.pop(next(iter(_ART_CACHE)))
        return data
    _ART_FAIL[key] = now
    return None


def _session_info(session) -> Optional[dict]:
    try:
        info = session.get_playback_info()
        status = _STATUS.get(int(info.playback_status), "open")
        app_id = session.source_app_user_model_id or ""
    except Exception:
        return None
    props = None
    try:
        props = session.try_get_media_properties_async().get()
        track = (props.title or "").strip()
        artist = (props.artist or "").strip()
        album = (props.album_title or "").strip()
    except Exception:
        # Some apps' properties call fails while their playback
        # info is fine — a session with a state but no title
        # still beats no session at all.
        track = artist = album = ""
    title = f"{artist} - {track}" if artist and track else track
    art = art_key = None
    if props is not None and track:
        art_key = (str(app_id), track, artist, album)
        art = _art_for(props, art_key)
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
        "art": art,
        "art_key": art_key,
        "is_spotify": "spotify" in str(app_id).lower(),
    }


def _active_session(manager):
    """The session that matters: a PLAYING one first — Windows'
    "current" session can be a paused Spotify sitting in front
    of a movie that is actually playing, and aiming transport
    at the paused one is how movie controls "didn't work"
    (Alek, 2026-10-27). Then the current session, then the
    first with a title."""
    infos = []
    for session in _sessions(manager):
        info = _session_info(session)
        if info:
            infos.append((session, info))
    for session, info in infos:
        if info["playing"]:
            return session
    try:
        current = manager.get_current_session()
    except Exception:
        current = None
    if current is not None:
        return current
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


def refresh_now() -> dict:
    """A synchronous fresh read of every session, cache updated.
    For closed-loop control verification — the cached read can
    be a second stale, which is an eternity after pressing a
    button."""
    data = _read_all()
    with _LOCK:
        _CACHE["time"] = time.time()
        _CACHE["data"] = data
    return data


_SETTLE_S = 0.35


def set_playing(want: bool, *, spotify_only: bool = False) -> Optional[bool]:
    """Closed-loop play/pause (Alek, 2026-10-27: the buttons
    didn't work while prev/next did — some sessions, Spotify's
    notoriously, accept SMTC transport calls without acting).
    Aim the explicit call, RE-READ the session, and if the
    state didn't flip, escalate to the global media-key toggle.
    Returns the session's final playing state, or None when
    there is no session to aim at."""
    pick = "spotify" if spotify_only else "active"
    state = refresh_now()[pick]
    if state is None:
        return None
    if bool(state["playing"]) == want:
        return want
    if spotify_only:
        control("play" if want else "pause")
    else:
        control_active("play" if want else "pause")
    time.sleep(_SETTLE_S)
    state = refresh_now()[pick]
    if state is not None and bool(state["playing"]) == want:
        return want
    # Escalation: the session ignored the explicit call — the
    # global toggle is dumb but it is what the hardware keys do.
    try:
        from aria.tools.builtins import tool_media_key
        tool_media_key("play_pause")
    except Exception:
        return state["playing"] if state else None
    time.sleep(_SETTLE_S)
    state = refresh_now()[pick]
    return bool(state["playing"]) if state else None


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
