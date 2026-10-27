"""Memory tab store functions (Alek, 2026-10-27, Mark-LVI parity).

memory_rows_for_ui / memory_forget_key back the dashboard's
Memory tab: every displayable fact newest first with NO 15-row
cap (memory_get_all caps for prompt use), '_' hidden keys
excluded (the store's standing visibility rule), and an
exact-key forget for the per-row ✕. Runs against a temp DB;
nothing touches the real store.
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


def _seed(tmp_path, monkeypatch):
    db = str(tmp_path / "aria_memory.db")
    monkeypatch.setattr(memory, "DB_PATH", db)
    memory.init_databases()
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO memory (category, key, value, updated_at) "
        "VALUES (?, ?, ?, ?)",
        [("user", "fav_color", "blue", "2026-10-20 10:00:00"),
         ("fact", "job", "Little Caesars", "2026-10-26 09:00:00"),
         ("sys", "_internal_note", "hidden", "2026-10-27 09:00:00")])
    conn.commit()
    conn.close()
    return db


def test_rows_for_ui_newest_first_hidden_excluded(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    rows = memory.memory_rows_for_ui()
    assert [r["key"] for r in rows] == ["job", "fav_color"]
    assert rows[0] == {"category": "fact", "key": "job",
                       "value": "Little Caesars", "updated": "2026-10-26"}
    assert rows[1]["updated"] == "2026-10-20"


def test_forget_key_exact(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    assert memory.memory_forget_key("job") is True
    assert [r["key"] for r in memory.memory_rows_for_ui()] == ["fav_color"]
    # Already gone -> False, and a substring does NOT forget.
    assert memory.memory_forget_key("job") is False
    assert memory.memory_forget_key("fav") is False
    assert [r["key"] for r in memory.memory_rows_for_ui()] == ["fav_color"]


def test_rows_for_ui_unreadable_store_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DB_PATH",
                        str(tmp_path / "missing_dir" / "nope.db"))
    assert memory.memory_rows_for_ui() == []


def test_map_points_embedding_roundtrip(tmp_path, monkeypatch):
    # The constellation feeds on these: an embedded fact comes
    # back with its vector, an unembedded one with None (it
    # will park on a ring), hidden keys stay hidden.
    import numpy as np
    db = _seed(tmp_path, monkeypatch)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE memory SET embedding=? WHERE key='job'",
                 (memory._pack_embedding([0.25, 0.5]),))
    conn.commit()
    conn.close()
    rows = memory.memory_map_points()
    by_key = {r["key"]: r for r in rows}
    assert set(by_key) == {"job", "fav_color"}
    assert by_key["job"]["vec"] is not None
    assert len(by_key["job"]["vec"]) == 2
    assert by_key["fav_color"]["vec"] is None
