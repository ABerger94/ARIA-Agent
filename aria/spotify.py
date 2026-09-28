"""
ARIA Spotify & Media Control Subsystem.
Supports voice playback control, playlist search, URI launching,
and intelligent DJ mode with Liked Songs and memory-stored playlist resolution.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
import urllib.parse
from typing import Optional, Tuple

from aria.config import add_log
from aria.memory import DB_LOCK, DB_PATH
from aria.tools.builtins import tool_focus_window


def _tap_ctrl_s() -> bool:
    """Best-effort Spotify shuffle toggle (Ctrl+S); needs Spotify focused."""
    try:
        import ctypes
        u = ctypes.windll.user32
        u.keybd_event(0x11, 0, 0, 0)  # Ctrl down
        u.keybd_event(0x53, 0, 0, 0)  # S down
        u.keybd_event(0x53, 0, 2, 0)  # S up
        u.keybd_event(0x11, 0, 2, 0)  # Ctrl up
        return True
    except Exception:
        return False


def _tap_media_vk(vk: int) -> bool:
    """Simulate Windows media key press via virtual key code."""
    try:
        import ctypes
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        return True
    except Exception:
        return False


def _find_playlist(name: str) -> Tuple[Optional[str], Optional[str]]:
    """Find a saved playlist URI in memory: category='playlist', fuzzy name match."""
    words = [w for w in re.sub(r"[^a-z0-9\s]", "", (name or "").lower()).split()
             if w not in ("my", "playlist", "playlists")]
    if not words:
        return None, None
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM memory WHERE category='playlist'")
        rows = cur.fetchall()
        conn.close()
    for key, value in rows:
        kl = key.lower()
        if all(w in kl for w in words) or all(w in " ".join(words) for w in kl.split()):
            return value.strip(), key
    return None, None


def tool_spotify(action: str = "play_pause", query: str = "") -> str:
    """Spotify voice control: open, play_pause, next, previous, search, play_uri."""
    a = str(action or "play_pause").lower().strip()
    vk_map = {
        "play_pause": 0xB3,
        "play": 0xB3,
        "pause": 0xB3,
        "next": 0xB0,
        "previous": 0xB1,
        "prev": 0xB1,
    }
    if a in vk_map:
        ok = _tap_media_vk(vk_map[a])
        return f"Spotify: sent {a} command." if ok else "[Media key dispatch failed.]"
    if a == "open":
        try:
            os.startfile("spotify:")
            return "Spotify: opening the app."
        except Exception as e:
            return f"[Could not open Spotify: {e}]"
    if a == "search":
        if not query:
            return "[Tell me what to search for.]"
        try:
            os.startfile("spotify:search:" + urllib.parse.quote(str(query)))
            return f"Spotify: searching for '{query}'. Tap a result to play it."
        except Exception as e:
            return f"[Could not open Spotify search: {e}]"
    if a == "play_uri":
        if not query:
            return "[Give me a spotify: URI, e.g. spotify:playlist:xxx.]"
        try:
            os.startfile(str(query))
        except Exception as e:
            return f"[Could not open that URI: {e}]"
        time.sleep(2.5)
        if _tap_media_vk(0xB3):
            return f"Spotify: playing {query}."
        return f"Spotify: opened {query} but the play key didn't respond - press play."
    return "[Unknown Spotify action - open, play_pause, next, previous, search, play_uri.]"


def tool_dj(request: str) -> str:
    """DJ mode: play/shuffle a Spotify playlist by name, mood, or 'liked songs'."""
    q = (request or "").lower()
    shuffle = "shuffle" in q
    uri, label = None, ""
    if "liked songs" in q:
        uri, label = "spotify:collection:tracks", "Liked Songs"
    else:
        name = re.sub(r"\b(play|shuffle|shuffled|some|something|music|me|my|"
                      r"playlist|playlists|on|spotify)\b", "", q)
        uri, label = _find_playlist(name)
    if not uri:
        return (f"[I don't have a playlist saved for '{request}' - tell me the "
                f"Spotify link once and I'll remember it for next time.]")
    try:
        os.startfile(uri)
    except Exception as e:
        return f"[Could not open Spotify: {e}]"
    if shuffle:
        time.sleep(1.5)
        tool_focus_window("Spotify")
        time.sleep(0.5)
        _tap_ctrl_s()
        time.sleep(0.3)
        _tap_media_vk(0xB3)
        return f"Spotify DJ: shuffling {label}."
    time.sleep(2.0)
    _tap_media_vk(0xB3)
    return f"Spotify DJ: playing {label}."
