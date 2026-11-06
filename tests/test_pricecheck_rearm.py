"""Pass 26 B5#3: price alerts must rearm after the price rises again.

Bug: check_price_watches() set alerted=1 on a qualifying drop and
alerted was never written back to 0 anywhere — the docstring says
"only fires once per drop" but the code implemented once-ever.

Fix contract: in check_price_watches(), after updating last_price,
clear the flag when the price is back above target, so the next
genuine drop alerts again. Transition model only (above->below
alerts, below->above rearms); no new cooldown machinery.
"""
import os
import sqlite3
import sys
import tempfile
import types

import pytest

_TMPHOME = tempfile.mkdtemp(prefix="aria_pricecheck_rearm_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg

import importlib  # noqa: E402

pc = importlib.import_module("aria.pricecheck")


@pytest.fixture
def watch(monkeypatch, tmp_path):
    db = tmp_path / "price_watches.db"
    monkeypatch.setattr(pc, "PRICE_WATCH_DB", str(db))
    monkeypatch.setattr(pc, "add_log", lambda *a, **k: None)
    prices = {"current": 35.0}
    emitted = []

    class _FakeBuiltins:
        @staticmethod
        def tool_fetch_url(url):
            return f'{{"price": {prices["current"]:.2f}}}'

    fake_builtins = _FakeBuiltins()
    fake_events = types.ModuleType("aria.events")
    fake_events.emit_price_drop = lambda label, target, price, url: emitted.append(
        (label, target, price))
    monkeypatch.setitem(sys.modules, "aria.tools.builtins", fake_builtins)
    monkeypatch.setitem(sys.modules, "aria.events", fake_events)
    # The from-imports inside check_price_watches resolve through
    # the parent packages' attributes first — bind the fakes there too.
    _tools_pkg.builtins = fake_builtins
    sys.modules["aria"].events = fake_events
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS price_watches(
            id INTEGER PRIMARY KEY, url TEXT, target REAL, label TEXT,
            last_price REAL, alerted INTEGER DEFAULT 0, created TEXT)""")
        conn.execute(
            "INSERT INTO price_watches(url, target, label) VALUES "
            "('https://example.com/goose', 40.0, 'goose')")
    return {"db": db, "prices": prices, "emitted": emitted}


def _alerted(db):
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT alerted FROM price_watches WHERE id=1").fetchone()[0]


def test_drop_alerts_rise_rearms_drop_alerts_again(watch):
    db, prices, emitted = watch["db"], watch["prices"], watch["emitted"]
    prices["current"] = 35.0
    pc.check_price_watches()
    assert len(emitted) == 1, emitted
    assert _alerted(db) == 1
    # Still below target: no duplicate alert.
    pc.check_price_watches()
    assert len(emitted) == 1, emitted
    # Price rises above target: the flag rearms.
    prices["current"] = 45.0
    pc.check_price_watches()
    assert len(emitted) == 1, emitted
    assert _alerted(db) == 0
    # A fresh drop alerts again.
    prices["current"] = 35.0
    pc.check_price_watches()
    assert len(emitted) == 2, emitted
    assert _alerted(db) == 1


def test_price_exactly_at_target_still_alerts(watch):
    prices, emitted = watch["prices"], watch["emitted"]
    prices["current"] = 40.0
    pc.check_price_watches()
    assert len(emitted) == 1, emitted
