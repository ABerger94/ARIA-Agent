"""Reminder gate (pass 6): on-return reminders + quiet-hours
eligibility. Pure logic with injected clock/presence/speech/card
seams; the store is a tmp JSON file."""
import importlib
import os
import sys
import types
from datetime import datetime

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)

gate = importlib.import_module("aria.reminder_gate")


def dt(day, h, m=0):
    return datetime(2026, 10, day, h, m)


# ------------------------------------------------------------- quiet hours

def test_quiet_hour_boundaries():
    assert gate.in_quiet_hours(dt(29, 23, 0)) is True
    assert gate.in_quiet_hours(dt(29, 22, 59)) is False
    assert gate.in_quiet_hours(dt(30, 0, 30)) is True
    assert gate.in_quiet_hours(dt(30, 6, 59)) is True
    assert gate.in_quiet_hours(dt(30, 7, 0)) is False
    assert gate.in_quiet_hours(dt(29, 12, 0)) is False
    assert gate.timed_eligible(dt(29, 23, 15)) is False
    assert gate.timed_eligible(dt(29, 9, 0)) is True


# ------------------------------------------------------------- watcher

def test_first_observation_is_baseline_only():
    w = gate.ReturnWatcher()
    assert w.observe(True, dt(29, 10)) is False
    assert w.due(dt(29, 9)) is False  # no return has been seen


def test_return_edge_fires_once():
    w = gate.ReturnWatcher()
    w.observe(True, dt(29, 10, 0))
    assert w.observe(False, dt(29, 10, 5)) is False
    assert w.observe(True, dt(29, 10, 20)) is True   # the return
    assert w.observe(True, dt(29, 10, 21)) is False  # duplicate level
    assert w.observe(True, dt(29, 10, 22)) is False
    assert w.due(dt(29, 10, 1)) is True
    assert w.due(dt(29, 10, 25)) is False  # created after the return


def test_second_return_fires_again():
    w = gate.ReturnWatcher()
    w.observe(True, dt(29, 10, 0))
    w.observe(False, dt(29, 11, 0))
    assert w.observe(True, dt(29, 11, 30)) is True
    w.observe(False, dt(29, 12, 0))
    assert w.observe(True, dt(29, 12, 15)) is True
    assert w.due(dt(29, 11, 45)) is True


def test_watcher_state_roundtrip():
    w = gate.ReturnWatcher()
    w.observe(False, dt(29, 9, 0))
    w.observe(True, dt(29, 9, 40))
    w2 = gate.ReturnWatcher(w.to_state())
    assert w2.due(dt(29, 9, 15)) is True
    assert w2.observe(True, dt(29, 9, 41)) is False  # still just present


# ------------------------------------------------------------- tick harness

class Harness:
    def __init__(self, tmp_path):
        self.store = gate.GateStore(str(tmp_path / "gate.json"))
        self.rows = []
        self.spoken = []
        self.queued = []
        self.cards = {}  # filename -> message

    def add(self, tid, message, created):
        self.rows.append((tid, message, created))

    def tick(self, now, present, busy=False):
        def delete_row(tid):
            self.rows = [r for r in self.rows if r[0] != tid]

        def write_card(tid, message):
            name = f"reminder-{tid}.txt"
            self.cards[name] = message
            return name

        return gate.tick(
            now=now, present=present, store=self.store, rows=list(self.rows),
            is_busy=busy, speak=self.spoken.append,
            queue_speech=self.queued.append, delete_row=delete_row,
            write_card=write_card,
            card_exists=lambda f: f in self.cards)


def test_delivery_on_return(tmp_path):
    h = Harness(tmp_path)
    h.add(1, "finish that GitHub issue", dt(29, 10, 1))
    h.tick(dt(29, 10, 2), present=True)    # baseline
    h.tick(dt(29, 10, 30), present=False)  # away
    done = h.tick(dt(29, 10, 50), present=True)  # return
    assert done == [1]
    assert h.spoken == ["Reminder from earlier: finish that GitHub issue"]
    assert h.rows == []
    assert h.cards == {"reminder-1.txt": "finish that GitHub issue"}
    cards = gate.pending_cards(h.store)
    assert len(cards) == 1 and cards[0]["tid"] == 1


def test_duplicate_presence_never_redelivers(tmp_path):
    h = Harness(tmp_path)
    h.add(1, "water the plants", dt(29, 10, 1))
    h.tick(dt(29, 10, 2), present=True)
    h.tick(dt(29, 10, 30), present=False)
    h.tick(dt(29, 10, 50), present=True)
    assert h.tick(dt(29, 10, 51), present=True) == []
    assert h.tick(dt(29, 10, 52), present=True) == []
    assert len(h.spoken) == 1


def test_quiet_hours_do_not_hold_on_return(tmp_path):
    # Alek's 2026-10-29 23:49 witness: he set an on-return reminder,
    # locked, returned, unlocked — inside quiet hours — and got
    # nothing, because the original gate held it for 07:00. A return
    # is Alek acting; the reminder fires on it at any hour.
    h = Harness(tmp_path)
    h.add(1, "call the storage office", dt(29, 22, 0))
    h.tick(dt(29, 22, 5), present=True)
    h.tick(dt(29, 23, 10), present=False)
    done = h.tick(dt(29, 23, 30), present=True)  # return, quiet hours
    assert done == [1]
    assert h.spoken == ["Reminder from earlier: call the storage office"]


def test_no_delivery_while_away_overnight(tmp_path):
    h = Harness(tmp_path)
    h.add(1, "overnight thing", dt(29, 22, 0))
    h.tick(dt(29, 22, 5), present=True)
    h.tick(dt(29, 23, 10), present=False)
    assert h.tick(dt(30, 2, 0), present=False) == []
    assert h.spoken == [] and len(h.rows) == 1


def test_created_after_return_waits_for_next(tmp_path):
    h = Harness(tmp_path)
    h.tick(dt(29, 10, 0), present=True)
    h.tick(dt(29, 10, 10), present=False)
    h.tick(dt(29, 10, 20), present=True)   # a return happens
    h.add(1, "set after the return", dt(29, 10, 25))
    assert h.tick(dt(29, 10, 26), present=True) == []
    h.tick(dt(29, 11, 0), present=False)
    assert h.tick(dt(29, 11, 5), present=True) == [1]


def test_restart_recovery(tmp_path):
    h = Harness(tmp_path)
    h.tick(dt(29, 20, 0), present=True)
    h.tick(dt(29, 20, 10), present=False)
    h.tick(dt(29, 20, 20), present=True)   # return recorded in store
    # Process restarts: a fresh harness over the same store file.
    h2 = Harness(tmp_path)
    h2.add(1, "created before the return", dt(29, 19, 55))
    done = h2.tick(dt(29, 20, 25), present=True)
    assert done == [1]


def test_busy_delivery_queues_speech(tmp_path):
    h = Harness(tmp_path)
    h.add(1, "stretch", dt(29, 10, 1))
    h.tick(dt(29, 10, 2), present=True)
    h.tick(dt(29, 10, 30), present=False)
    done = h.tick(dt(29, 10, 50), present=True, busy=True)
    assert done == [1] and h.spoken == [] and len(h.queued) == 1


def test_acknowledge_clears_cards(tmp_path):
    h = Harness(tmp_path)
    h.add(1, "one", dt(29, 10, 1))
    h.tick(dt(29, 10, 2), present=True)
    h.tick(dt(29, 10, 30), present=False)
    h.tick(dt(29, 10, 50), present=True)
    deleted = []
    n = gate.acknowledge_all(store=h.store, delete_card=deleted.append)
    assert n == 1 and deleted == ["reminder-1.txt"]
    assert gate.pending_cards(h.store) == []
    assert gate.acknowledge_all(store=h.store,
                                delete_card=deleted.append) == 0


def test_external_card_deletion_reconciles(tmp_path):
    h = Harness(tmp_path)
    h.add(1, "one", dt(29, 10, 1))
    h.tick(dt(29, 10, 2), present=True)
    h.tick(dt(29, 10, 30), present=False)
    h.tick(dt(29, 10, 50), present=True)
    assert len(gate.pending_cards(h.store)) == 1
    del h.cards["reminder-1.txt"]  # he deleted the file himself
    h.tick(dt(29, 10, 55), present=True)
    assert gate.pending_cards(h.store) == []
