"""
ARIA reminder gate — presence-waiting reminders (pass 6, Alek's pick).

THE GAP
    Reminders were purely timed: a "once" reminder spoke at its
    moment whether Alek was at the laptop or not, and there was no
    way to say "remind me when I'm back." Presence awareness only
    decided when ARIA slept and greeted.

WHAT THIS ADDS
    - A second trigger for reminders: ON RETURN. Stored in the same
      scheduled_tasks table (kind='on_return', sentinel next_run),
      delivered on the first eligible tick after a genuine
      away->present transition that happened after the reminder was
      created.
    - Quiet hours (23:00-07:00, the presence monitor's own hours)
      defer TIMED "once" reminders that fall due overnight —
      deferred, never dropped, until the first eligible tick. They
      do NOT hold on-return reminders: a return is Alek acting, not
      ARIA initiating, and the reminder exists because he asked for
      it on exactly that event. (Corrected 2026-10-29 after his
      23:49 witness: the original gate held his on-return reminder
      for 07:00 while he stood at the unlocked laptop.)
    - Delivery = a brief spoken line plus a card file in the shared
      inbox (the folder both the dashboard Inbox tab and the phone
      inbox page render). Acknowledgment — the
      acknowledge_reminders tool, or deleting the card file —
      removes the card from both views at once, because there is
      only one folder.

STATE
    A small JSON store (~/ARIA/reminder_gate.json) holds the return
    watcher's state (so a restart keeps the last observed return)
    and the delivered-but-unacknowledged cards (so ack survives
    restarts too). All seams (clock, presence, speech, card files)
    are injected — the scheduler wires the real ones, tests use
    fakes. This module imports nothing from the package.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from typing import Callable, List, Optional

QUIET_HOURS = (23, 7)


def in_quiet_hours(moment: datetime, quiet=QUIET_HOURS) -> bool:
    """Mirror of presence._in_quiet_hours' wrap-around semantics."""
    start, end = int(quiet[0]), int(quiet[1])
    h = moment.hour
    if start <= end:
        return start <= h < end
    return h >= start or h < end


def timed_eligible(moment: datetime) -> bool:
    """A timed reminder may fire whenever it is not quiet hours."""
    return not in_quiet_hours(moment)


class ReturnWatcher:
    """Folds a presence level signal into return edges.

    The FIRST observation only sets the baseline — a process that
    starts while Alek is present has seen no return. After that, an
    away->present transition records last_return_at and reports an
    edge exactly once; repeated present observations never re-fire.
    """

    def __init__(self, state: Optional[dict] = None):
        state = state or {}
        self.known = bool(state.get("known", False))
        self.present = bool(state.get("present", False))
        raw = state.get("last_return_at")
        self.last_return_at: Optional[datetime] = None
        if raw:
            try:
                self.last_return_at = datetime.fromisoformat(raw)
            except ValueError:
                self.last_return_at = None

    def observe(self, present: bool, now: datetime) -> bool:
        """Feed one presence reading; True exactly on a return edge."""
        if not self.known:
            self.known = True
            self.present = bool(present)
            return False
        edge = bool(present) and not self.present
        self.present = bool(present)
        if edge:
            self.last_return_at = now
        return edge

    def due(self, created: datetime) -> bool:
        """Has a return happened at or after `created`?"""
        return (self.last_return_at is not None
                and self.last_return_at >= created)

    def to_state(self) -> dict:
        return {
            "known": self.known,
            "present": self.present,
            "last_return_at": (self.last_return_at.isoformat(timespec="seconds")
                                if self.last_return_at else None),
        }


class GateStore:
    """JSON persistence for watcher state + delivered cards."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {}

    def save(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path),
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def tick(*, now: datetime, present: bool, store: GateStore,
         rows: List[tuple], is_busy: bool,
         speak: Callable[[str], None],
         queue_speech: Callable[[str], None],
         delete_row: Callable[[int], None],
         write_card: Callable[[int, str], str],
         card_exists: Callable[[str], bool]) -> List[int]:
    """One scheduler tick for on-return reminders.

    rows: (tid, message, created_datetime) for kind='on_return'.
    Delivers every row whose return has happened, on the first
    tick where Alek is present — at any hour (see module docstring:
    quiet hours gate timed reminders, never on-return ones).
    Returns delivered tids. Also reconciles delivered cards
    deleted externally (acked)."""
    data = store.load()
    watcher = ReturnWatcher(data.get("watcher"))
    watcher.observe(present, now)

    delivered = [d for d in data.get("delivered", [])
                 if card_exists(d.get("file", ""))]

    done: List[int] = []
    if present:
        for tid, message, created in rows:
            if not watcher.due(created):
                continue
            delete_row(tid)
            text = f"Reminder from earlier: {message}"
            if is_busy:
                queue_speech(text)
            else:
                speak(text)
            fname = write_card(tid, message)
            delivered.append({
                "tid": tid, "file": fname, "message": message,
                "delivered_at": now.isoformat(timespec="seconds"),
            })
            done.append(tid)

    data["watcher"] = watcher.to_state()
    data["delivered"] = delivered
    store.save(data)
    return done


def acknowledge_all(*, store: GateStore,
                    delete_card: Callable[[str], None]) -> int:
    """Clear every delivered card (the acknowledge_reminders tool).
    Returns how many cards were cleared."""
    data = store.load()
    delivered = data.get("delivered", [])
    for d in delivered:
        try:
            delete_card(d.get("file", ""))
        except Exception:
            pass
    data["delivered"] = []
    store.save(data)
    return len(delivered)


def pending_cards(store: GateStore) -> List[dict]:
    return list(store.load().get("delivered", []))
