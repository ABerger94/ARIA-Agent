"""Pass 26 B5#7: OS reminder names must not collide and overwrite
earlier reminders.

Bug: task_name_for built "ARIA Reminder <yyyymmddHHMM> <slug>" with
the slug truncated to 24 chars at minute granularity, and
tool_os_reminder registered with schtasks /Create ... /F — force
overwrites both the existing task and its script. Two distinct
messages sharing the first 24 slug chars in the same minute
produced byte-identical names; the second silently replaced the
first.

Fix contract:
- the name carries a short hash of the full sanitized message
  (first 8 hex of sha1), so distinct messages always differ;
- retries of the SAME reminder reuse the same name (the hash is
  deterministic on the message).
"""
import importlib
import os
import sys
import types
from datetime import datetime, timedelta

import pytest

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg

gap = importlib.import_module("aria.tools.gaptools")

WHEN = datetime(2026, 11, 6, 10, 30)
MSG_A = "aaaaaaaaaaaaaaaaaaaaaaaa one"
MSG_B = "aaaaaaaaaaaaaaaaaaaaaaaa two"


def test_same_prefix_messages_get_distinct_names():
    name_a = gap.task_name_for(MSG_A, WHEN)
    name_b = gap.task_name_for(MSG_B, WHEN)
    assert name_a != name_b, (name_a, name_b)
    assert name_a.startswith("ARIA Reminder 202611061030 ")
    assert name_b.startswith("ARIA Reminder 202611061030 ")


def test_same_message_twice_reuses_the_name():
    assert gap.task_name_for(MSG_A, WHEN) == gap.task_name_for(MSG_A, WHEN)
    assert gap.task_name_for("Take your meds!", WHEN) == gap.task_name_for(
        "Take your meds!", WHEN)


def test_os_reminder_same_minute_distinct_scripts(tmp_path, monkeypatch):
    monkeypatch.setattr(gap, "_os_name", lambda: "win32")
    monkeypatch.setattr(gap, "_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(gap, "_run", lambda cmd: (0, "SUCCESS"))
    future = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    out_a = gap.tool_os_reminder({"message": MSG_A, "at": future})
    out_b = gap.tool_os_reminder({"message": MSG_B, "at": future})
    assert out_a.startswith("status=done"), out_a
    assert out_b.startswith("status=done"), out_b
    scripts = sorted((tmp_path / "os_reminders").glob("*.ps1"))
    assert len(scripts) == 2, [s.name for s in scripts]
    texts = [s.read_text() for s in scripts]
    assert any("one" in t for t in texts)
    assert any("two" in t for t in texts)
    # Both reminders survive: neither script overwrote the other.
    assert len({s.name for s in scripts}) == 2
