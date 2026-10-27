"""
ARIA unified media control (Gap fill 6).

THE GAP
    Playback control was three disconnected surfaces: the
    `volume` tool (HUD master volume), the `media_key` tool
    (Windows media keys for OTHER apps), and the built-in video
    panel (Port 3) — which no tool could drive at all, so "skip
    to halfway" on a video ARIA herself was showing had no path.
    Mark-LVI has one media_control tool; this is that shape here.

ONE TOOL, ROUTED BY TARGET
    media_control(action, target='auto', value=''):
      actions: play, pause, stop, next, prev,
               seek_to (value = seconds, or 'M:SS'),
               seek_percent (value = 0-100),
               seek_by (value = signed seconds, '+30'/'-15'),
               volume (value = 0-100), mute, unmute, status.
      targets: 'hud' (the built-in video player), 'system'
               (media keys + master volume), 'auto' (default).

    Auto routing: when the HUD player has media loaded it owns
    transport and seek (it is the thing actually playing);
    volume goes to the HUD player while it is loaded, else to
    the master volume; next/prev are always system keys (the
    HUD player has no playlist). With nothing loaded, transport
    falls back to system media keys — Spotify and friends.

THREADING
    Tools run on agent threads; Qt playback lives on the GUI
    thread. The dashboard registers its VideoPanel here; commands
    reach it by emitting its `command` Qt signal (queued delivery
    to the GUI thread), and status reads its plain-data
    snapshot(). This module itself imports no Qt.

HONEST REFUSALS
    A seek with nothing loaded, or any HUD action with no panel
    registered, says so plainly instead of pretending — the
    seek honesty the video panel was built for, kept end-to-end.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

_PANEL: Any = None
_LOCK = threading.Lock()

ACTIONS = ("play", "pause", "stop", "next", "prev", "seek_to",
           "seek_percent", "seek_by", "volume", "mute", "unmute",
           "status")


def register_panel(panel) -> None:
    """Called by the dashboard when its video panel exists."""
    global _PANEL
    with _LOCK:
        _PANEL = panel


def unregister_panel() -> None:
    global _PANEL
    with _LOCK:
        _PANEL = None


def _panel():
    with _LOCK:
        return _PANEL


# ------------------------------------------------------- system seams
def _system_key(action: str) -> str:
    from aria.tools.builtins import tool_media_key
    return tool_media_key(action)


def _system_volume(action: str, level: int = 50) -> str:
    from aria.tools.builtins import tool_volume
    return tool_volume(action, level)


def _emit(panel, action: str, payload=None) -> None:
    """Deliver a command to the panel on its own thread."""
    sig = getattr(panel, "command", None)
    if sig is not None:
        sig.emit(action, payload)
    else:  # test doubles / direct objects
        panel.apply_command(action, payload)


def _parse_seconds(value: str) -> Optional[float]:
    """'90' -> 90.0; '1:30' -> 90.0; '+30'/'-15' keep sign."""
    v = (value or "").strip()
    if not v:
        return None
    sign = -1.0 if v.startswith("-") else 1.0
    v = v.lstrip("+-")
    try:
        if ":" in v:
            parts = [float(p) for p in v.split(":")]
            total = 0.0
            for p in parts:
                total = total * 60 + p
            return sign * total
        return sign * float(v)
    except ValueError:
        return None


def _fmt_ms(ms: int) -> str:
    s = max(0, int(ms) // 1000)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _fmt(snap: dict) -> str:
    state = "playing" if snap.get("playing") else "paused"
    return (f"{snap.get('title') or 'video'} — {state}, "
            f"{_fmt_ms(snap.get('position_ms', 0))} / "
            f"{_fmt_ms(snap.get('duration_ms', 0))}"
            + (" (muted)" if snap.get("muted") else ""))


def control(action: str, target: str = "auto", value: str = "", *,
            panel=None, system_key: Optional[Callable] = None,
            system_volume: Optional[Callable] = None) -> str:
    """Run one unified media command and describe the outcome."""
    a = (action or "").strip().lower()
    t = (target or "auto").strip().lower()
    if a not in ACTIONS:
        return (f"[Media control: unknown action '{action}'. "
                f"Known: {', '.join(ACTIONS)}.]")
    key_fn = system_key or _system_key
    vol_fn = system_volume or _system_volume
    p = panel if panel is not None else _panel()
    snap = p.snapshot() if p is not None else None
    hud_loaded = bool(snap and snap.get("loaded"))
    use_hud = (t == "hud") or (t == "auto" and hud_loaded)

    if a == "status":
        if p is not None and t != "system":
            if hud_loaded:
                return f"Built-in player: {_fmt(snap)}."
            return "Built-in player: nothing loaded."
        return "No built-in player is registered."

    if a in ("next", "prev"):
        return key_fn(a)

    if a in ("play", "pause", "stop"):
        if use_hud:
            if p is None:
                return "[Media control: no built-in player is available.]"
            _emit(p, {"play": "play", "pause": "pause",
                      "stop": "stop"}[a])
            return f"Built-in player: {a} sent."
        return key_fn("play_pause" if a in ("play", "pause") else "pause")

    if a in ("seek_to", "seek_percent", "seek_by"):
        if p is None or not hud_loaded:
            return ("[Media control: nothing is loaded in the "
                    "built-in player, so there is nothing to seek.]")
        if a == "seek_percent":
            try:
                pct = float(str(value).rstrip("%"))
            except ValueError:
                return f"[Media control: '{value}' is not a percentage.]"
            _emit(p, "seek_percent", pct)
            return f"Seeking to {pct:g}% of {snap.get('title') or 'the video'}."
        secs = _parse_seconds(str(value))
        if secs is None:
            return f"[Media control: '{value}' is not a time.]"
        if a == "seek_to":
            _emit(p, "seek_ms", int(secs * 1000))
            return f"Seeking to {int(secs)}s in {snap.get('title') or 'the video'}."
        _emit(p, "seek_by", int(secs * 1000))
        return f"Seeking {'forward' if secs >= 0 else 'back'} {abs(int(secs))}s."

    if a in ("volume", "mute", "unmute"):
        if use_hud and p is not None:
            if a == "volume":
                try:
                    level = int(float(str(value)))
                except ValueError:
                    return f"[Media control: '{value}' is not a volume level.]"
                _emit(p, "set_volume", max(0, min(100, level)))
                return f"Built-in player volume set to {max(0, min(100, level))}%."
            _emit(p, a)
            return f"Built-in player: {a}d."
        if a == "volume":
            try:
                level = int(float(str(value)))
            except ValueError:
                return f"[Media control: '{value}' is not a volume level.]"
            return vol_fn("set", max(0, min(100, level)))
        return vol_fn(a)

    return f"[Media control: action '{a}' is not routable.]"
