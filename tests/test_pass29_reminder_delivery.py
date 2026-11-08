"""Pass 29 #34: reminders are deleted only after durable delivery succeeds.

Bug: the timed path deleted the scheduled_tasks row BEFORE speaking,
and the on-return gate deleted BEFORE speaking and writing the card —
while speech.speak() returned None silently when muted or locked. A
muted/locked refusal or a card-write failure lost the reminder with
no record.

Fix contract:
- on-return gate: card write -> speech -> delete only when both
  succeeded; a card-write failure skips the row this tick; a refused
  speech keeps the row (the written card is the durable record) for
  a retry, and the delivered-card list never duplicates the tid;
- timed path: speak/queue first, sched_cancel only on acceptance;
  a refused delivery keeps the row due (the row itself is the
  durable outbox record, keyed by its stable tid) for the next tick;
- a delivery callable that reports nothing (None) still counts as
  accepted, so legacy callables keep working; only an explicit
  False refuses.
"""
import os
import sys
import types
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")


def _pkg(name, path):
    m = types.ModuleType(name)
    m.__path__ = [path]
    sys.modules[name] = m
    return m


_pkg("aria", PKG)
_pkg("aria.tools", os.path.join(PKG, "tools"))
_pkg("aria.agent", os.path.join(PKG, "agent"))
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
_speech = types.ModuleType("aria.speech")
_speech.speak = lambda text, whisper_mode=False: True
_speech.enqueue_speech = lambda text: True
sys.modules["aria.speech"] = _speech

import pytest  # noqa: E402

from aria import events  # noqa: E402
from aria import reminder_gate as gate  # noqa: E402
from aria import scheduler  # noqa: E402


def dt(day, h, m=0):
    return datetime(2026, 10, day, h, m)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(scheduler, "add_log", lambda *a, **k: None)
    events.drain()
    yield
    events.drain()


# ------------------------------------------------------------- gate harness

class Harness:
    def __init__(self, tmp_path):
        self.store = gate.GateStore(str(tmp_path / "gate.json"))
        self.rows = []
        self.spoken = []
        self.queued = []
        self.cards = {}
        self.deleted = []
        self.speak_result = None  # None = legacy silent callable
        self.queue_result = None
        self.card_fail = False

    def add(self, tid, message, created):
        self.rows.append((tid, message, created))

    def _speak(self, text):
        self.spoken.append(text)
        return self.speak_result

    def _queue(self, text):
        self.queued.append(text)
        return self.queue_result

    def _delete(self, tid):
        self.deleted.append(tid)
        self.rows = [r for r in self.rows if r[0] != tid]

    def _write_card(self, tid, message):
        if self.card_fail:
            raise OSError("disk full (test)")
        name = f"reminder-{tid}.txt"
        self.cards[name] = message
        return name

    def tick(self, now, present, busy=False):
        return gate.tick(
            now=now, present=present, store=self.store, rows=list(self.rows),
            is_busy=busy, speak=self._speak, queue_speech=self._queue,
            delete_row=self._delete, write_card=self._write_card,
            card_exists=lambda f: f in self.cards)


def _return_sequence(h):
    h.tick(dt(29, 10, 2), present=True)    # baseline
    h.tick(dt(29, 10, 30), present=False)  # away


def test_card_write_failure_leaves_reminder(tmp_path):
    h = Harness(tmp_path)
    h.add(1, "call the dentist", dt(29, 10, 1))
    _return_sequence(h)
    h.card_fail = True
    done = h.tick(dt(29, 10, 50), present=True)
    assert done == []
    assert h.deleted == []
    assert h.rows == [(1, "call the dentist", dt(29, 10, 1))]
    assert h.spoken == []  # nothing spoken: no durable record exists
    assert gate.pending_cards(h.store) == []
    # The write heals: the next tick delivers and deletes exactly once.
    h.card_fail = False
    done = h.tick(dt(29, 10, 55), present=True)
    assert done == [1]
    assert h.deleted == [1]
    assert h.rows == []
    assert len(h.spoken) == 1


def test_muted_delivery_leaves_reminder_but_keeps_card(tmp_path):
    h = Harness(tmp_path)
    h.add(1, "take your meds", dt(29, 10, 1))
    _return_sequence(h)
    h.speak_result = False  # muted / locked: speech refused
    done = h.tick(dt(29, 10, 50), present=True)
    assert done == []
    assert h.deleted == []
    assert len(h.rows) == 1  # reminder present for a retry
    # The card IS the durable outbox record: written and listed.
    assert h.cards == {"reminder-1.txt": "take your meds"}
    assert len(gate.pending_cards(h.store)) == 1
    # Unmute: the retry speaks and deletes exactly once, no dup card.
    # (Two speak attempts total: the refused one + the accepted retry.)
    h.speak_result = True
    done = h.tick(dt(29, 10, 55), present=True)
    assert done == [1]
    assert h.deleted == [1]
    assert len(h.spoken) == 2
    assert len(gate.pending_cards(h.store)) == 1


def test_busy_path_refusal_leaves_reminder(tmp_path):
    h = Harness(tmp_path)
    h.add(1, "stretch", dt(29, 10, 1))
    _return_sequence(h)
    h.queue_result = False  # busy-path enqueue refused
    done = h.tick(dt(29, 10, 50), present=True, busy=True)
    assert done == [] and h.deleted == [] and len(h.rows) == 1
    assert h.queued == ["Reminder from earlier: stretch"]
    h.queue_result = True
    assert h.tick(dt(29, 10, 55), present=True, busy=True) == [1]
    assert h.deleted == [1]


def test_successful_delivery_deletes_exactly_once(tmp_path):
    h = Harness(tmp_path)
    h.add(1, "water the plants", dt(29, 10, 1))
    _return_sequence(h)
    assert h.tick(dt(29, 10, 50), present=True) == [1]
    assert h.deleted == [1]
    assert h.rows == []
    # A later tick never re-delivers or re-deletes.
    assert h.tick(dt(29, 10, 51), present=True) == []
    assert h.deleted == [1] and len(h.spoken) == 1


def test_legacy_silent_speak_still_counts_as_accepted(tmp_path):
    # Callables that report nothing (None) are accepted, so the
    # pre-bool speech.speak and list.append fakes keep working.
    h = Harness(tmp_path)
    h.add(1, "legacy path", dt(29, 10, 1))
    _return_sequence(h)
    h.speak_result = None
    assert h.tick(dt(29, 10, 50), present=True) == [1]
    assert h.deleted == [1]


# ------------------------------------------------- timed-delivery helper

class TimedHarness:
    def __init__(self, monkeypatch):
        self.deleted = []
        self.emitted = []
        monkeypatch.setattr(scheduler, "sched_cancel",
                            lambda tid: self.deleted.append(tid) or True)
        monkeypatch.setattr(events, "emit_reminder_fired",
                            lambda tid, prompt: self.emitted.append(tid))


def test_timed_accepted_delivery_deletes(monkeypatch):
    th = TimedHarness(monkeypatch)
    assert scheduler._deliver_timed_reminder(7, "drink water") is True
    assert th.deleted == [7]
    assert th.emitted == [7]


def test_timed_muted_delivery_keeps_row(monkeypatch):
    th = TimedHarness(monkeypatch)
    monkeypatch.setattr(_speech, "speak", lambda t, whisper_mode=False: False)
    assert scheduler._deliver_timed_reminder(7, "drink water") is False
    assert th.deleted == []  # row stays due: the durable outbox record
    # Unmute: the next tick delivers and deletes exactly once.
    monkeypatch.setattr(_speech, "speak", lambda t, whisper_mode=False: True)
    assert scheduler._deliver_timed_reminder(7, "drink water") is True
    assert th.deleted == [7]


def test_timed_busy_path_uses_enqueue_verdict(monkeypatch):
    th = TimedHarness(monkeypatch)
    monkeypatch.setattr(_speech, "enqueue_speech", lambda t: False)
    assert scheduler._deliver_timed_reminder(
        7, "drink water", is_busy_fn=lambda: True) is False
    assert th.deleted == []
    monkeypatch.setattr(_speech, "enqueue_speech", lambda t: True)
    assert scheduler._deliver_timed_reminder(
        7, "drink water", is_busy_fn=lambda: True) is True
    assert th.deleted == [7]


def test_timed_speech_exception_keeps_row(monkeypatch):
    th = TimedHarness(monkeypatch)

    def _boom(text, whisper_mode=False):
        raise RuntimeError("speaker gone")

    monkeypatch.setattr(_speech, "speak", _boom)
    assert scheduler._deliver_timed_reminder(7, "drink water") is False
    assert th.deleted == []


def test_speak_returns_bool_verdict(monkeypatch):
    # speech.speak itself: muted/locked is a False, never a silent None.
    # Load the REAL speech.py (this file stubs aria.speech above).
    import importlib.util
    try:
        import speech_recognition  # noqa: F401
    except Exception:
        _sr = types.ModuleType("speech_recognition")
        _sr.Recognizer = object
        _sr.AudioData = object
        _sr.AudioSource = object
        monkeypatch.setitem(sys.modules, "speech_recognition", _sr)
    spec = importlib.util.spec_from_file_location(
        "aria_speech_real", os.path.join(PKG, "speech.py"))
    real_speech = importlib.util.module_from_spec(spec)
    sys.modules["aria_speech_real"] = real_speech
    spec.loader.exec_module(real_speech)
    monkeypatch.setattr(real_speech, "VOICE_ENABLED", False)
    assert real_speech.speak("hello") is False
    monkeypatch.setattr(real_speech, "VOICE_ENABLED", True)
    # Locked app also refuses; unlocked speaks (queued, True).
    monkeypatch.setitem(sys.modules, "aria.applock",
                        types.SimpleNamespace(is_locked=lambda: True))
    assert real_speech.speak("hello") is False
    monkeypatch.setitem(sys.modules, "aria.applock",
                        types.SimpleNamespace(is_locked=lambda: False))
    assert real_speech.speak("hello") is True
