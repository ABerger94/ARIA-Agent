"""Relevance grading (Self-RAG "IsRel") for memory_search_semantic.

The semantic hit list (cosine > 0.35) is graded by ONE light-tier
model call that can only REMOVE hits. The contract under test is the
fail-open shape: grader down / raising / unparseable / disabled all
return the legacy ungraded result byte-for-byte, while a real
"nothing relevant" verdict stands (no keyword-fallback rescue).

The grader seam is memory._memory_grade_call — stubbed here, so no
provider and no network is ever touched. Embeddings are stubbed too:
query vector [1, 0]; dog_name cos ~0.98, pizza_topping cos ~0.55
(both semantic hits), car_model cos ~0.10 (never a hit).
"""
import os
import sqlite3
import sys
import types

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

import importlib

config = importlib.import_module("aria.config")  # noqa: F401
memory = importlib.import_module("aria.memory")
usage = importlib.import_module("aria.usage")

ROWS = [
    ("fact", "dog_name", "the dog is called Biscuit", [0.98, 0.20]),
    ("pref", "pizza_topping", "pepperoni on pizza", [0.55, 0.835]),
    ("fact", "car_model", "a 2008 Honda Civic", [0.10, 0.995]),
]

BOTH_HITS = ("• [fact] dog_name: the dog is called Biscuit\n"
             "• [pref] pizza_topping: pepperoni on pizza")


def _seed(tmp_path, monkeypatch, grader):
    db = str(tmp_path / "aria_memory.db")
    monkeypatch.setattr(memory, "DB_PATH", db)
    memory.init_databases()
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO memory (category, key, value, embedding) "
        "VALUES (?, ?, ?, ?)",
        [(c, k, v, memory._pack_embedding(e)) for c, k, v, e in ROWS])
    conn.commit()
    conn.close()
    monkeypatch.setattr(memory, "_embed", lambda text: [1.0, 0.0])
    monkeypatch.setattr(memory, "_memory_grade_call", grader)
    # Hermetic settings: grading enabled unless a test overrides.
    monkeypatch.setattr(usage, "settings", lambda: {})
    return db


def test_grader_keeps_relevant_drops_irrelevant(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, lambda q, hits: "1")
    logs = []
    monkeypatch.setattr(memory, "_LOG_CALLBACK", logs.append)
    out = memory.memory_search_semantic("what is my dog called")
    assert out == "• [fact] dog_name: the dog is called Biscuit"
    # The drop is witnessed in the activity log, naming the key.
    assert any("Memory grading dropped" in line and "pizza_topping" in line
               for line in logs), logs


def test_grader_raises_fails_open(tmp_path, monkeypatch):
    def boom(q, hits):
        raise RuntimeError("provider exploded")
    _seed(tmp_path, monkeypatch, boom)
    out = memory.memory_search_semantic("what is my dog called")
    assert out == BOTH_HITS


def test_grader_unparseable_fails_open(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch,
          lambda q, hits: "Both of these seem lovely, honestly.")
    out = memory.memory_search_semantic("what is my dog called")
    assert out == BOTH_HITS


def test_grader_out_of_range_numbers_fail_open(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, lambda q, hits: "7, 9")
    out = memory.memory_search_semantic("what is my dog called")
    assert out == BOTH_HITS


def test_all_irrelevant_verdict_stands(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, lambda q, hits: "NONE")
    # Query "dog" WOULD match dog_name via the keyword fallback if the
    # code fell through — the grader's verdict must stand instead.
    out = memory.memory_search_semantic("dog")
    assert out == "No relevant memories found."


def test_grading_disabled_never_calls_grader(tmp_path, monkeypatch):
    calls = []

    def counting(q, hits):
        calls.append((q, hits))
        return "1"
    _seed(tmp_path, monkeypatch, counting)
    monkeypatch.setattr(usage, "settings",
                        lambda: {"memory_grading": {"enabled": False}})
    out = memory.memory_search_semantic("what is my dog called")
    assert out == BOTH_HITS
    assert calls == []


def test_keyword_fallback_unchanged_without_semantic_hits(tmp_path,
                                                          monkeypatch):
    def forbidden(q, hits):
        raise AssertionError("grader must not run on the keyword path")
    _seed(tmp_path, monkeypatch, forbidden)
    monkeypatch.setattr(memory, "_embed", lambda text: None)
    out = memory.memory_search_semantic("pizza")
    assert out == "• [pref] pizza_topping: pepperoni on pizza"


def test_parse_grade_reply_shapes():
    parse = memory._parse_grade_reply
    assert parse("1, 3", 3) == {0, 2}
    assert parse("NONE", 3) == set()
    assert parse(None, 3) is None
    assert parse("", 3) is None
    assert parse("no idea", 3) is None
    assert parse("5", 3) is None  # points at no real hit
