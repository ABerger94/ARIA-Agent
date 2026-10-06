"""
ARIA Instant Voice Command Shortcuts.
Fast-path recognition for fixed commands that execute without LLM round-trips.
Includes instant media playback controls, master volume, app launches,
Spotify fast queries, and real-time system status.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Tuple

import psutil

from aria.spotify import tool_media_key, tool_spotify
from aria.tools.builtins import tool_open_app_or_url, tool_volume


def normalize_shortcut(text: str) -> str:
    """Normalize apostrophes, punctuation, and casing for shortcut matching."""
    t = (text or "").lower().replace("’", "").replace("'", "").strip()
    return re.sub(r"[^a-z0-9\s]", "", t).strip()


def check_voice_shortcut(user_prompt: str) -> Optional[Tuple[str, str]]:
    """Return (result_text, spoken_ack) if prompt matches a fast-path shortcut; else None."""
    p = (user_prompt or "").strip()
    if not p:
        return None
    norm = normalize_shortcut(p)

    # 1. Exact media commands (Windows native virtual key)
    if norm in ("pause", "pause music", "stop music", "pause playback", "pause song"):
        tool_media_key("pause")
        return "Executed media key: pause", "Paused."
    if norm in ("play", "play music", "resume", "resume music", "continue music"):
        tool_media_key("play")
        return "Executed media key: play", "Resuming."
    if norm in ("next", "next song", "next track", "skip", "skip song", "skip track"):
        tool_media_key("next")
        return "Executed media key: next", "Skipping track."
    if norm in ("previous", "previous song", "previous track", "last song", "go back"):
        tool_media_key("previous")
        return "Executed media key: previous", "Previous track."
    if norm in ("mute", "unmute", "mute audio", "unmute audio"):
        tool_media_key("mute")
        return "Executed media key: mute", "Audio mute toggled."

    # 2. Volume controls
    if norm in ("volume up", "turn it up", "louder"):
        res = tool_volume("up", 10)
        return res, "Volume up."
    if norm in ("volume down", "turn it down", "quieter", "softer"):
        res = tool_volume("down", 10)
        return res, "Volume down."
    vm = re.match(r"^(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})%?$", norm)
    if vm:
        lvl = max(0, min(100, int(vm.group(1))))
        res = tool_volume("set", lvl)
        return res, f"Volume set to {lvl} percent."

    # 3. Direct application launches
    if norm in ("open spotify", "launch spotify", "start spotify"):
        tool_spotify("open")
        return "Executed fast-path: Spotify", "Opening Spotify."
    if norm in ("open video downloader", "launch video downloader", "start video downloader"):
        tool_open_app_or_url("video-downloader")
        return "Executed fast-path: video-downloader", "Opening video downloader."
    if norm in ("lets play some magic", "let us play some magic", "open convoke", "convoke lobby", "play convoke"):
        tool_open_app_or_url("https://convoke.games/en/lobby")
        return "Executed fast-path: convoke", "Opening Convoke lobby. Have a good game."

    # 4. Spotify explicit queries / searches
    sp_m = re.match(r"^(?:open\s+spotify\s+and\s+play\s+(.+)|play\s+(.+?)\s+on\s+spotify|search\s+spotify\s+for\s+(.+))$", p, re.IGNORECASE)
    if sp_m:
        q = next((g for g in sp_m.groups() if g), "").strip()
        if q and q.lower() not in ("music", "song", "audio"):
            res = tool_spotify("play", query=q)
            return res, f"Playing {q} on Spotify."

    # 5. Instant time & battery queries
    if norm in ("what time is it", "what is the time", "current time", "tell me the time", "time check"):
        t_str = datetime.now().strftime("%I:%M %p").lstrip("0")
        return f"Current time: {t_str}", f"It's {t_str}."
    if norm in ("battery level", "battery status", "what is the battery", "battery percentage", "check battery"):
        b = psutil.sensors_battery()
        if b:
            state = "charging" if b.power_plugged else "on battery"
            return f"Battery: {b.percent}%, {state}", f"Battery is at {b.percent} percent, {state}."
        return "Battery: unknown", "Battery status is unavailable."

    return None
