"""Tests for aria/ops.py — pure logic, no OpenCV needed.

Run:  python3 tests/test_ops.py
"""
import sys
import os
import types
from datetime import datetime, timedelta

# Bypass aria/__init__.py's eager hardware imports (same pattern as test_providers.py)
PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg

from aria import ops


def test_parse_mail_lines():
    raw = ("[uid=101] | Jane Doe <jane@x.com> | \"Dinner plans\"\n"
           "[uid=102] | noreply@bank.com | \"Statement ready\"\n"
           "garbage line\n")
    items = ops._parse_mail_lines(raw)
    assert len(items) == 2, items
    assert items[0]["uid"] == "101"
    assert items[0]["sender"] == "Jane Doe"
    assert items[0]["subject"] == "Dinner plans"


def test_fmt_countdown():
    assert ops.fmt_countdown(-5) == "OVERDUE"
    assert ops.fmt_countdown(90) == "in 1m"
    assert ops.fmt_countdown(5400) == "in 1h30"
    assert ops.fmt_countdown(90000) == "in 1d"


def test_compute_attention():
    now = datetime.now()
    dash = {
        "schedule": {"data": {"events": [
            {"summary": "Dock shift", "start": now + timedelta(hours=1),
             "end": now + timedelta(hours=4), "all_day": False}]}, "stale": False},
        "inbox": {"data": {"items": [
            {"uid": "1", "sender": "Mom", "subject": "hi", "important": True},
            {"uid": "2", "sender": "promo@x", "subject": "sale", "important": False}],
            "unread": 2}, "stale": False},
        "providers": {"data": {"active": "groq", "chain": [], "stats": {}},
                      "stale": False},
        "tasks": {"data": {"items": [], "overdue": 1}, "stale": False},
        "systems": {"data": {"available": True, "disk": 95.0, "batt": None},
                    "stale": False},
    }
    attn = ops.compute_attention(dash, now)
    assert len(attn) <= 3
    assert any("Dock shift" in a for a in attn), attn
    assert any("important unread" in a for a in attn), attn
    assert any("fallback" in a for a in attn), attn
    # overdue + disk are cut off by the 3-item cap — fine


def test_compute_attention_clear():
    dash = {n: {"data": {"events": [], "items": [],
                         "active": "ollama_cloud", "chain": [],
                         "available": False, "overdue": 0},
                "stale": False}
            for n in ("schedule", "inbox", "providers", "tasks", "systems")}
    assert ops.compute_attention(dash) == []


def test_compute_timeline():
    now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    events = [
        {"summary": "Lunch", "start": now + timedelta(hours=1),
         "end": now + timedelta(hours=2), "all_day": False},
        {"summary": "Past", "start": now - timedelta(hours=3),
         "end": now - timedelta(hours=2), "all_day": False},
        {"summary": "AllDay", "start": now, "end": now,
         "all_day": True},
    ]
    blocks = ops.compute_timeline(events, now)
    assert len(blocks) == 1, blocks
    b = blocks[0]
    assert b["label"] == "Lunch"
    assert 0 < b["x0"] < b["x1"] <= 1.0


def test_dashboard_shape():
    dash = ops.get_dashboard()
    assert set(dash) == {"schedule", "inbox", "tasks", "systems", "providers"}
    for name, entry in dash.items():
        assert "data" in entry and "stale" in entry


if __name__ == "__main__":
    test_parse_mail_lines()
    test_fmt_countdown()
    test_compute_attention()
    test_compute_attention_clear()
    test_compute_timeline()
    test_dashboard_shape()
    print("ALL OPS TESTS PASSED")
