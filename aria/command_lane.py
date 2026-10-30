"""
ARIA local command lane (ChatGPT loop pass 6, 2026-10-29).

THE GAP
    A handful of media commands dominate daily voice use — pause,
    resume, next track, volume up/down. Sending each one through a
    full model round trip makes the most predictable interactions
    the slowest, and ties basic playback control to provider
    availability even though everything needed is already local.

THE LANE
    A deterministic matcher for a small set of COMPLETE phrases.
    Text is normalized (case, whitespace, trailing punctuation, one
    leading "please") and must equal a known phrase exactly —
    sentences merely containing a phrase never match and fall
    through to the brain untouched. Matches execute through the
    same seams the model's tools use (media_control.control for
    transport, tool_volume for relative volume), so routing and
    behaviour stay identical to the tool path; nothing here is
    gated because the underlying media tools are not gated either.

    Scope is deliberately tiny: transport + relative volume only.
    Anything ambiguous belongs to the brain.

This module imports nothing from the package at module level; the
real seams are imported lazily inside try_handle so tests can
inject fakes.
"""

from __future__ import annotations

import re
from typing import Callable, Optional, Tuple

# phrase -> (kind, action)
#   kind "media":  media_control.control(action) — auto routing
#   kind "volume": tool_volume(action) — relative, clamped by the tool
_PHRASES = {
    "pause music": ("media", "pause"),
    "resume music": ("media", "play"),
    "play music": ("media", "play"),
    "next track": ("media", "next"),
    "previous track": ("media", "prev"),
    "volume up": ("volume", "up"),
    "volume down": ("volume", "down"),
}

_WS = re.compile(r"\s+")
_TRAILING = ".!?,;:"


def normalize(text: str) -> str:
    """Lowercase, collapse whitespace, drop trailing punctuation
    and one leading 'please' — the shape a spoken phrase arrives in."""
    t = _WS.sub(" ", (text or "").strip().lower())
    t = t.rstrip(_TRAILING).strip()
    if t.startswith("please "):
        t = t[len("please "):].lstrip().rstrip(_TRAILING).strip()
    return t


def match(text: str) -> Optional[Tuple[str, str]]:
    """Return (kind, action) when the WHOLE normalized utterance is
    a known command phrase, else None."""
    return _PHRASES.get(normalize(text))


def try_handle(text: str, *,
               control_fn: Optional[Callable[..., str]] = None,
               volume_fn: Optional[Callable[..., str]] = None
               ) -> Optional[str]:
    """Execute a lane command and return its acknowledgement, or
    None when the text is not a lane command (the caller then runs
    the normal brain path)."""
    m = match(text)
    if m is None:
        return None
    kind, action = m
    if kind == "media":
        if control_fn is None:
            from aria.media_control import control as control_fn
        return control_fn(action)
    if volume_fn is None:
        from aria.tools.builtins import tool_volume as volume_fn
    return volume_fn(action)
