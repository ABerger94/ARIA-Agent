"""ARIA Event Bus — Module 5 of ARIA ULTIMATE.

A lightweight, thread-safe pub/sub for ambient events. Producers (scheduler,
price watcher, inbox poller, screen watcher) emit; the heartbeat consumes via
``aria.agent.proactive.process_event_queue`` and speaks through the EXISTING
``proactive_say`` decline-learning/suppression logic.

Hard rules (spec):
- Top-level imports: stdlib + ``aria.config.add_log`` ONLY. No import cycles.
- The bus NEVER breaks a producer: every emit is wrapped in try/except.
- Handlers are best-effort: a failing subscriber is logged, not re-raised.

COORDINATOR HOOKS (one-liners to insert — do NOT hack these into scheduler.py
by hand; keep the wired edits minimal):

1) Inbox poller (scheduler tick hook) — insert into ``scheduler_loop``'s
   per-tick block, alongside the other polling work::

       try:
           from aria.events import poll_inbox
           poll_inbox()
       except Exception:
           pass

2) Price-drop emit — there is NO price-drop detection loop in the repo yet
   (``tool_watch_price`` in builtins only records watches; no checker runs).
   When the coordinator wires the hourly price checker, have it call::

       events.emit_price_drop(label, old_price, new_price, url)

   directly on ``aria.events`` (it wraps ``emit`` in its own try/except).

3) Heartbeat consumer — ``process_event_queue`` is already called from
   ``proactive_heartbeat_loop`` (added by Worker C, section 5b). If the
   coordinator re-homes it, the call is::

       from aria.agent import proactive
       proactive.process_event_queue(speak_fn, is_busy_fn)
"""
from __future__ import annotations

import collections
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from aria.config import add_log

# ---------------------------------------------------------------------------
# Event types (spec §Module 5)
# ---------------------------------------------------------------------------
REMINDER_FIRED = "reminder_fired"
TASK_FIRED = "task_fired"
INBOX_NEW_FILE = "inbox_new_file"
PRICE_DROP = "price_drop"
CALENDAR_SOON = "calendar_soon"
SCREEN_WATCH_TRIGGERED = "screen_watch_triggered"
APPROVAL_REQUESTED = "approval_requested"

EVENT_TYPES = (
    REMINDER_FIRED,
    TASK_FIRED,
    INBOX_NEW_FILE,
    PRICE_DROP,
    CALENDAR_SOON,
    SCREEN_WATCH_TRIGGERED,
    APPROVAL_REQUESTED,
)

# ---------------------------------------------------------------------------
# Bus (thread-safe deque + subscriber registry)
# ---------------------------------------------------------------------------
_bus: "collections.deque" = collections.deque()
_bus_lock = threading.Lock()
_subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}


def emit(event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Queue an event and fan out to subscribers. Never raises."""
    try:
        evt = {
            "type": event_type,
            "payload": dict(payload or {}),
            "ts": time.time(),
        }
        with _bus_lock:
            _bus.append(evt)
            handlers = list(_subscribers.get(event_type, []))
        for fn in handlers:
            try:
                fn(dict(evt["payload"]))
            except Exception as e:  # noqa: BLE001 - best-effort subscriber
                add_log(f"events: subscriber for '{event_type}' failed: {e}")
    except Exception as e:  # noqa: BLE001 - the bus never breaks producers
        add_log(f"events: emit('{event_type}') failed: {e}")


def subscribe(event_type: str, handler_fn: Callable[[Dict[str, Any]], None]) -> None:
    """Register ``handler_fn(payload_dict)`` for ``event_type``."""
    with _bus_lock:
        _subscribers.setdefault(event_type, []).append(handler_fn)


def drain() -> List[Dict[str, Any]]:
    """Pop and return all queued events (oldest first). Thread-safe."""
    with _bus_lock:
        items = list(_bus)
        _bus.clear()
        return items


def queue_size() -> int:
    """Current number of undrained events."""
    with _bus_lock:
        return len(_bus)


# ---------------------------------------------------------------------------
# Producer convenience emitters (each is try/except-safe via emit())
# ---------------------------------------------------------------------------
def emit_reminder_fired(task_id: Any, prompt: str) -> None:
    emit(REMINDER_FIRED, {"task_id": task_id, "prompt": prompt})


def emit_task_fired(task_id: Any, kind: str, prompt: str,
                    interval_s: Optional[int] = None) -> None:
    payload: Dict[str, Any] = {"task_id": task_id, "kind": kind, "prompt": prompt}
    if interval_s is not None:
        payload["interval_s"] = interval_s
    emit(TASK_FIRED, payload)


def emit_price_drop(label: str, old_price: Any, new_price: Any,
                    url: str = "") -> None:
    """Convenience emit for the (coordinator-wired) price checker."""
    emit(PRICE_DROP, {
        "label": label,
        "old_price": old_price,
        "new_price": new_price,
        "url": url,
    })


# ---------------------------------------------------------------------------
# Inbox poller — NO poller exists in aria/inbox.py (it's an upload/mailbox
# module), so the lightweight poller lives here per spec. Drive it from the
# scheduler tick with the one-line hook in the module docstring above.
# ---------------------------------------------------------------------------
_inbox_seen: Dict[str, float] = {}


def poll_inbox() -> List[str]:
    """Scan the inbox dir; emit ``inbox_new_file`` for each newly seen file.

    Idempotent: only files not seen since process start (or since their mtime
    changed) fire. Returns the list of newly-seen filenames (testability).
    """
    try:
        from aria.inbox import INBOX_DIR
    except Exception:
        return []
    try:
        names = os.listdir(INBOX_DIR)
    except OSError:
        return []
    new_files: List[str] = []
    for name in names:
        path = os.path.join(INBOX_DIR, name)
        try:
            if not os.path.isfile(path):
                continue
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if _inbox_seen.get(name) != mtime:
            _inbox_seen[name] = mtime
            new_files.append(name)
            emit(INBOX_NEW_FILE, {"filename": name, "path": path})
    # Forget deleted files so a re-uploaded same-name file fires again.
    for seen in [n for n in _inbox_seen if n not in names]:
        _inbox_seen.pop(seen, None)
    if new_files:
        add_log(f"events: inbox poller saw {len(new_files)} new file(s): "
                f"{', '.join(new_files)}")
    return new_files
