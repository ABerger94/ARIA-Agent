"""Pass 28 #1: an explicit null in a journal record must not override
the resumer's live mission policy.

TaskQueue.submit restores the ORIGINAL policy (cap, scope, envelope,
mission) from a resumed journal record. A record carrying
"envelope": null / "spend_cap": null used to override the resumer's
capped mission policy with nothing — a forged record could resume a
task outside its mission's cap. Null now counts as absent: only a
non-null value restores the original policy.
"""
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_resume_null_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg

import importlib  # noqa: E402

import pytest  # noqa: E402

config = importlib.import_module("aria.config")
taskstore = importlib.import_module("aria.agent.taskstore")
taskqueue = importlib.import_module("aria.agent.taskqueue")
runtime = importlib.import_module("aria.runtime")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    return tmp_path


def _forge_record(task_id, **overrides):
    fields = dict(status="blocked", spend_cap=None, budget_scope="",
                  envelope=None, mission_id=None,
                  parent_execution_id="")
    fields.update(overrides)
    return taskstore.create(task_id, "old goal", **fields)


def _resumer_ctx():
    return runtime.execution(
        kind="tool", spend_cap=5.0, budget_scope="mission:9",
        envelope={"mission_id": 9, "tools": ["write_file"]})


def test_null_policy_fields_keep_resumer_policy(ws):
    _forge_record("old-null")
    queue = taskqueue.TaskQueue()
    with _resumer_ctx():
        new_id = queue.submit("resumed goal", resume_from="old-null")
    task = queue._tasks[new_id]
    assert task.spend_cap == 5.0
    assert task.budget_scope == "mission:9"
    assert task.envelope == {"mission_id": 9, "tools": ["write_file"]}
    assert task.mission_id == 9


def test_real_policy_values_restore_the_original(ws):
    _forge_record("old-real", spend_cap=2.0, budget_scope="mission:3",
                  envelope={"mission_id": 3, "tools": ["read_file"]},
                  mission_id=3)
    queue = taskqueue.TaskQueue()
    with _resumer_ctx():
        new_id = queue.submit("resumed goal", resume_from="old-real")
    task = queue._tasks[new_id]
    assert task.spend_cap == 2.0
    assert task.budget_scope == "mission:3"
    assert task.envelope == {"mission_id": 3, "tools": ["read_file"]}
    assert task.mission_id == 3


def test_legacy_record_without_policy_keys_uses_resumer(ws):
    _forge_record("old-legacy")
    data = taskstore._load()
    for key in ("spend_cap", "budget_scope", "envelope", "mission_id",
                "parent_execution_id"):
        data["old-legacy"].pop(key, None)
    taskstore._save(data)
    queue = taskqueue.TaskQueue()
    with _resumer_ctx():
        new_id = queue.submit("resumed goal", resume_from="old-legacy")
    task = queue._tasks[new_id]
    assert task.spend_cap == 5.0
    assert task.envelope == {"mission_id": 9, "tools": ["write_file"]}
    assert task.mission_id == 9
