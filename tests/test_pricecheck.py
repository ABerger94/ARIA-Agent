"""Pricecheck startup: a fresh install (no price_watches table yet)
must read as "no watches", not a setup failure (Alek's startup log,
2026-10-27: 'Pricecheck task setup failed: no such table:
price_watches' — ensure_pricecheck_task SELECTed from a table that
only check_price_watches created)."""
import sqlite3
import sys
import types
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)


def _pricecheck(monkeypatch, tmp_path):
    import importlib
    pc = importlib.import_module('aria.pricecheck')
    db = tmp_path / 'price_watches.db'
    monkeypatch.setattr(pc, 'PRICE_WATCH_DB', str(db))
    sched = types.ModuleType('aria.scheduler')
    state = {'kinds': [], 'added': []}
    sched.sched_list = lambda: [(i + 1, k) for i, k in enumerate(state['kinds'])]
    sched.sched_add = lambda name, kind, interval_s=None: state['added'].append((name, kind, interval_s))
    monkeypatch.setitem(sys.modules, 'aria.scheduler', sched)
    return pc, db, state


def test_ensure_task_fresh_db_is_quiet_noop(monkeypatch, tmp_path):
    pc, db, state = _pricecheck(monkeypatch, tmp_path)
    assert not db.exists()
    assert pc.ensure_pricecheck_task() is False     # no failure path
    assert state['added'] == []
    assert db.exists()                              # table now created


def test_ensure_task_schedules_once_when_watches_exist(monkeypatch, tmp_path):
    pc, db, state = _pricecheck(monkeypatch, tmp_path)
    assert pc.ensure_pricecheck_task() is False
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO price_watches(url, target, label) VALUES ('https://x', 10.0, 'thing')")
    assert pc.ensure_pricecheck_task() is True
    assert state['added'] == [('pricecheck', 'pricecheck', 3600)]
    state['kinds'] = ['pricecheck']
    assert pc.ensure_pricecheck_task() is False     # already scheduled
