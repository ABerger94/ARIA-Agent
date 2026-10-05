"""
ARIA Spotify & Media Control Subsystem.
Supports voice playback control, playlist search, URI launching,
keyboard transport controls, and intelligent DJ mode with Liked Songs and memory-stored playlist resolution.
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


CURATED_MOODS = {
    "chill": ("spotify:playlist:37i9dQZF1DWWQRwui0ExPn", "Lofi Beats"),
    "lofi": ("spotify:playlist:37i9dQZF1DWWQRwui0ExPn", "Lofi Beats"),
    "focus": ("spotify:playlist:37i9dQZF1DWZeKCadgRdKQ", "Deep Focus"),
    "ambient": ("spotify:playlist:37i9dQZF1DWZeKCadgRdKQ", "Deep Focus"),
    "study": ("spotify:playlist:37i9dQZF1DWZeKCadgRdKQ", "Deep Focus"),
    "energy": ("spotify:playlist:37i9dQZF1DX76Wlfdnj7AP", "Beast Mode"),
    "workout": ("spotify:playlist:37i9dQZF1DX76Wlfdnj7AP", "Beast Mode"),
    "gym": ("spotify:playlist:37i9dQZF1DX76Wlfdnj7AP", "Beast Mode"),
    "electronic workout": ("spotify:playlist:37i9dQZF1DX76Wlfdnj7AP", "Beast Mode"),
}


def _tap_ctrl_s() -> bool:
    """Best-effort Spotify shuffle toggle (Ctrl+S); needs Spotify focused."""
    try:
        import ctypes
        tool_focus_window("Spotify")
        time.sleep(0.15)
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


VK_MEDIA_MAP = {
    "play_pause": 0xB3,
    "play": 0xB3,
    "pause": 0xB3,
    "resume": 0xB3,
    "stop": 0xB2,
    "next": 0xB0,
    "skip": 0xB0,
    "previous": 0xB1,
    "prev": 0xB1,
    "back": 0xB1,
    "mute": 0xAD,
    "unmute": 0xAD,
    "volume_up": 0xAF,
    "vol_up": 0xAF,
    "volume_down": 0xAE,
    "vol_down": 0xAE,
}


def tool_media_key(key: str = "play_pause") -> str:
    """Simulate Windows media key press (play_pause, next, previous, stop, mute, volume_up, volume_down)."""
    k = str(key or "play_pause").lower().strip()
    if k in VK_MEDIA_MAP:
        ok = _tap_media_vk(VK_MEDIA_MAP[k])
        return f"Media key '{k}' dispatched." if ok else f"[Failed to dispatch media key '{k}'.]"
    return f"[Unknown media key: {k}. Supported: play_pause, next, previous, stop, mute, volume_up, volume_down]"


def tool_spotify(action: str = "play_pause", query: str = "") -> str:
    """Spotify voice control: open, play_pause, next, previous, search, play_uri."""
    a = str(action or "play_pause").lower().strip()
    q = str(query or "").strip()

    # 1. Action is 'open'
    if a == "open":
        try:
            os.startfile("spotify:")
            return "Spotify: opening the app."
        except Exception as e:
            return f"[Could not open Spotify: {e}]"

    # 2. Play with explicit query, or search
    if (a in ("play", "search") and q) or (a == "play_track" and q):
        if q.startswith("spotify:") or q.startswith("http"):
            return tool_spotify(action="play_uri", query=q)

        # Check for saved playlist in memory first
        saved_uri, saved_name = _find_playlist(q)
        if saved_uri:
            return tool_spotify(action="play_uri", query=saved_uri)

        # Check curated mood playlists
        ql = q.lower()
        for m_key, (m_uri, m_label) in CURATED_MOODS.items():
            if m_key in ql:
                return tool_spotify(action="play_uri", query=m_uri)

        # Launch search in Spotify
        try:
            os.startfile("spotify:search:" + urllib.parse.quote(q))
            time.sleep(1.8)
            tool_focus_window("Spotify")
            time.sleep(0.3)
            # Send Play to auto-start if possible
            _tap_media_vk(0xB3)
            return f"Spotify: searching for '{q}' and sending play command."
        except Exception as e:
            return f"[Could not open Spotify search: {e}]"

    # 3. Media key actions (when no query or pure transport actions)
    if a in VK_MEDIA_MAP:
        # Focus Spotify if running before sending media key
        tool_focus_window("Spotify")
        ok = _tap_media_vk(VK_MEDIA_MAP[a])
        return f"Spotify: sent {a} command." if ok else "[Media key dispatch failed.]"

    # 4. Direct Spotify URI playback
    if a == "play_uri":
        if not q:
            return "[Give me a spotify: URI, e.g. spotify:playlist:xxx.]"
        try:
            os.startfile(str(q))
        except Exception as e:
            return f"[Could not open that URI: {e}]"
        time.sleep(2.0)
        tool_focus_window("Spotify")
        time.sleep(0.4)
        if _tap_media_vk(0xB3):
            return f"Spotify: playing {q}."
        return f"Spotify: opened {q} but the play key didn't respond - press play."

    return "[Unknown Spotify action - open, play, pause, next, previous, search, play_uri.]"


def tool_dj(request: str) -> str:
    """DJ mode: play/shuffle a Spotify playlist by name, mood, or 'liked songs'."""
    q = (request or "").lower()
    shuffle = "shuffle" in q
    uri, label = None, ""
    if "liked songs" in q:
        uri, label = "spotify:collection:tracks", "Liked Songs"
    else:
        name = re.sub(r"\b(play|shuffle|shuffled|some|something|music|me|my|"
                      r"playlist|playlists|on|spotify)\b", "", q).strip()
        uri, label = _find_playlist(name)

    if not uri:
        # Check curated mood playlists
        for m_key, (m_uri, m_label) in CURATED_MOODS.items():
            if m_key in q:
                uri, label = m_uri, m_label
                break

    if not uri:
        clean_name = re.sub(r"\b(play|shuffle|shuffled|some|something|music|me|my|"
                            r"playlist|playlists|on|spotify)\b", "", q).strip()
        if clean_name:
            try:
                os.startfile("spotify:search:" + urllib.parse.quote(clean_name))
                time.sleep(1.8)
                tool_focus_window("Spotify")
                time.sleep(0.3)
                _tap_media_vk(0xB3)
                return f"Spotify DJ: searching '{clean_name}' and playing."
            except Exception as e:
                return f"[Could not open Spotify search: {e}]"
        return (f"[I don't have a playlist saved for '{request}' - tell me the "
                f"Spotify link once and I'll remember it for next time.]")

    try:
        os.startfile(uri)
    except Exception as e:
        return f"[Could not open Spotify: {e}]"

    if shuffle:
        time.sleep(1.8)
        tool_focus_window("Spotify")
        time.sleep(0.4)
        _tap_ctrl_s()
        time.sleep(0.3)
        _tap_media_vk(0xB3)
        return f"Spotify DJ: shuffling {label}."
    time.sleep(2.0)
    tool_focus_window("Spotify")
    time.sleep(0.3)
    _tap_media_vk(0xB3)
    return f"Spotify DJ: playing {label}."
