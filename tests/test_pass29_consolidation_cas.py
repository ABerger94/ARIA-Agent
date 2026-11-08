"""Pass 29 #50: consolidation resolution must be atomic against
memory writes.

Verified against the code at 820f2c3:
- resolve_contradiction keep='new' did `existing()` -> compare
  -> memory_save() (unconditional upsert) as separate ops, and
  the read-back check after the save read the just-saved new
  value, so it could not detect an intervening overwrite;
- memory_save computed the embedding BEFORE DB_LOCK (the pause
  point) and then upserted unconditionally — a newer user
  correction saved between the comparison and the upsert was
  silently overwritten, and the archive kept the original old
  value, not the lost correction.

Fix contract pinned here:
- memory_save_if_value: conditional SQL (UPDATE ... WHERE
  key=? AND value=?) in one transaction under the lock, with
  the embedding computed beforehand and the lock never held
  across the provider call; returns False (nothing written)
  when the live value no longer matches;
- resolve_contradiction keep='new' saves via that CAS: a
  failed comparison leaves the contradiction pending, never
  overwrites;
- normal consolidation still lands, and a missing live row
  still saves the claim (insert path).
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
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

_config = importlib.import_module("aria.config")
_WS = tempfile.mkdtemp(prefix="aria-consolidation-cas50-ws-")
_config.WORKSPACE_DIR = _WS

memory = importlib.import_module("aria.memory")
cons = importlib.import_module("aria.agent.consolidation")


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    # Offline, hermetic store: no embedding provider call, no
    # spine writes to the real workspace, no backup queue.
    monkeypatch.setattr(memory, "_embed", lambda text: None)
    monkeypatch.setattr(memory, "_queue_backup", lambda: None)
    monkeypatch.setattr(memory, "SPINE_PATH",
                        str(tmp_path / "memory_spine.jsonl"))
    db = os.path.join(_WS, "aria_memory.db")
    if os.path.exists(db):
        os.unlink(db)
    memory.init_databases()
    cons._write_json_list(cons._queue_path(), [])
    cons._write_json_list(cons._retired_path(), [])
    yield


def _seed(key="work", value="Dock of the Bay"):
    memory.memory_save("general", key, value)
    assert memory.memory_get("general", key) == value


def _queue(key="work", new="Little Caesars"):
    counts = cons.reconcile([(key, new)])
    assert counts["contradictions"] == 1, counts
    (entry,) = cons.pending_contradictions()
    return entry


def test_conditional_save_matches_mismatches_and_inserts():
    _seed()
    # Wrong expected value: refused, row untouched.
    assert memory.memory_save_if_value(
        "consolidated", "work", "Little Caesars", "Something Else") is False
    assert memory.memory_get("general", "work") == "Dock of the Bay"
    # Expected None while a row exists: refused, row untouched.
    assert memory.memory_save_if_value(
        "consolidated", "work", "Little Caesars", None) is False
    assert memory.memory_get("general", "work") == "Dock of the Bay"
    # Right expected value: lands.
    assert memory.memory_save_if_value(
        "consolidated", "work", "Little Caesars", "Dock of the Bay") is True
    assert memory.memory_get("consolidated", "work") == "Little Caesars"
    # Expected None with no row: inserts.
    assert memory.memory_save_if_value(
        "consolidated", "brand_new", "hello", None) is True
    assert memory.memory_get("consolidated", "brand_new") == "hello"


def test_correction_between_compare_and_upsert_survives():
    _seed()
    entry = _queue()

    def _racing_cas(category, key, value, expected):
        # A newer user correction saved at exactly the pause
        # point — between resolve's comparison read and its save.
        memory.memory_save("general", key, "Corrected Job")
        return cons._cas_save(category, key, value, expected)

    with pytest.raises(ValueError, match="changed"):
        cons.resolve_contradiction(entry["id"], "new",
                                   cas_save=_racing_cas)
    # The correction survived; the claim did not overwrite it.
    assert memory.memory_get("general", "work") == "Corrected Job"
    # The contradiction is still pending, nothing was retired.
    assert [e["id"] for e in cons.pending_contradictions()] == [entry["id"]]
    assert cons._read_json_list(cons._retired_path()) == []


def test_normal_consolidation_still_lands():
    _seed()
    entry = _queue()
    settled = cons.resolve_contradiction(entry["id"], "new")
    assert settled["key"] == "work"
    assert memory.memory_get("consolidated", "work") == "Little Caesars"
    retired = cons._read_json_list(cons._retired_path())
    assert retired[-1]["value"] == "Dock of the Bay"
    assert "replaced" in retired[-1]["reason"]
    assert cons.pending_contradictions() == []


def test_resolve_with_missing_live_row_saves_claim():
    _seed()
    entry = _queue()
    assert memory.memory_forget_key("work") is True
    settled = cons.resolve_contradiction(entry["id"], "new")
    assert settled["key"] == "work"
    assert memory.memory_get("consolidated", "work") == "Little Caesars"
    assert cons.pending_contradictions() == []
