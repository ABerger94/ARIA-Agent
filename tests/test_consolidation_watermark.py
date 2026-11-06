"""Idle consolidation must hold the watermark on provider failure
(pass 25 P2#12).

A provider outage returns a bracketed sentinel ("[No provider
responded.]", "[provider_text error: ...]") that the old code
parsed as facts (or as zero facts) and then advanced the
watermark past — permanently consuming chat that was never
distilled. Drives the real loop with a stubbed clock/provider.
"""
import importlib
import os
import sqlite3
import sys
import tempfile
import time
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_consolidation_wm_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, 'ARIA')
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
for _name in ('aria.scheduler', 'aria.vision', 'aria.hardware',
              'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)
sys.modules['aria.scheduler'].BREAK_REMINDERS = True

config = importlib.import_module('aria.config')
memory = importlib.import_module('aria.memory')
proactive = importlib.import_module('aria.agent.proactive')


class _StopLoop(Exception):
    pass


def _setup_db(tmp_path, monkeypatch):
    db = str(tmp_path / "aria_memory.db")
    monkeypatch.setattr(memory, "DB_PATH", db)
    monkeypatch.setattr(proactive, "DB_PATH", db)
    memory.init_databases()
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO chat_history (timestamp, sender, message) "
        "VALUES (?, ?, ?)",
        [("2026-11-05 22:00:00", "Alek", "I like dark roast coffee"),
         ("2026-11-05 22:01:00", "A.R.I.A.", "Noted.")])
    conn.commit()
    conn.close()
    return db


def _run_one_pass(monkeypatch, provider_text):
    calls = {"n": 0}

    def _sleep(_s):
        calls["n"] += 1
        if calls["n"] >= 3:  # initial 300s, loop-top 1800s, then stop
            raise _StopLoop()

    monkeypatch.setattr(time, "sleep", _sleep)
    monkeypatch.setattr(proactive, "provider_text", provider_text)
    try:
        proactive.idle_consolidation_loop(lambda: False, lambda: 0.0)
    except _StopLoop:
        pass


def test_provider_outage_holds_watermark(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    assert proactive._consolidation_watermark() == 0
    _run_one_pass(monkeypatch, lambda *a, **k: "[No provider responded.]")
    assert proactive._consolidation_watermark() == 0
    # And the error-with-colon variant must not become a bogus fact.
    _run_one_pass(monkeypatch,
                  lambda *a, **k: "[provider_text error: boom]")
    assert proactive._consolidation_watermark() == 0
    conn = sqlite3.connect(str(tmp_path / "aria_memory.db"))
    rows = conn.execute(
        "SELECT value FROM memory WHERE category != 'system'").fetchall()
    conn.close()
    assert rows == [], rows


def test_none_reply_still_advances_watermark(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _run_one_pass(monkeypatch, lambda *a, **k: "NONE")
    assert proactive._consolidation_watermark() == 2
