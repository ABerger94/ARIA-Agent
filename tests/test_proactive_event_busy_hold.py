"""Pass 28 #40: busy-time event notifications must wait, not vanish.

Bug: process_event_queue drained the whole bus, then proactive_say
returned False (without speaking) while the user was busy — the
event was already popped, so a reminder / price drop / calendar
notice fired during a call was discarded forever.

Fix contract:
- busy at entry: nothing is drained (the queue waits for the
  next cycle);
- busy mid-loop: the unspoken remainder is re-queued (subscribers
  are NOT re-notified — they already saw the first emit);
- a decline-suppressed event still drops deliberately (that is
  the decline-learning contract, not a loss);
- a held event speaks on the next free cycle.
"""
import json
import os
import sys
import time
import types

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
sys.modules["aria.speech"] = types.ModuleType("aria.speech")

import pytest  # noqa: E402

from aria import events  # noqa: E402
from aria.agent import proactive  # noqa: E402


@pytest.fixture(autouse=True)
def _quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(proactive, "add_log", lambda *a, **k: None)
    monkeypatch.setattr(proactive, "spine_append", lambda *a, **k: None)
    # Decline-learning state must not touch the real workspace file.
    monkeypatch.setattr(proactive, "_HEARTBEAT_MEM_FILE",
                        str(tmp_path / "heartbeat_memory.json"))
    # The bus is module-global: each test starts empty.
    events.drain()
    yield
    events.drain()


def _emit_two():
    events.emit("reminder_fired", {"prompt": "Take your meds"})
    events.emit("price_drop", {"label": "porch goose",
                               "new_price": 35.0})
    assert events.queue_size() == 2


def test_busy_at_entry_holds_all_events():
    _emit_two()
    spoken = []
    n = proactive.process_event_queue(spoken.append, lambda: True)
    assert n == 0
    assert spoken == []
    assert events.queue_size() == 2


def test_busy_mid_loop_holds_the_rest():
    _emit_two()
    spoken = []
    state = {"busy": False}

    def speak(text):
        spoken.append(text)
        state["busy"] = True  # user becomes busy after the first speak

    n = proactive.process_event_queue(speak, lambda: state["busy"])
    assert n == 1
    assert len(spoken) == 1 and "Take your meds" in spoken[0]
    assert events.queue_size() == 1
    remaining = events.drain()
    assert remaining[0]["type"] == "price_drop"


def test_free_cycle_speaks_all_and_drains():
    _emit_two()
    spoken = []
    n = proactive.process_event_queue(spoken.append, lambda: False)
    assert n == 2
    assert len(spoken) == 2
    assert events.queue_size() == 0


def test_suppressed_event_still_drops(tmp_path):
    mem = {"event_reminder_fired":
           {"suppressed_until": time.time() + 3600}}
    with open(proactive._HEARTBEAT_MEM_FILE, "w") as f:
        json.dump(mem, f)
    events.emit("reminder_fired", {"prompt": "Take your meds"})
    spoken = []
    n = proactive.process_event_queue(spoken.append, lambda: False)
    assert n == 0
    assert spoken == []
    assert events.queue_size() == 0


def test_held_event_speaks_when_free():
    _emit_two()
    spoken = []
    state = {"busy": True}
    n = proactive.process_event_queue(spoken.append, lambda: state["busy"])
    assert n == 0 and events.queue_size() == 2
    state["busy"] = False
    n = proactive.process_event_queue(spoken.append, lambda: state["busy"])
    assert n == 2
    assert len(spoken) == 2
    assert events.queue_size() == 0
