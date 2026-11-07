"""Pass 27 #16: deferred work must keep its originating mission policy.

Before: schedules, triggers and autonomous goals persisted only a
prompt — their later execution started from the scheduler/poller
context with no envelope, spend cap or parent, so a trigger
created under a capped mission fired uncapped. Now creation
snapshots the originating policy (mission envelope, budget scope,
spend cap) and the firing restores it; legacy records with no
policy behave exactly as before.

Pinned here: the snapshot under a mission execution, the restore
through origin_policy, goal persistence + proactive restore,
trigger persistence + auto-fire capture via taskqueue.submit,
and scheduler persistence + _fire_scheduled restore.
"""
import json
import os
import sqlite3
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

import pytest

from aria import runtime
from aria import memory
from aria import scheduler
from aria import triggers
from aria.agent import proactive
from aria.agent import taskqueue


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    db = str(tmp_path / "aria_memory.db")
    monkeypatch.setattr(memory, "DB_PATH", db)
    monkeypatch.setattr(scheduler, "DB_PATH", db)
    memory.init_databases()
    return tmp_path


MISSION_ENV = {"allowed_tools": ["write_file"], "spend_cap": 25.0,
               "pre_approved_irreversible": [], "mission_id": 7,
               "goal": "test mission"}


def _mission_ctx():
    return runtime.execution(
        'mission', execution_id='mission:default:7',
        budget_scope='mission:7', spend_cap=25.0,
        envelope=dict(MISSION_ENV))


def test_snapshot_under_mission():
    with _mission_ctx():
        snap = runtime.origin_policy_snapshot()
    assert snap["mission_id"] == 7, snap
    assert snap["budget_scope"] == "mission:7", snap
    assert snap["spend_cap"] == 25.0, snap
    assert snap["envelope"]["allowed_tools"] == ["write_file"], snap


def test_snapshot_outside_mission_is_empty():
    snap = runtime.origin_policy_snapshot()
    assert snap.get("envelope") is None, snap
    assert snap.get("spend_cap") is None, snap


def test_origin_policy_restores():
    snap = {"envelope": dict(MISSION_ENV), "mission_id": 7,
            "budget_scope": "mission:7", "spend_cap": 25.0}
    with runtime.origin_policy(snap, kind='scheduled'):
        ctx = runtime.current()
        assert ctx.envelope == MISSION_ENV, ctx.envelope
        assert ctx.budget_scope == "mission:7", ctx.budget_scope
        assert ctx.spend_cap == 25.0, ctx.spend_cap
    # Legacy / empty policy restores exactly today's behaviour.
    with runtime.origin_policy(None, kind='scheduled'):
        ctx = runtime.current()
        assert ctx.envelope is None, ctx.envelope
        assert ctx.spend_cap is None, ctx.spend_cap


def test_goal_persists_and_restores_policy(tmpdb, monkeypatch):
    monkeypatch.setattr(proactive, "spine_append", lambda *a, **k: None)
    monkeypatch.setattr(proactive, "add_log", lambda *a, **k: None)
    with _mission_ctx():
        gid = memory.goal_db_create(
            "watch the thing", "do it", interval_s=0, priority=5)
    row = memory.goal_db_list()[0]
    assert row["id"] == gid
    policy = json.loads(row["policy"])
    assert policy["mission_id"] == 7, policy
    assert policy["budget_scope"] == "mission:7", policy
    seen = {}

    def action_fn(kind, text):
        ctx = runtime.current()
        seen["envelope"] = ctx.envelope
        seen["scope"] = ctx.budget_scope
        seen["cap"] = ctx.spend_cap
        return "done"

    proactive.evaluate_autonomous_goals(action_fn=action_fn)
    assert seen["envelope"] == MISSION_ENV, seen
    assert seen["scope"] == "mission:7", seen
    assert seen["cap"] == 25.0, seen


def test_trigger_persists_and_fire_captures_policy(
        tmp_path, monkeypatch):
    monkeypatch.setattr(triggers, "STORE_DIR", str(tmp_path))
    with _mission_ctx():
        ok = triggers.create_trigger(
            "Watch", "folder", "file the report",
            rule={}, mode="auto")
    assert ok["ok"], ok
    stored = triggers.load_triggers()[0]
    assert stored["policy"]["mission_id"] == 7, stored["policy"]
    out = triggers._default_fire(stored, "folder: x")
    assert out.startswith("delegated as task "), out
    tid = out.rsplit(" ", 1)[-1]
    task = taskqueue.get_queue()._tasks[tid]
    assert task.envelope == MISSION_ENV, task.envelope
    assert task.budget_scope == "mission:7", task.budget_scope
    assert task.spend_cap == 25.0, task.spend_cap
    assert task.mission_id == 7, task.mission_id


def test_scheduler_persists_and_fire_restores(tmpdb, monkeypatch):
    monkeypatch.setattr(scheduler, "add_log", lambda *a, **k: None)
    with _mission_ctx():
        tid = scheduler.sched_add(
            "interval", "check the thing", interval_s=3600)
    conn = sqlite3.connect(str(tmpdb / "aria_memory.db"))
    row = conn.execute(
        "SELECT policy FROM scheduled_tasks WHERE id = ?",
        (tid,)).fetchone()
    conn.close()
    policy = json.loads(row[0])
    assert policy["mission_id"] == 7, policy
    seen = {}

    def run_agent_fn(text):
        ctx = runtime.current()
        seen["envelope"] = ctx.envelope
        seen["scope"] = ctx.budget_scope
        seen["cap"] = ctx.spend_cap

    scheduler._fire_scheduled(tid, "check the thing", row[0],
                              run_agent_fn)
    assert seen["envelope"] == MISSION_ENV, seen
    assert seen["scope"] == "mission:7", seen
    assert seen["cap"] == 25.0, seen


def test_legacy_rows_without_policy_run_as_before(tmpdb, monkeypatch):
    monkeypatch.setattr(proactive, "spine_append", lambda *a, **k: None)
    monkeypatch.setattr(proactive, "add_log", lambda *a, **k: None)
    gid = memory.goal_db_create("legacy", "old goal")
    conn = sqlite3.connect(str(tmpdb / "aria_memory.db"))
    conn.execute("UPDATE autonomous_goals SET policy = NULL WHERE id = ?",
                 (gid,))
    conn.commit()
    conn.close()
    seen = {}

    def action_fn(kind, text):
        ctx = runtime.current()
        seen["envelope"] = ctx.envelope
        seen["cap"] = ctx.spend_cap
        return "done"

    proactive.evaluate_autonomous_goals(action_fn=action_fn)
    assert seen["envelope"] is None, seen
    assert seen["cap"] is None, seen
