"""Gap fill 3 — task-engine hardening: journal, checkpoints,
resume, blocked states, verifier.

Pins:
  * taskstore round-trip: create -> checkpoint -> finish, and
    crash recovery (records left 'running' become blocked);
  * executor resume: finished steps are SKIPPED (their tools do
    not run again) while their results still feed {prev};
  * a step result starting 'NEED_INPUT:' parks the task via
    TaskBlocked with the checkpoint attached;
  * the verifier's FAIL parks the task; PASS and unparseable
    verdicts let it finish;
  * queue integration: TaskBlocked lands as status 'blocked' in
    the journal, and submit(resume_from=...) refuses finished
    tasks.
"""
import importlib
import os
import sys
import tempfile
import types

# Same package-stub preamble as tests/test_tasks.py (the modules
# under test import through the real aria.config / planner chain).
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
    tempfile.mkdtemp(prefix="aria_taskstore_"), "test.db")
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

# taskstore writes under config.WORKSPACE_DIR: scratch dir.
_cfg.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-taskstore-ws-")

taskstore = importlib.import_module("aria.agent.taskstore")
taskexec = importlib.import_module("aria.agent.taskexec")
taskqueue = importlib.import_module("aria.agent.taskqueue")


# ------------------------------------------------------------- journal
def test_journal_roundtrip_and_recovery():
    rec = taskstore.create("t1", "do a thing")
    assert rec["status"] == "running"
    taskstore.checkpoint("t1", {
        "plan": {"steps": [{"step": 1}]},
        "completed": [{"step": 1}],
        "results": {1: "done-1"},
    })
    got = taskstore.get("t1")
    assert got["results"] == {"1": "done-1"} and got["checkpoint_at"] > 0
    # simulate a crash: t2 is left 'running'
    taskstore.create("t2", "victim")
    n = taskstore.recover_interrupted()
    assert n == 2  # t1 and t2 both looked running
    assert taskstore.get("t1")["status"] == "blocked"
    assert "interrupted" in taskstore.get("t2")["blocked_reason"]
    assert taskstore.get("t2")["task_id"] in [
        r["task_id"] for r in taskstore.resumable()]
    taskstore.finish("t1", "completed", result="all good")
    assert taskstore.get("t1")["result"] == "all good"
    print("ok journal_roundtrip_and_recovery")


# ------------------------------------------------------------- executor
_PLAN = {"goal": "g", "steps": [
    {"step": 1, "tool": "web_search", "description": "first",
     "parameters": {}, "critical": False},
    {"step": 2, "tool": "read_notes", "description": "second",
     "parameters": {"x": "{prev}"}, "critical": False},
]}


def _executor(calls, outcomes, **kw):
    def runner(name, args):
        calls.append((name, dict(args or {})))
        out = outcomes[name]
        return out.pop(0) if len(out) > 1 else out[0]
    kw.setdefault("summarizer", lambda goal, completed: "SUMMARY-OK")
    kw.setdefault("sleep_fn", lambda s: None)
    return taskexec.AgentExecutor(tool_runner=runner, **kw)


def test_resume_skips_completed_steps():
    calls = []
    ex = _executor(calls, {"web_search": ["A-OUT"], "read_notes": ["B-OUT"]})
    resume = {"plan": _PLAN,
              "completed": [_PLAN["steps"][0]],
              "results": {"1": "A-OUT"}}
    out = ex.execute("g", resume=resume)
    assert out == "SUMMARY-OK"
    assert [c[0] for c in calls] == ["read_notes"], calls  # step 1 skipped
    assert calls[0][1]["x"] == "A-OUT"        # {prev} still fed
    print("ok resume_skips_completed_steps")


def test_need_input_parks_with_checkpoint():
    calls, states = [], []
    ex = _executor(calls, {"web_search": ["first-ok"],
                           "read_notes": ["NEED_INPUT: which account?"]})
    try:
        ex.execute("g", resume={"plan": _PLAN, "completed": [],
                                "results": {}},
                   on_checkpoint=states.append)
        raise AssertionError("expected TaskBlocked")
    except taskexec.TaskBlocked as b:
        assert "which account" in b.reason
        assert [s["step"] for s in b.state["completed"]] == [1]
        assert b.state["results"][1] == "first-ok"
    assert states and states[-1]["results"][1] == "first-ok"
    print("ok need_input_parks_with_checkpoint")


def test_verifier_fail_parks_pass_and_garbage_do_not():
    calls = []
    ex = _executor(calls, {"web_search": ["a"], "read_notes": ["b"]},
                   verifier=lambda g, c, r: "results look fabricated")
    try:
        ex.execute("g", resume={"plan": _PLAN, "completed": [],
                                "results": {}}, verify=True)
        raise AssertionError("expected TaskBlocked")
    except taskexec.TaskBlocked as b:
        assert b.reason.startswith("verifier:")

    ex2 = _executor([], {"web_search": ["a"], "read_notes": ["b"]},
                    verifier=lambda g, c, r: None)
    assert ex2.execute("g", resume={"plan": _PLAN, "completed": [],
                                    "results": {}}, verify=True) \
        == "SUMMARY-OK"

    # LLM-shaped verdicts: FAIL parsed, nonsense passes open.
    ex3 = _executor([], {"web_search": ["a"], "read_notes": ["b"]},
                    llm=lambda prompt, system=None:
                        "VERDICT: FAIL — no evidence")
    try:
        ex3.execute("g", resume={"plan": _PLAN, "completed": [],
                                 "results": {}}, verify=True)
        raise AssertionError("expected TaskBlocked")
    except taskexec.TaskBlocked as b:
        assert "no evidence" in b.reason
    ex4 = _executor([], {"web_search": ["a"], "read_notes": ["b"]},
                    llm=lambda prompt, system=None: "whatever, ship it")
    assert ex4.execute("g", resume={"plan": _PLAN, "completed": [],
                                    "results": {}}, verify=True) \
        == "SUMMARY-OK"
    print("ok verifier_fail_parks_pass_and_garbage_do_not")


# ------------------------------------------------------------- queue
class _BlockingExecutor:
    def execute(self, goal, speak=None, cancel_flag=None, **kw):
        raise taskexec.TaskBlocked(
            "needs the user",
            {"plan": _PLAN, "completed": [], "results": {}})


def test_queue_blocked_and_resume_refusal():
    q = taskqueue.TaskQueue(executor_factory=lambda: _BlockingExecutor())
    q.start()
    tid = q.submit("park me")
    import time
    for _ in range(100):
        st = q.get_status(tid)
        if st and st["status"] == "blocked":
            break
        time.sleep(0.05)
    assert st["status"] == "blocked", st
    assert "needs the user" in st["error"]
    rec = taskstore.get(tid)
    assert rec["status"] == "blocked"
    # a completed record refuses resume
    taskstore.create("done1", "finished business")
    taskstore.finish("done1", "completed", result="ok")
    try:
        q.submit("", resume_from="done1")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    # and the blocked one accepts it
    new_id = q.submit("", resume_from=tid)
    assert new_id != tid
    q.stop()
    print("ok queue_blocked_and_resume_refusal")


if __name__ == "__main__":
    test_journal_roundtrip_and_recovery()
    test_resume_skips_completed_steps()
    test_need_input_parks_with_checkpoint()
    test_verifier_fail_parks_pass_and_garbage_do_not()
    test_queue_blocked_and_resume_refusal()
    print("ALL TASKSTORE/HARDENING CHECKS PASSED")
