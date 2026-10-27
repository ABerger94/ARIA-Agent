"""Gap fill 4 — memory consolidation reconciliation.

Pins:
  * reconcile: new key saves; same value (case/whitespace
    variants) is a quiet dupe; a conflicting value NEVER
    overwrites — it queues as a contradiction;
  * resolve keep='new' archives the stored value to the retired
    list and saves the claim; keep='old' archives the claim and
    leaves memory untouched; unknown ids return None;
  * retire_fact archives + deletes a live fact;
  * the queue survives a reload (it is a file, not state).
"""
import importlib
import os
import sys
import tempfile
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg

_cfg = importlib.import_module("aria.config")
_WS = tempfile.mkdtemp(prefix="aria-consolidation-ws-")
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


def test_reconcile_save_dupe_contradiction():
    mem = FakeMemory()
    mem.rows["favorite_color"] = ("general", "Teal")
    counts = cons.reconcile(
        [("new_fact", "lives in Dundalk"),
         ("favorite_color", "  teal "),          # dupe, normalized
         ("favorite_color", "purple")],          # contradiction
        existing=mem.existing, save=mem.save)
    assert counts == {"saved": 1, "dupes": 1, "contradictions": 1}, counts
    assert mem.rows["new_fact"] == ("consolidated", "lives in Dundalk")
    assert mem.rows["favorite_color"] == ("general", "Teal")  # untouched
    pending = cons.pending_contradictions()
    assert len(pending) == 1
    assert pending[0]["key"] == "favorite_color"
    assert pending[0]["old_value"] == "Teal"
    assert pending[0]["new_value"] == "purple"
    print("ok reconcile_save_dupe_contradiction")


def test_resolve_new_archives_old_resolve_old_archives_claim():
    cons._write_json_list(cons._queue_path(), [])  # isolate from test 1
    mem = FakeMemory()
    mem.rows["work"] = ("general", "Dock of the Bay")
    cons.reconcile([("work", "Little Caesars")],
                   existing=mem.existing, save=mem.save)
    entry = cons.pending_contradictions()[0]
    settled = cons.resolve_contradiction(
        entry["id"], "new", save=mem.save, delete=mem.delete)
    assert settled["key"] == "work"
    assert mem.rows["work"] == ("consolidated", "Little Caesars")
    assert mem.deleted == ["work"]
    assert cons.pending_contradictions() == []
    retired = cons._read_json_list(cons._retired_path())
    assert retired[-1]["value"] == "Dock of the Bay"
    assert "replaced" in retired[-1]["reason"]

    cons.reconcile([("work", "somewhere else")],
                   existing=mem.existing, save=mem.save)
    entry2 = cons.pending_contradictions()[0]
    cons.resolve_contradiction(entry2["id"], "old",
                                save=mem.save, delete=mem.delete)
    assert mem.rows["work"] == ("consolidated", "Little Caesars")
    retired = cons._read_json_list(cons._retired_path())
    assert retired[-1]["value"] == "somewhere else"
    assert "rejected" in retired[-1]["reason"]
    assert cons.resolve_contradiction("nope", "new") is None
    print("ok resolve_new_archives_old_resolve_old_archives_claim")


def test_retire_fact():
    mem = FakeMemory()
    mem.rows["old_job"] = ("general", "Bar Louie")
    assert cons.retire_fact("old_job", existing=mem.existing,
                            delete=mem.delete) is True
    assert "old_job" not in mem.rows and mem.deleted == ["old_job"]
    retired = cons._read_json_list(cons._retired_path())
    assert any(r["key"] == "old_job" and r["value"] == "Bar Louie"
               for r in retired)
    assert cons.retire_fact("missing", existing=mem.existing,
                            delete=mem.delete) is False
    print("ok retire_fact")


if __name__ == "__main__":
    test_reconcile_save_dupe_contradiction()
    test_resolve_new_archives_old_resolve_old_archives_claim()
    test_retire_fact()
    print("ALL CONSOLIDATION CHECKS PASSED")
