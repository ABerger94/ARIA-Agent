"""Unit tests for Merger Phase 3 — aria/agent/taskqueue.py,
planner.py, taskexec.py + the delegate_task/task_status/task_cancel
tool wiring (blueprint SS2.1, SS3.3).

Everything is local and deterministic: the planner's LLM is a stub,
step tools are scripted fakes registered in the REAL dispatch
registry (so steps run through the real execute_tool), the queue's
executor is injected, and the memory DB is a temp file. Verifies
the caps on the record: plan clamped to 5, step attempts capped at
3, replans capped at 2, unknown tool = hard abort, 600 s budget,
queue concurrency 1, priority order, cancel.

Run:  python3 tests/_run_suite.py tests/test_tasks.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import types

import pytest

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

import importlib

config = importlib.import_module("aria.config")
schemas = importlib.import_module("aria.tools.schemas")
sandbox = importlib.import_module("aria.tools.sandbox")
memory = importlib.import_module("aria.memory")
memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_tasks_"), "test.db")
memory.init_databases()
memory._embed = lambda text: None

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

builtins = importlib.import_module("aria.tools.builtins")
dispatch = importlib.import_module("aria.tools.dispatch")

_speech = types.ModuleType("aria.speech")
_SPOKEN = []
_speech.speak = lambda text: _SPOKEN.append(text)
sys.modules["aria.speech"] = _speech

failures = importlib.import_module("aria.agent.failures")
planner = importlib.import_module("aria.agent.planner")
taskqueue = importlib.import_module("aria.agent.taskqueue")
taskexec = importlib.import_module("aria.agent.taskexec")


# ---------------------------------------------------------------- helpers
class ScriptedTool:
    """A dispatch-registry tool with scripted outcomes (strings are
    returned; the last outcome repeats)."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, args):
        self.calls.append(dict(args or {}))
        if len(self.outcomes) > 1:
            return self.outcomes.pop(0)
        return self.outcomes[0]


def _register(name, tool):
    dispatch.register_tool(name, tool, replay_safe=True)
    return tool


def _executor(llm=None, **kw):
    kw.setdefault("summarizer", lambda goal, completed: "SUMMARY-OK")
    kw.setdefault("sleep_fn", lambda s: None)
    return taskexec.AgentExecutor(llm=llm, **kw)


def _plan_json(steps, goal="g"):
    return json.dumps({"goal": goal, "steps": steps})


def _step(tool, critical=False, params=None, desc="do it"):
    return {"step": 1, "tool": tool, "description": desc,
            "parameters": params or {}, "critical": critical}


# ---------------------------------------------------------------- planner
def test_planner_clamps_to_five_steps():
    _register("fake_ok", ScriptedTool(["ok-output"]))
    steps = [_step("fake_ok") for _ in range(7)]
    plan = planner.create_plan("g", llm=lambda p, s: _plan_json(steps))
    assert len(plan["steps"]) == 5, plan
    assert any("clamped" in w for w in plan["warnings"]), plan["warnings"]
    assert [s["step"] for s in plan["steps"]] == [1, 2, 3, 4, 5]
    print("ok planner_clamps_to_five_steps")


def test_planner_drops_unknown_tools():
    _register("fake_ok", ScriptedTool(["ok-output"]))
    steps = [_step("fake_ok"), _step("nope_tool_xyz")]
    plan = planner.create_plan("g", llm=lambda p, s: _plan_json(steps))
    assert [s["tool"] for s in plan["steps"]] == ["fake_ok"], plan
    assert any("unknown tool" in w for w in plan["warnings"])
    print("ok planner_drops_unknown_tools")


def test_planner_fallbacks():
    # Pass 36 #33: a non-research goal has no safe fallback —
    # a planning failure is a typed error, not a web search of
    # the raw goal (which would exfiltrate a private goal to a
    # search backend and let a search result certify work never
    # done).
    with pytest.raises(planner.PlanningFailed):
        planner.create_plan("g", llm=lambda p, s: "not json at all")

    def _boom(p, s):
        raise RuntimeError("pool down")
    with pytest.raises(planner.PlanningFailed):
        planner.create_plan("g", llm=_boom)

    # An explicitly research-shaped goal keeps the scoped search
    # fallback — with the framing verb stripped, so the raw goal
    # is never the query.
    research = planner.create_plan(
        "research the best laptop under $1000",
        llm=lambda p, s: "not json at all")
    assert len(research["steps"]) == 1
    assert research["steps"][0]["tool"] == "web_search"
    assert research["steps"][0]["critical"] is True
    assert research["steps"][0]["parameters"]["query"] == \
        "the best laptop under $1000", research
    assert research["task_type"] == "research"
    print("ok planner_fallbacks")


# ---------------------------------------------------------------- executor
def test_executor_happy_path_and_substitution():
    tool = _register("fake_ok", ScriptedTool(["ok-output"]))
    steps = [_step("fake_ok", params={"x": "first"}),
             _step("fake_ok", params={"x": "{prev}"})]
    ex = _executor(llm=lambda p, s: _plan_json(steps))
    spoken = []
    out = ex.execute("goal", speak=spoken.append)
    assert out == "SUMMARY-OK", out
    assert spoken == ["SUMMARY-OK"], spoken
    assert tool.calls[0]["x"] == "first"
    assert tool.calls[1]["x"] == "ok-output", tool.calls
    print("ok executor_happy_path_and_substitution")


def test_executor_retries_transient_then_succeeds():
    tool = _register("fake_flaky", ScriptedTool([
        "[Tool Error: HTTP 503 service unavailable]",
        "[Tool Error: HTTP 503 service unavailable]",
        "recovered",
    ]))
    steps = [_step("fake_flaky", critical=True)]
    ex = _executor(llm=lambda p, s: _plan_json(steps))
    out = ex.execute("goal")
    assert out == "SUMMARY-OK", out
    assert len(tool.calls) == 3, tool.calls
    print("ok executor_retries_transient_then_succeeds")


def test_executor_attempt_cap_stops_at_three():
    tool = _register("fake_always503", ScriptedTool(
        ["[Tool Error: HTTP 503 service unavailable]"]))
    steps = [_step("fake_always503", critical=True)]
    ex = _executor(llm=lambda p, s: _plan_json(steps))
    out = ex.execute("goal")
    # Attempts are capped at STEP_MAX_ATTEMPTS per plan incarnation;
    # a critical transient failure escalates retry -> replan, and
    # replans are capped at MAX_REPLAN_ATTEMPTS, so the tool sees
    # exactly 3 attempts x (1 + 2 replans) calls before the abort.
    cap = config.STEP_MAX_ATTEMPTS * (1 + config.MAX_REPLAN_ATTEMPTS)
    assert len(tool.calls) == cap, (len(tool.calls), cap)
    assert "failed despite new approaches" in out, out
    print("ok executor_attempt_cap_stops_at_three")


def test_executor_noncritical_skip_continues():
    _register("fake_broken_nc", ScriptedTool(
        ["[Tool Error: FileNotFoundError: No such file or directory: 'x']"]))
    _register("fake_ok2", ScriptedTool(["done"]))
    steps = [_step("fake_broken_nc", critical=False),
             _step("fake_ok2")]
    ex = _executor(llm=lambda p, s: _plan_json(steps))
    out = ex.execute("goal")
    assert out == "SUMMARY-OK", out
    print("ok executor_noncritical_skip_continues")


def test_executor_critical_replan_cap_then_abort():
    _register("fake_broken_c", ScriptedTool(
        ["[Tool Error: FileNotFoundError: No such file or directory: 'x']"]))
    plan = {"goal": "g", "steps": [_step("fake_broken_c", critical=True)],
            "warnings": []}
    replan_calls = [0]
    orig_create, orig_replan = planner.create_plan, planner.replan
    try:
        planner.create_plan = lambda goal, context="", llm=None: plan
        def _replan(goal, completed, failed, error, llm=None, results=None):
            replan_calls[0] += 1
            return plan
        planner.replan = _replan
        ex = _executor()
        spoken = []
        out = ex.execute("goal", speak=spoken.append)
    finally:
        planner.create_plan, planner.replan = orig_create, orig_replan
    assert replan_calls[0] == config.MAX_REPLAN_ATTEMPTS, replan_calls
    assert "failed despite new approaches" in out, out
    assert spoken and spoken[-1] == out
    # The abort is on the record in the incident store.
    rows = [r for r in memory.incident_db_list(limit=100)
            if r["source"].startswith("taskexec")]
    assert rows, "expected taskexec incident rows"
    print("ok executor_critical_replan_cap_then_abort")


def test_executor_unknown_tool_hard_abort():
    plan = {"goal": "g", "steps": [_step("ghost_tool_xyz")], "warnings": []}
    orig_create = planner.create_plan
    try:
        planner.create_plan = lambda goal, context="", llm=None: plan
        ex = _executor()
        out = ex.execute("goal")
    finally:
        planner.create_plan = orig_create
    assert "don't have" in out, out
    rows = [r for r in memory.incident_db_list(limit=100)
            if r["source"] == "taskexec.step:ghost_tool_xyz"]
    assert rows and rows[0]["action_taken"] == "abort", rows
    print("ok executor_unknown_tool_hard_abort")


def test_executor_budget_abort():
    tool = _register("fake_ok3", ScriptedTool(["ok"]))
    steps = [_step("fake_ok3")]
    saved = config.DELEGATED_TASK_BUDGET_S
    try:
        config.DELEGATED_TASK_BUDGET_S = -1  # already expired
        ex = _executor(llm=lambda p, s: _plan_json(steps))
        out = ex.execute("goal")
    finally:
        config.DELEGATED_TASK_BUDGET_S = saved
    assert "ran too long" in out, out
    assert tool.calls == [], "budget abort must precede any step"
    print("ok executor_budget_abort")


# ------------------------------------------------- approval parking
def _approval_plan(tool):
    plan = {"goal": "g", "plan_rev": "r1", "steps": [
        {"id": "s-1", "step": 1, "tool": tool, "description": "d",
         "parameters": {}, "critical": True}]}
    return plan


def test_executor_approval_parks_with_token():
    # A step parked behind an approval used to ABORT the task,
    # orphaning the live token: approving it later ran the action
    # detached from a task already reported as failed. The task
    # now parks with the token so the resolution can settle it.
    _register("fake_gated", ScriptedTool(
        ["[AWAITING_APPROVAL token=abc123] run_python_code"]))
    steps = [_step("fake_gated", critical=True)]
    ex = _executor(llm=lambda p, s: _plan_json(steps))
    with pytest.raises(taskexec.TaskBlocked) as ei:
        ex.execute("goal")
    pa = ei.value.state["pending_approval"]
    assert pa["token"] == "abc123" and pa["tool"] == "fake_gated", pa
    assert pa["step_id"] == ei.value.state["plan"]["steps"][0]["id"]
    assert "approval" in ei.value.reason and "abc123" in ei.value.reason


def test_executor_resume_settles_from_approved_replay():
    tool = _register("fake_gated2", ScriptedTool(["SHOULD-NOT-RUN"]))
    resume = {"plan": _approval_plan("fake_gated2"), "completed": [],
              "results": {},
              "pending_approval": {
                  "step": 1, "step_id": "s-1", "tool": "fake_gated2",
                  "token": "abc123",
                  "resolution": {"approved": True,
                                 "result": "replayed-ok"}}}
    out = _executor().execute("goal", resume=resume)
    assert out == "SUMMARY-OK", out
    assert tool.calls == [], "the approved replay must not run twice"


def test_executor_resume_after_denial_stops():
    tool = _register("fake_gated3", ScriptedTool(["SHOULD-NOT-RUN"]))
    resume = {"plan": _approval_plan("fake_gated3"), "completed": [],
              "results": {},
              "pending_approval": {
                  "step": 1, "step_id": "s-1", "tool": "fake_gated3",
                  "token": "abc123",
                  "resolution": {"approved": False, "result": "denied"}}}
    out = _executor().execute("goal", resume=resume)
    assert "denied" in out, out
    assert taskexec.normalize(out).status == "failed", out
    assert tool.calls == []


def test_executor_resume_unresolved_approval(monkeypatch):
    tool = _register("fake_gated4", ScriptedTool(["fresh-run"]))
    pending = {"step": 1, "step_id": "s-1", "tool": "fake_gated4",
               "token": "abc123"}
    # Still pending in the store: keep waiting, do not re-run.
    monkeypatch.setattr(taskexec, "_approval_still_pending",
                        lambda t: True)
    with pytest.raises(taskexec.TaskBlocked):
        _executor().execute("goal", resume={
            "plan": _approval_plan("fake_gated4"), "completed": [],
            "results": {}, "pending_approval": dict(pending)})
    assert tool.calls == []
    # Expired with no resolution: it never ran — run it fresh.
    monkeypatch.setattr(taskexec, "_approval_still_pending",
                        lambda t: False)
    out = _executor().execute("goal", resume={
        "plan": _approval_plan("fake_gated4"), "completed": [],
        "results": {}, "pending_approval": dict(pending)})
    assert out == "SUMMARY-OK", out
    assert len(tool.calls) == 1


def test_taskstore_records_approval_resolution():
    taskstore = taskqueue.taskstore
    taskstore.create("tA", "goal")
    taskstore.checkpoint("tA", {
        "plan": _approval_plan("x"), "completed": [], "results": {},
        "pending_approval": {"step_id": "s-1", "token": "aaaa1111"}})
    # Not blocked yet (still parking): recorded, no resume.
    assert taskstore.resolve_pending_approval(
        "aaaa1111", True, "ok") is None
    assert taskstore.get("tA")["pending_approval"]["resolution"] == \
        {"approved": True, "result": "ok"}
    # A checkpoint written after the tap keeps the resolution.
    taskstore.checkpoint("tA", {
        "plan": _approval_plan("x"), "completed": [], "results": {},
        "pending_approval": {"step_id": "s-1", "token": "aaaa1111"}})
    assert "resolution" in taskstore.get("tA")["pending_approval"]

    taskstore.create("tB", "goal")
    taskstore.checkpoint("tB", {
        "plan": _approval_plan("x"), "completed": [], "results": {},
        "pending_approval": {"step_id": "s-1", "token": "bbbb2222"}})
    taskstore.finish("tB", "blocked", blocked_reason="waiting")
    # A replay that re-parked moves the wait to the new token.
    assert taskstore.resolve_pending_approval(
        "bbbb2222", True,
        "[AWAITING_APPROVAL token=cccc3333] again") is None
    assert taskstore.get("tB")["pending_approval"]["token"] == "cccc3333"
    # The final answer resolves the blocked task and names it.
    assert taskstore.resolve_pending_approval(
        "cccc3333", True, "done") == "tB"
    assert taskstore.resolve_pending_approval("nope", True, "x") is None


# ------------------------------------------------- NEED_INPUT
def test_executor_need_input_reason_strips_prefix():
    _register("fake_ask", ScriptedTool(["NEED_INPUT: which folder?"]))
    steps = [_step("fake_ask", critical=True)]
    ex = _executor(llm=lambda p, s: _plan_json(steps))
    with pytest.raises(taskexec.TaskBlocked) as ei:
        ex.execute("goal")
    assert ei.value.reason == "which folder?", ei.value.reason


# ------------------------------------------------- budget mid-step
def test_executor_budget_bounds_a_hung_step():
    def _slow(args):
        time.sleep(4.0)
        return "late"
    dispatch.register_tool("fake_slow", _slow, replay_safe=True)
    steps = [_step("fake_slow", critical=True)]
    saved = config.DELEGATED_TASK_BUDGET_S
    config.DELEGATED_TASK_BUDGET_S = 1  # _cap() reads whole seconds
    try:
        ex = _executor(llm=lambda p, s: _plan_json(steps))
        t0 = time.time()
        with pytest.raises(taskexec.TaskBlocked) as ei:
            ex.execute("goal")
        assert time.time() - t0 < 3.0, "the budget must bound the step"
    finally:
        config.DELEGATED_TASK_BUDGET_S = saved
    assert "still running" in ei.value.reason, ei.value.reason


# ------------------------------------------------- replan numbering
def test_replan_numbers_after_completed_and_sees_results():
    _register("fake_first", ScriptedTool(["first-output"]))
    _register("fake_breaks", ScriptedTool(
        ["[Tool Error: wrong approach, try something else]"]))
    later = _register("fake_later", ScriptedTool(["later-output"]))
    prompts = []

    def _llm(prompt, system):
        prompts.append(prompt)
        if len(prompts) == 1:
            return _plan_json([_step("fake_first"),
                               _step("fake_breaks", critical=True)])
        return _plan_json([_step("fake_later",
                                 params={"x": "{step1}"})])
    ex = _executor(llm=_llm)
    out = ex.execute("goal")
    assert out == "SUMMARY-OK", out
    replan_prompt = prompts[1]
    assert "result: first-output" in replan_prompt, replan_prompt
    assert "Number the new steps from 2" in replan_prompt
    # {step1} names the completed step, not the replacement
    assert later.calls[0]["x"] == "first-output", later.calls


def test_planner_replan_numbering():
    _register("fake_ok", ScriptedTool(["ok"]))
    done = [{"id": "a", "step": 1, "tool": "fake_ok"},
            {"id": "b", "step": 2, "tool": "fake_ok"}]
    plan = planner.replan("g", done, {"tool": "fake_ok"}, "err",
                          llm=lambda p, s: _plan_json(
                              [_step("fake_ok"), _step("fake_ok")]),
                          results={"a": "r1", "b": "r2"})
    assert [s["step"] for s in plan["steps"]] == [3, 4], plan


# ------------------------------------------------- continuations
def test_planner_drops_steps_past_a_checkpoint():
    _register("fake_ok", ScriptedTool(["ok"]))
    cp = dict(_step("fake_ok"), checkpoint=True)
    plan = planner.create_plan("g", llm=lambda p, s: _plan_json(
        [_step("fake_ok"), cp, _step("fake_ok")]))
    assert len(plan["steps"]) == 2 and plan["steps"][-1]["checkpoint"]
    assert any("checkpoint" in w for w in plan["warnings"])


def test_executor_continues_after_checkpoint_step():
    _register("fake_list", ScriptedTool(["a.txt, b.txt"]))
    act = _register("fake_act", ScriptedTool(["acted"]))
    prompts = []

    def _llm(prompt, system):
        prompts.append(prompt)
        if len(prompts) == 1:
            return _plan_json([dict(_step("fake_list"), checkpoint=True)])
        return _plan_json([_step("fake_act", params={"files": "{step1}"})])
    out = _executor(llm=_llm).execute("goal")
    assert out == "SUMMARY-OK", out
    assert len(prompts) == 2 and "Completed so far" in prompts[1]
    assert "result: a.txt, b.txt" in prompts[1]
    assert act.calls == [{"files": "a.txt, b.txt"}], act.calls


def test_executor_empty_continuation_finishes():
    _register("fake_list2", ScriptedTool(["nothing to do"]))
    calls = []

    def _llm(prompt, system):
        calls.append(prompt)
        if len(calls) == 1:
            return _plan_json([dict(_step("fake_list2"), checkpoint=True)])
        return json.dumps({"goal": "g", "steps": []})
    out = _executor(llm=_llm).execute("goal")
    assert out == "SUMMARY-OK", out
    assert len(calls) == 2


def test_executor_continuations_are_capped():
    _register("fake_loop", ScriptedTool(["more"]))
    calls = []

    def _llm(prompt, system):
        calls.append(prompt)
        return _plan_json([dict(_step("fake_loop"), checkpoint=True)])
    out = _executor(llm=_llm).execute("goal")
    assert out == "SUMMARY-OK", out
    assert len(calls) == 1 + config.MAX_PLAN_CONTINUATIONS, len(calls)


# ---------------------------------------------------------------- queue
class _FakeExecutor:
    def __init__(self):
        self.executed = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()
        self.blocker = None  # threading.Event to hold a task open

    def execute(self, goal, speak=None, cancel_flag=None):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.blocker is not None:
                self.blocker.wait(timeout=10)
            self.executed.append(goal)
            return f"done: {goal}"
        finally:
            with self._lock:
                self.active -= 1


def _wait_for(pred, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_queue_priority_order_and_concurrency():
    fake = _FakeExecutor()
    fake.blocker = threading.Event()
    q = taskqueue.TaskQueue(executor_factory=lambda: fake)
    q.start()
    try:
        q.submit("blocker", priority=taskqueue.TaskPriority.NORMAL)
        assert _wait_for(lambda: fake.active == 1)
        q.submit("low-job", priority=taskqueue.TaskPriority.LOW)
        q.submit("normal-job", priority=taskqueue.TaskPriority.NORMAL)
        q.submit("high-job", priority=taskqueue.TaskPriority.HIGH)
        fake.blocker.set()
        assert _wait_for(lambda: len(fake.executed) == 4)
        order = fake.executed[1:]
        assert order == ["high-job", "normal-job", "low-job"], order
        assert fake.max_active == 1, fake.max_active
    finally:
        q.stop()
    print("ok queue_priority_order_and_concurrency")


def test_queue_cancel_and_status():
    fake = _FakeExecutor()
    fake.blocker = threading.Event()
    q = taskqueue.TaskQueue(executor_factory=lambda: fake)
    q.start()
    try:
        q.submit("blocker")
        assert _wait_for(lambda: fake.active == 1)
        tid = q.submit("victim")
        assert q.cancel(tid) is True
        fake.blocker.set()
        assert _wait_for(
            lambda: q.get_status(tid)["status"] == "cancelled")
        assert "victim" not in fake.executed
        assert q.cancel("no-such-id") is False
        assert q.get_status("no-such-id") is None
        st = q.get_status(tid)
        assert st["budget_s"] == config.DELEGATED_TASK_BUDGET_S
    finally:
        q.stop()
    print("ok queue_cancel_and_status")


def test_queue_executor_end_to_end():
    """The full composition: real queue + real executor + stub LLM +
    a real dispatch-registered tool, multi-step, outcome recorded."""
    tool = _register("fake_e2e", ScriptedTool(["e2e-result"]))
    steps = [_step("fake_e2e", params={"x": "a"}),
             _step("fake_e2e", params={"x": "{prev}"})]
    ex = taskexec.AgentExecutor(
        # Pass 28 #11: the LLM contract is llm(prompt, system) — the
        # verifier passes the reviewer instruction as `system`, the
        # planner passes its own system prompt. Key the double on
        # the system text, not on a missing second argument.
        llm=lambda p, s: ("VERDICT: PASS" if s and "skeptical reviewer" in s
                          else _plan_json(steps)),
        summarizer=lambda goal, completed: "E2E SUMMARY",
        sleep_fn=lambda s: None)
    q = taskqueue.TaskQueue(executor_factory=lambda: ex)
    q.start()
    try:
        tid = q.submit("end to end goal")
        assert _wait_for(
            lambda: (q.get_status(tid) or {}).get("status") == "completed")
        st = q.get_status(tid)
        assert st["result"] == "E2E SUMMARY", st
        assert tool.calls[1]["x"] == "e2e-result", tool.calls
    finally:
        q.stop()
    print("ok queue_executor_end_to_end")


# ---------------------------------------------------------------- tools
def test_delegate_tools_end_to_end():
    fake = _FakeExecutor()
    q = taskqueue.TaskQueue(executor_factory=lambda: fake)
    q.start()
    saved = taskqueue._queue
    taskqueue._queue = q
    try:
        out, _ = dispatch.execute_tool(
            "delegate_task", {"goal": "research the thing"})
        # Pass 27 #25: delegate_task returns a STARTED outcome
        # carrying the child handle, not "Delegated as task".
        assert out.startswith("status=started action=delegate_task "), out
        tid = out.split("task_id=")[1].split()[0]
        assert _wait_for(
            lambda: (taskqueue.get_queue().get_status(tid) or {})
            .get("status") == "completed")
        st_out, _ = dispatch.execute_tool("task_status", {"task_id": tid})
        assert "completed" in st_out, st_out
        assert "done: research the thing" in st_out, st_out
        bad, _ = dispatch.execute_tool("task_status", {"task_id": "nope"})
        assert "No delegated task" in bad
        no_goal, _ = dispatch.execute_tool("delegate_task", {"goal": ""})
        # The dispatcher's own schema validation rejects it first.
        assert "requires goal" in no_goal, no_goal
    finally:
        taskqueue._queue = saved
        q.stop()
    print("ok delegate_tools_end_to_end")
