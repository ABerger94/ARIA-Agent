"""Pass 26 B1#4 — resuming delegated work keeps its original mission policy.

Pins:
  * a task submitted under a mission context (spend cap, budget
    scope, envelope) has that policy persisted in the journal;
  * resuming it from ordinary conversation restores the RECORDED
    policy instead of capturing the resumer's context;
  * a legacy journal record (written before the policy fields
    existed) still resumes, falling back to the resumer's context.
"""
import importlib
import os
import sys
import tempfile
import time
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

_cfg = importlib.import_module("aria.config")
_memory = importlib.import_module("aria.memory")
_memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_taskstore_policy_"), "test.db")
_memory.init_databases()
_memory._embed = lambda text: None

for _name in ("aria.scheduler", "aria.vision", "aria.hardware",
              "aria.spotify", "aria.hud"):
    sys.modules[_name] = types.ModuleType(_name)
sys.modules["aria.vision"].get_face_frame_jpeg = lambda: None
sys.modules["aria.vision"].publish_phone_frame = lambda jpeg: True
sys.modules["aria.vision"].get_phone_frame_jpeg = lambda: None
sys.modules["aria.vision"].get_phone_frame_status = lambda: {"active": False, "age_s": -1.0}
sys.modules["aria.vision"].describe_phone_view = lambda q="": "[stubbed]"
sys.modules["aria.hud"].tool_show_commands = lambda: "ok"
sys.modules["aria.hud"].tool_hide_commands = lambda: "ok"

_cfg.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-taskstore-policy-ws-")

taskstore = importlib.import_module("aria.agent.taskstore")
taskexec = importlib.import_module("aria.agent.taskexec")
taskqueue = importlib.import_module("aria.agent.taskqueue")
runtime = importlib.import_module("aria.runtime")

_PLAN = {"goal": "g", "steps": [
    {"step": 1, "tool": "web_search", "description": "first",
     "parameters": {}, "critical": False},
]}


class _BlockingExecutor:
    def execute(self, goal, speak=None, cancel_flag=None, **kw):
        state = {"plan": _PLAN, "completed": [], "results": {}}
        ck = kw.get("on_checkpoint")
        if ck:
            ck(state)
        raise taskexec.TaskBlocked("needs the user", state)


def _wait_status(q, tid, want, timeout=10):
    end = time.time() + timeout
    st = None
    while time.time() < end:
        st = q.get_status(tid)
        if st and st["status"] == want:
            return st
        time.sleep(0.05)
    return st


def test_resume_restores_original_mission_policy():
    q = taskqueue.TaskQueue(max_concurrent=2,
                            executor_factory=lambda: _BlockingExecutor())
    q.start()
    try:
        env = {"allowed_tools": ["fake_note"], "spend_cap": 12.5,
               "pre_approved_irreversible": [], "mission_id": 7,
               "goal": "mission work"}
        with runtime.execution(
                "mission", execution_id="mission:default:7",
                budget_scope="mission:7", spend_cap=12.5, envelope=env):
            tid = q.submit("mission work")
        st = _wait_status(q, tid, "blocked")
        assert st and st["status"] == "blocked", st
        rec = taskstore.get(tid)
        assert rec["spend_cap"] == 12.5, rec
        assert rec["budget_scope"] == "mission:7", rec
        assert (rec.get("envelope") or {}).get("mission_id") == 7, rec
        # Resume from ordinary conversation (no cap, no envelope).
        with runtime.execution("tool", execution_id="conv-1"):
            new_id = q.submit("", resume_from=tid)
        task = q._tasks[new_id]
        assert task.spend_cap == 12.5, task
        assert task.budget_scope == "mission:7", task
        assert (task.envelope or {}).get("mission_id") == 7, task
        # The resumed run re-persists the ORIGINAL policy too.
        st = _wait_status(q, new_id, "blocked")
        assert st and st["status"] == "blocked", st
        rec2 = taskstore.get(new_id)
        assert rec2["spend_cap"] == 12.5, rec2
        assert rec2["budget_scope"] == "mission:7", rec2
        assert (rec2.get("envelope") or {}).get("mission_id") == 7, rec2
    finally:
        q.stop()
    print("ok resume_restores_original_mission_policy")


def test_legacy_record_resumes_with_resumer_context():
    q = taskqueue.TaskQueue(max_concurrent=2,
                            executor_factory=lambda: _BlockingExecutor())
    q.start()
    try:
        # A record written before the policy fields existed.
        taskstore.create("legacy1", "old work")
        taskstore.finish("legacy1", "blocked",
                         blocked_reason="old park")
        data = taskstore._load()
        for k in ("spend_cap", "budget_scope", "envelope",
                  "mission_id", "parent_execution_id"):
            data["legacy1"].pop(k, None)
        taskstore._save(data)
        assert "spend_cap" not in taskstore.get("legacy1")
        env = {"allowed_tools": [], "spend_cap": 9.0,
               "pre_approved_irreversible": [], "mission_id": 99,
               "goal": "resumer mission"}
        with runtime.execution(
                "mission", execution_id="mission:default:99",
                budget_scope="mission:99", spend_cap=9.0, envelope=env):
            new_id = q.submit("", resume_from="legacy1")
        task = q._tasks[new_id]
        assert task.spend_cap == 9.0, task
        assert task.budget_scope == "mission:99", task
        assert (task.envelope or {}).get("mission_id") == 99, task
    finally:
        q.stop()
    print("ok legacy_record_resumes_with_resumer_context")


if __name__ == "__main__":
    test_resume_restores_original_mission_policy()
    test_legacy_record_resumes_with_resumer_context()
    print("ALL TASKSTORE POLICY CHECKS PASSED")
