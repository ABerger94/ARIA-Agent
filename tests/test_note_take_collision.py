"""note_take: two notes in the same minute must both survive (pass 25 P2#22).

Minute-resolution keys collided on the memory upsert, so the
first note was silently overwritten. Keys now carry seconds +
microseconds. Uses a temp DB; spine/backup side effects stubbed.
"""
import os
import sqlite3
import sys
import types
import importlib

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
tools_pkg = types.ModuleType('aria.tools')
tools_pkg.__path__ = [PKG + '/tools']
sys.modules['aria.tools'] = tools_pkg
agent_pkg = types.ModuleType('aria.agent')
agent_pkg.__path__ = [PKG + '/agent']
sys.modules['aria.agent'] = agent_pkg
_ps = types.ModuleType('psutil')
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules['psutil'] = _ps
for name in ('aria.scheduler', 'aria.vision', 'aria.hardware', 'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[name] = types.ModuleType(name)

memory = importlib.import_module('aria.memory')
builtins = importlib.import_module('aria.tools.builtins')


def _setup(tmp_path, monkeypatch):
    db = str(tmp_path / "aria_memory.db")
    monkeypatch.setattr(memory, "DB_PATH", db)
    monkeypatch.setattr(memory, "spine_append", lambda *a, **k: None)
    monkeypatch.setattr(memory, "_queue_backup", lambda: None)
    monkeypatch.setattr(builtins, "JOURNAL_DIR", str(tmp_path / "journal"))
    memory.init_databases()
    return db


def test_two_notes_same_minute_both_survive(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    assert builtins.note_take("first note") == "Noted."
    assert builtins.note_take("second note") == "Noted."
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT value FROM memory WHERE category='note' ORDER BY key"
    ).fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["first note", "second note"], rows
