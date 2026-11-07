"""Pass 28 #38: extract_price must not report shipping (or another
item's) price as the watched item's price.

Bug: the $ fallback took min() of every $ amount on the page, so
a $4.99 shipping line or a $9.99 accessory beat the $43.99 item
and fired a false price-drop alert.

Fix contract:
- JSON-LD "price" still wins when present (most reliable signal);
- one distinct $ amount on the page is the price;
- several distinct amounts are disambiguated by the watch label:
  the $ amount nearest a label occurrence in the text wins;
- several amounts with no label on the page are ambiguous — return
  None instead of guessing (no false alert; the check logs it and
  moves on).
"""
import os
import sqlite3
import sys
import tempfile
import types

import pytest

_TMPHOME = tempfile.mkdtemp(prefix="aria_pricecheck_extract_")
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


def test_jsonld_price_wins_over_dollar_amounts():
    text = ('{"price": "43.99"} Classic Porch Goose $43.99 '
            'Shipping $4.99 Accessory $9.99')
    assert pc.extract_price(text) == 43.99


def test_single_dollar_amount_is_the_price():
    assert pc.extract_price("Classic Porch Goose $43.99") == 43.99


def test_min_is_not_taken_when_many_prices():
    # The old code returned 4.99 here (the shipping line).
    text = ('Classic Porch Goose $43.99 — Shipping $4.99 — '
            'Frequently bought: Goose Dress $9.99')
    assert pc.extract_price(text, "porch goose") == 43.99


def test_label_nearest_price_wins():
    text = ('Shipping $4.99 for orders under $35.00. '
            'Classic Porch Goose $29.99, in stock.')
    assert pc.extract_price(text, "porch goose") == 29.99


def test_many_prices_without_label_is_ambiguous():
    text = ('Classic Porch Goose $43.99 — Shipping $4.99 — '
            'Frequently bought: Goose Dress $9.99')
    assert pc.extract_price(text, "garden gnome") is None
    assert pc.extract_price(text) is None


def test_no_prices_returns_none():
    assert pc.extract_price("no prices on this page") is None
    assert pc.extract_price("") is None


def test_repeated_same_price_is_single():
    text = "$43.99 Classic Porch Goose $43.99 — buy now $43.99"
    assert pc.extract_price(text) == 43.99


@pytest.fixture
def watch(monkeypatch, tmp_path):
    db = tmp_path / "price_watches.db"
    monkeypatch.setattr(pc, "PRICE_WATCH_DB", str(db))
    monkeypatch.setattr(pc, "add_log", lambda *a, **k: None)
    pages = {"current": ("Classic Porch Goose $43.99 — Shipping $4.99")}
    emitted = []

    class _FakeBuiltins:
        @staticmethod
        def tool_fetch_url(url):
            return pages["current"]

    fake_builtins = _FakeBuiltins()
    fake_events = types.ModuleType("aria.events")
    fake_events.emit_price_drop = lambda label, target, price, url: emitted.append(
        (label, target, price))
    monkeypatch.setitem(sys.modules, "aria.tools.builtins", fake_builtins)
    monkeypatch.setitem(sys.modules, "aria.events", fake_events)
    _tools_pkg.builtins = fake_builtins
    sys.modules["aria"].events = fake_events
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS price_watches(
            id INTEGER PRIMARY KEY, url TEXT, target REAL, label TEXT,
            last_price REAL, alerted INTEGER DEFAULT 0, created TEXT)""")
        conn.execute(
            "INSERT INTO price_watches(url, target, label) VALUES "
            "('https://example.com/goose', 40.0, 'porch goose')")
    return {"db": db, "pages": pages, "emitted": emitted}


def _last_price(db):
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT last_price FROM price_watches WHERE id=1").fetchone()[0]


def test_check_records_item_price_not_shipping(watch):
    db, pages, emitted = watch["db"], watch["pages"], watch["emitted"]
    pc.check_price_watches()
    assert _last_price(db) == 43.99
    assert emitted == []  # 43.99 is above the 40.00 target: no alert
    pages["current"] = ("Classic Porch Goose $35.00 — Shipping $4.99")
    pc.check_price_watches()
    assert _last_price(db) == 35.00
    assert len(emitted) == 1 and emitted[0][2] == 35.00


def test_check_ambiguous_page_records_nothing(watch):
    db, pages, emitted = watch["db"], watch["pages"], watch["emitted"]
    pages["current"] = ("Widget $43.99 — Shipping $4.99 — "
                        "Accessory $9.99")
    pc.check_price_watches()
    assert _last_price(db) is None
    assert emitted == []
