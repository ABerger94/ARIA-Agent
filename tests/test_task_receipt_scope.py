"""Pass 26 B1#5 — delegated receipt identity must not depend on budget scope.

Pins:
  * the same plan step executed under a mission budget scope and
    then resumed under an ordinary scope produces the SAME
    operation id (built from the stable execution scope, not the
    volatile budget scope);
  * a re-run under that same id hits the action journal's cached /
    UNVERIFIED path instead of re-executing, while a different id
    grants execution (the duplicate-effect shape the bug allowed).
"""
import importlib
import os
import sys
import tempfile
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
    tempfile.mkdtemp(prefix="aria_task_receipt_"), "test.db")
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

_cfg.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-task-receipt-ws-")

taskexec = importlib.import_module("aria.agent.taskexec")
runtime = importlib.import_module("aria.runtime")
action_journal = importlib.import_module("aria.action_journal")

_PLAN = {"goal": "g", "steps": [
    # Pass 36 #26: steps carry stable ids; the op id binds the
    # id, not the display number.
    {"id": "step-1", "step": 1, "tool": "web_search",
     "description": "first", "parameters": {}, "critical": False},
    {"id": "step-2", "step": 2, "tool": "read_notes",
     "description": "second", "parameters": {"x": "{prev}"},
     "critical": False},
]}


def _executor(calls):
    def runner(name, args):
        calls.append((name, runtime.operation_id()))
        # Step 1 returns the same value in both runs, so step 2's
        # {prev}-substituted params (and hence its signature) are
        # identical — only the scope may differ, which is what the
        # test isolates.
        return "A-OUT" if name == "web_search" else "OUT-" + name
    return taskexec.AgentExecutor(
        tool_runner=runner,
        summarizer=lambda goal, completed: "SUMMARY-OK",
        sleep_fn=lambda s: None)


def test_same_step_same_operation_id_across_resume():
    calls = []
    ex = _executor(calls)
    with runtime.execution("task", execution_id="task:orig123",
                           budget_scope="mission:7"):
        out = ex.execute("g", resume={"plan": _PLAN, "completed": [],
                                      "results": {}})
    assert out == "SUMMARY-OK", out
    first = dict(calls)
    assert set(first) == {"web_search", "read_notes"}, calls
    # Pass 36 #26: the receipt binds the stable step id, not
    # the display number — same scope, same step, same id.
    assert first["read_notes"].startswith(
        "task:orig123:step:step-2:"), first
    assert "mission:7" not in first["read_notes"], first

    calls.clear()
    ex2 = _executor(calls)
    resume = {"plan": _PLAN, "completed": [_PLAN["steps"][0]],
              "results": {"step-1": "A-OUT"},
              "execution_scope": "task:orig123"}
    with runtime.execution("task", execution_id="task:new456",
                           budget_scope=""):
        out2 = ex2.execute("g", resume=resume)
    assert out2 == "SUMMARY-OK", out2
    assert [c[0] for c in calls] == ["read_notes"], calls  # step 1 skipped
    assert calls[0][1] == first["read_notes"], (calls, first)
    print("ok same_step_same_operation_id_across_resume")


def test_stable_id_hits_journal_cache():
    op = ("task:orig123:step:2:"
          + action_journal.signature("read_notes", {"x": "A-OUT"}))
    assert action_journal.begin(op, "read_notes", {"x": "A-OUT"}) is None
    action_journal.finish(op, "DONE-OUT")
    cached = action_journal.begin(op, "read_notes", {"x": "A-OUT"})
    assert cached is not None, "same id must not grant a second execution"
    assert cached.status == "done", cached
    assert "DONE-OUT" in str(cached), cached
    # A different id (the old budget-scope-derived shape) grants
    # execution again — the duplicate-effect hole.
    other = ("task:new456:step:2:"
             + action_journal.signature("read_notes", {"x": "A-OUT"}))
    assert action_journal.begin(other, "read_notes",
                                {"x": "A-OUT"}) is None
    print("ok stable_id_hits_journal_cache")


if __name__ == "__main__":
    test_same_step_same_operation_id_across_resume()
    test_stable_id_hits_journal_cache()
    print("ALL TASK RECEIPT SCOPE CHECKS PASSED")
