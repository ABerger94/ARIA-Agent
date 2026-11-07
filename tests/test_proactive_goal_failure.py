"""Pass 28 #39: an autonomous goal that errored — or never ran
because no dispatcher was bound — must not be marked completed.

Bug: evaluate_autonomous_goals set status="completed" (one-shot)
for every outcome, including "Goal execution error: ..." and
"Goal evaluated (no action dispatcher bound)." The goal would
never run, and the record claimed it finished.

Fix contract:
- a one-shot goal whose action raised, or with no dispatcher,
  parks as "failed" (not due, so no retry loop) with the error
  as its last_result;
- a successful one-shot still completes;
- a recurring goal still reschedules either way (its next cycle
  retries); only the completion claim changes.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")


def _pkg(name, path):
    m = types.ModuleType(name)
    m.__path__ = [path]
    sys.modules[name] = m
    return m


_pkg("aria", PKG)
_pkg("aria.tools", os.path.join(PKG, "tools"))
_pkg("aria.agent", os.path.join(PKG, "agent"))
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
sys.modules["aria.speech"] = types.ModuleType("aria.speech")
_inbox = types.ModuleType("aria.inbox")
_inbox.INBOX_DIR = "/nonexistent-test-inbox"
sys.modules["aria.inbox"] = _inbox

import pytest  # noqa: E402

from aria import memory  # noqa: E402
from aria import scheduler  # noqa: E402
from aria.agent import proactive  # noqa: E402


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    db = str(tmp_path / "aria_memory.db")
    monkeypatch.setattr(memory, "DB_PATH", db)
    monkeypatch.setattr(scheduler, "DB_PATH", db)
    monkeypatch.setattr(proactive, "spine_append", lambda *a, **k: None)
    monkeypatch.setattr(proactive, "add_log", lambda *a, **k: None)
    memory.init_databases()
    return tmp_path


def _row(gid):
    rows = [r for r in memory.goal_db_list() if r["id"] == gid]
    assert rows, f"goal {gid} missing"
    return rows[0]


def test_errored_one_shot_goal_is_failed_not_completed(tmpdb):
    gid = memory.goal_db_create("boom", "do it", interval_s=0, priority=5)

    def action_fn(kind, text):
        raise RuntimeError("dispatcher blew up")

    proactive.evaluate_autonomous_goals(action_fn=action_fn)
    row = _row(gid)
    assert row["status"] == "failed", row
    assert "Goal execution error" in (row["last_result"] or "")
    # A failed goal is not due again — it must not retry in a loop.
    proactive.evaluate_autonomous_goals(action_fn=action_fn)
    assert _row(gid)["status"] == "failed"


def test_no_dispatcher_one_shot_goal_is_failed_not_completed(tmpdb):
    gid = memory.goal_db_create("orphan", "do it", interval_s=0, priority=5)
    proactive.evaluate_autonomous_goals(action_fn=None)
    row = _row(gid)
    assert row["status"] == "failed", row
    assert "no action dispatcher" in (row["last_result"] or "")


def test_successful_one_shot_goal_still_completes(tmpdb):
    gid = memory.goal_db_create("ok", "do it", interval_s=0, priority=5)
    proactive.evaluate_autonomous_goals(
        action_fn=lambda kind, text: "done")
    row = _row(gid)
    assert row["status"] == "completed", row
    assert row["last_result"] == "done"


def test_errored_recurring_goal_reschedules(tmpdb):
    gid = memory.goal_db_create("retry me", "do it", interval_s=3600,
                                priority=5)

    def action_fn(kind, text):
        raise RuntimeError("transient failure")

    proactive.evaluate_autonomous_goals(action_fn=action_fn)
    row = _row(gid)
    assert row["status"] == "active", row
    assert "Goal execution error" in (row["last_result"] or "")
    assert row["next_run"] > 0


def test_no_dispatcher_recurring_goal_reschedules(tmpdb):
    gid = memory.goal_db_create("retry me", "do it", interval_s=3600,
                                priority=5)
    proactive.evaluate_autonomous_goals(action_fn=None)
    row = _row(gid)
    assert row["status"] == "active", row
    assert "no action dispatcher" in (row["last_result"] or "")
