"""Pass 26 B3#6: contradiction resolution must not delete live
memory before its replacement succeeds (compare-and-swap).

Verified against the code at b1ba4ad:
- consolidation.py resolve_contradiction() does retire(key, old)
  -> delete(key) -> save(new) with no save-failure handling
  (memory_save can raise) — a failing save leaves the live key
  deleted and the replacement unsaved;
- it never checks the current value via _existing() — if the
  live value changed since queueing, the CURRENT value is deleted
  without being archived.

Fix contract pinned here:
- keep='new' reads the current value first and REFUSES (raising,
  entry stays queued) when a live value exists that no longer
  matches the queued old_value;
- the replacement is saved FIRST and verified to read back
  before the old value is retired; the live row is deleted only
  if it still holds the old value after the save (an upsert
  save already replaced it, so a delete there would destroy
  the replacement);
- a failing save propagates with the live key intact and the
  entry still queued.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import tempfile
import types

import pytest

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg

_cfg = importlib.import_module("aria.config")
_WS = tempfile.mkdtemp(prefix="aria-consolidation-cas-ws-")
_cfg.WORKSPACE_DIR = _WS

cons = importlib.import_module("aria.agent.consolidation")


class FakeMemory:
    def __init__(self):
        self.rows = {}     # key -> (category, value)
        self.deleted = []

    def existing(self, key):
        return self.rows.get(key)

    def save(self, category, key, value):
        self.rows[key] = (category, value)

    def delete(self, key):
        self.deleted.append(key)
        self.rows.pop(key, None)


def _queue(mem, key="work", old="Dock of the Bay", new="Little Caesars"):
    cons._write_json_list(cons._queue_path(), [])
    mem.rows[key] = ("general", old)
    cons.reconcile([(key, new)], existing=mem.existing, save=mem.save)
    (entry,) = cons.pending_contradictions()
    return entry


def test_failing_save_leaves_live_key_intact():
    mem = FakeMemory()
    entry = _queue(mem)

    def _failing_save(category, key, value):
        raise RuntimeError("disk full (injected)")

    with pytest.raises(RuntimeError):
        cons.resolve_contradiction(entry["id"], "new",
                                   save=_failing_save, delete=mem.delete,
                                   existing=mem.existing)
    assert mem.rows["work"] == ("general", "Dock of the Bay")
    assert mem.deleted == []
    assert [e["id"] for e in cons.pending_contradictions()] == [entry["id"]]


def test_changed_live_value_is_not_deleted():
    mem = FakeMemory()
    entry = _queue(mem)
    mem.rows["work"] = ("general", "Somewhere Else Entirely")
    with pytest.raises(ValueError, match="changed"):
        cons.resolve_contradiction(entry["id"], "new",
                                   save=mem.save, delete=mem.delete,
                                   existing=mem.existing)
    assert mem.rows["work"] == ("general", "Somewhere Else Entirely")
    assert mem.deleted == []
    assert [e["id"] for e in cons.pending_contradictions()] == [entry["id"]]


def test_save_that_does_not_land_is_refused():
    mem = FakeMemory()
    entry = _queue(mem)

    def _noop_save(category, key, value):
        return None  # claims success, writes nothing

    with pytest.raises(ValueError):
        cons.resolve_contradiction(entry["id"], "new",
                                   save=_noop_save, delete=mem.delete,
                                   existing=mem.existing)
    assert mem.rows["work"] == ("general", "Dock of the Bay")
    assert mem.deleted == []
    assert [e["id"] for e in cons.pending_contradictions()] == [entry["id"]]


def test_happy_path_saves_first_then_retires():
    mem = FakeMemory()
    entry = _queue(mem)
    settled = cons.resolve_contradiction(entry["id"], "new",
                                         save=mem.save, delete=mem.delete,
                                         existing=mem.existing)
    assert settled["key"] == "work"
    assert mem.rows["work"] == ("consolidated", "Little Caesars")
    # The upsert save already replaced the live row — deleting
    # after it would destroy the replacement, so no delete runs.
    assert mem.deleted == []
    retired = cons._read_json_list(cons._retired_path())
    assert retired[-1]["value"] == "Dock of the Bay"
    assert "replaced" in retired[-1]["reason"]
    assert cons.pending_contradictions() == []
