"""When the media visualizer owns the dashboard centerpiece.

Pure state machine, no Qt (Alek, 2026-10-29): the visualizer
pops up on its own when music starts playing, steps aside the
moment Alek talks or types to ARIA, and comes back when the
interaction ends — unless he closed it himself with its ✕, which
wins for the rest of that playback session. A session ends when
the player stops entirely (closed / nothing running); pausing
and resuming is the same session, so a manual close survives a
pause. The next session starts fresh.
"""
from __future__ import annotations


class VizPolicy:
    def __init__(self):
        self._session = False
        self._dismissed = False

    def manual_close(self):
        """Alek closed the visualizer: keep it away this session."""
        self._dismissed = True

    @property
    def dismissed(self):
        return self._dismissed

    def update(self, *, playing, session, interacting):
        """Feed one observation; return True when the visualizer
        should be the visible centerpiece right now."""
        session = bool(session)
        if session and not self._session:
            self._dismissed = False      # a fresh session re-arms it
        if not session:
            self._dismissed = False      # nothing playing: reset
        self._session = session
        return bool(playing) and not self._dismissed \
            and not interacting
