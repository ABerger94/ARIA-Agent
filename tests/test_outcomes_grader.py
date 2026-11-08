"""Outcomes/Grader upgrade (design 2026-11-08 —
aria-audit/outcomes-grader-design-2026-11-08.md).

Pins the design's §4 list:

  * rubric selection + validation (unknown task_type →
    general; a malformed rubric never crashes a verify);
  * bundle construction — no chat history / worker reasoning
    can enter it, outputs capped at 1500 chars with the
    truncation marked, destination probes included;
  * malformed grader JSON → cannot_grade → the legacy
    fallback in BOTH engines (missions: deterministic evidence
    check; taskexec: the one-line verifier);
  * missions: failed criteria drive repair targeting; a
    grader PASS cannot override a recorded failure marker
    (pass-18 invariant under the grader); a retry-unsafe
    repair target still ends done_unverified;
  * taskexec: a grader FAIL parks with the structured reason
    (failed criteria + cited evidence);
  * self_graded set when grader model == worker model, unset
    otherwise; the "different" policy picks a different leg;
  * off-switch: outcomes_grader_enabled default False — with
    it off the grader is never consulted and both engines run
    their legacy paths.

Harness: the light package-stub prelude of
test_taskexec_verify_signature.py for the unit/engine tests,
and test_missions.py's real-dispatch fixture (with two fake
tools of its own, grader_note / grader_read) for the mission
tests.
"""
import importlib
import json
import os
import sys
import tempfile
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules.setdefault("aria", _pkg)
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules.setdefault("aria.tools", _tools_pkg)
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules.setdefault("aria.agent", _agent_pkg)
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

for _name in ("aria.scheduler", "aria.hardware", "aria.spotify",
              "aria.agent.taskqueue"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
_vision = types.ModuleType("aria.vision")
_vision.get_face_frame_jpeg = lambda: None
_vision.publish_phone_frame = lambda jpeg: True
_vision.get_phone_frame_jpeg = lambda: None
_vision.get_phone_frame_status = lambda: {"active": False,
                                          "age_s": -1.0}
_vision.describe_phone_view = lambda q="": "[stubbed]"
sys.modules.setdefault("aria.vision", _vision)
_speech = types.ModuleType("aria.speech")
_speech.speak = lambda t: None
_speech.tts_bytes_for_bridge = lambda text: (b"", "no speech")
_speech.transcribe_audio = lambda audio, mime: ""
_speech._SPEECH_QUEUE = types.SimpleNamespace(
    unfinished_tasks=0, put=lambda t: None)
sys.modules.setdefault("aria.speech", _speech)
_hud = types.ModuleType("aria.hud")
_hud.tool_show_commands = lambda: "ok"
_hud.tool_hide_commands = lambda: "ok"
sys.modules.setdefault("aria.hud", _hud)
_frame = types.ModuleType("aria.frame")
sys.modules.setdefault("aria.frame", _frame)

import pytest

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_outcomes_"), "test.db")
memory.init_databases()
memory._embed = lambda text: None

rubrics = importlib.import_module("aria.agent.rubrics")
grader = importlib.import_module("aria.agent.grader")
planner = importlib.import_module("aria.agent.planner")
taskexec = importlib.import_module("aria.agent.taskexec")
action_journal = importlib.import_module("aria.action_journal")
tool_policy = importlib.import_module("aria.tool_policy")


def _grade_json(rubric_id, fail_must=(), fail_advisory=(),
                present_cases=(), na_must=()):
    rubric = rubrics.get_rubric(rubric_id)
    return json.dumps({
        "criteria": [
            {"id": c["id"],
             "verdict": ("na" if c["id"] in na_must else
                         "fail" if c["id"] in fail_must or
                         c["id"] in fail_advisory else "pass"),
             "evidence": f"evidence for {c['id']}"}
            for c in rubric["criteria"]],
        "failure_cases": [
            {"id": f["id"], "present": f["id"] in present_cases,
             "evidence": f"case evidence {f['id']}"}
            for f in rubric["failure_cases"]]})


def _chat_returning(text, model="grader-model"):
    def _chat(prompt, system, model_arg=None):
        return text, model
    return _chat


@pytest.fixture()
def grader_on(monkeypatch):
    monkeypatch.setattr(
        config, "get_setting",
        lambda key, default=None:
            True if key == "outcomes_grader_enabled" else default)
    yield


# -- rubrics: selection + validation -------------------------------

def test_rubric_selection_and_fallbacks():
    assert rubrics.get_rubric("research")["id"] == "research"
    assert rubrics.get_rubric("code_change")["id"] == "code_change"
    assert rubrics.get_rubric("bogus")["id"] == "general"
    assert rubrics.get_rubric(None)["id"] == "general"
    assert rubrics.select_rubric(tools=["write_file"])["id"] == \
        "code_change"
    assert rubrics.select_rubric(tools=["web_search"])["id"] == \
        "research"
    assert rubrics.select_rubric(tools=["send_email"])["id"] == \
        "general"
    assert rubrics.select_rubric("research", ["write_file"])["id"] \
        == "research"


def test_malformed_rubric_never_validates():
    assert rubrics.validate_rubric(None) is None
    assert rubrics.validate_rubric({}) is None
    assert rubrics.validate_rubric(
        {"id": "x", "version": 1, "task_types": ["x"],
         "criteria": []}) is None
    assert rubrics.validate_rubric(
        {"id": "x", "version": 1, "task_types": ["x"],
         "criteria": [{"id": "c", "text": "t", "kind": "bogus",
                       "scope": "all"}]}) is None
    assert rubrics.validate_rubric(
        {"id": "x", "version": 1, "task_types": ["x"],
         "criteria": [{"id": "c", "text": "t", "kind": "advisory",
                       "scope": "all"}]}) is None  # no must
    good = rubrics.validate_rubric(
        {"id": "x", "version": 2, "task_types": ["x"],
         "criteria": [{"id": "c", "text": "t", "kind": "must",
                       "scope": "write_file"}]})
    assert good is not None
    assert good["criteria"][0]["scope"] == ["write_file"]


def test_builtin_rubrics_are_valid_and_versioned():
    for t in ("research", "code_change", "general"):
        r = rubrics.get_rubric(t)
        assert r["version"] >= 1
        assert any(c["kind"] == "must" for c in r["criteria"])


# -- planner task_type ----------------------------------------------

def test_planner_task_type_validated(monkeypatch):
    monkeypatch.setattr(planner, "known_tools",
                        lambda: {"web_search"})

    def _llm(payload):
        def fake(prompt, system):
            return json.dumps(payload)
        return fake

    step = {"step": 1, "tool": "web_search", "description": "s",
            "parameters": {}, "critical": True}
    plan = planner.create_plan("g", llm=_llm(
        {"goal": "g", "task_type": "research", "steps": [step]}))
    assert plan["task_type"] == "research"
    plan = planner.create_plan("g", llm=_llm(
        {"goal": "g", "task_type": "bogus", "steps": [step]}))
    assert plan["task_type"] == "general"
    plan = planner.create_plan("g", llm=_llm(
        {"goal": "g", "steps": [step]}))
    assert plan["task_type"] == "general"

    def _boom(prompt, system):
        raise RuntimeError("pool down")
    plan = planner.create_plan("g", llm=_boom)
    assert plan["task_type"] == "general"


# -- bundle construction --------------------------------------------

def test_bundle_excludes_history_and_reasoning_and_marks_truncation():
    steps = [{
        "step": 1, "tool": "web_search", "description": "look",
        "parameters": {"query": "q"},
        "output": "x" * 2000,
        "reasoning": "SECRET-REASONING",
        "history": "SECRET-HISTORY",
        "chat": "SECRET-CHAT",
    }]
    norm = grader.normalize_steps(steps)
    bundle = grader.build_bundle(
        "the goal", norm, rubrics.get_rubric("general"),
        probes=[{"kind": "journal", "step": 1, "tool": "send_x",
                 "state": "done"}])
    assert "SECRET-REASONING" not in bundle
    assert "SECRET-HISTORY" not in bundle
    assert "SECRET-CHAT" not in bundle
    assert "[truncated: 500 more chars]" in bundle
    assert "journal receipt: state=done" in bundle
    assert "claims" in bundle and "evidence" in bundle


def test_probes_file_and_journal(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("landed content", encoding="utf-8")
    steps = grader.normalize_steps([{
        "step": 1, "tool": "write_file",
        "parameters": {"filename": str(target)},
        "output": "wrote it"}])
    op = "test:probe:1"
    assert action_journal.begin(op, "write_file",
                                {"filename": str(target)}) is None
    action_journal.finish(op, "file written")
    probes = grader.build_probes(steps, journal_ids={1: op})
    kinds = {(p["kind"], p.get("state") or p.get("exists"))
             for p in probes}
    assert ("journal", "done") in kinds
    file_probes = [p for p in probes if p["kind"] == "file"]
    assert file_probes and file_probes[0]["exists"] is True
    assert file_probes[0]["size"] == len("landed content")
    bundle = grader.build_bundle("g", steps,
                                 rubrics.get_rubric("code_change"),
                                 probes=probes)
    assert "landed content" in bundle


# -- grader verdict computation --------------------------------------

def test_grader_pass_and_advisory_fail_does_not_block():
    steps = [{"step": 1, "tool": "web_search",
              "output": "found it"}]
    out = grader.grade(
        "g", steps,
        chat=_chat_returning(_grade_json(
            "research", fail_advisory=("uncertainty_named",))))
    assert out["verdict"] == "pass", out
    assert out["failed_criteria"] == []
    assert out["rubric_id"] == "research"


def test_grader_must_fail_and_present_case_fail():
    steps = [{"step": 1, "tool": "web_search", "output": "stuff"}]
    out = grader.grade(
        "g", steps,
        chat=_chat_returning(_grade_json(
            "research", fail_must=("question_answered",),
            present_cases=("unsourced_assertion",))))
    assert out["verdict"] == "fail", out
    assert "question_answered" in out["failed_criteria"]
    assert "unsourced_assertion" in out["failed_criteria"]
    reason = grader.format_failure_reason(out)
    assert "grader (rubric research v1) failed:" in reason
    assert "evidence for question_answered" in reason


def test_grader_malformed_output_is_cannot_grade():
    steps = [{"step": 1, "tool": "web_search", "output": "x"}]
    out = grader.grade("g", steps,
                       chat=_chat_returning("not json at all"))
    assert out["verdict"] == "cannot_grade", out
    out = grader.grade(
        "g", steps,
        chat=_chat_returning(_grade_json(
            "research", na_must=("sourced_claims",))))
    assert out["verdict"] == "cannot_grade", out

    def _boom(prompt, system, model=None):
        raise RuntimeError("pool down")
    out = grader.grade("g", steps, chat=_boom)
    assert out["verdict"] == "cannot_grade", out


def test_grader_model_policies_and_self_graded():
    roster = grader._roster()
    assert len(roster) >= 2
    seen = {}

    def _chat(prompt, system, model=None):
        seen["model"] = model
        return _grade_json("general"), model

    steps = [{"step": 1, "tool": "web_search", "output": "x"}]
    out = grader.grade("g", steps, chat=_chat,
                       worker_model=roster[0], policy="different")
    assert seen["model"] == roster[1]
    assert out["grader_model"] == roster[1]
    assert out["self_graded"] is False

    out = grader.grade("g", steps, chat=_chat,
                       worker_model=roster[0], policy="same")
    assert seen["model"] == roster[0]
    assert out["self_graded"] is True

    out = grader.grade("g", steps, chat=_chat, policy="cheap")
    assert seen["model"] == roster[-1]
    assert out["grader_model"] == roster[-1]


# -- taskexec wiring ---------------------------------------------------

def _executor(llm, **kw):
    return taskexec.AgentExecutor(
        llm=llm, tool_runner=kw.pop("tool_runner", None) or
        (lambda name, args: "step output"),
        summarizer=lambda goal, completed: "SUMMARY-OK",
        sleep_fn=lambda s: None, **kw)


def test_taskexec_grader_fail_returns_structured_reason(grader_on):
    def _llm(prompt, system):
        assert "independent grader" in system, system
        return _grade_json("general",
                           fail_must=("goal_accomplished",))

    ex = _executor(_llm)
    reason = ex._verify(
        "do the thing",
        [{"step": 1, "tool": "fake_tool",
          "parameters": {}}],
        {1: "claimed done"},
        plan={"task_type": "general"})
    assert reason is not None
    assert "grader (rubric general v1) failed:" in reason
    assert "[goal_accomplished] evidence for goal_accomplished" \
        in reason


def test_taskexec_grader_pass_and_cannot_grade_fallback(grader_on):
    systems = []

    def _llm_pass(prompt, system):
        systems.append(system)
        if "independent grader" in system:
            return _grade_json("general")
        return "VERDICT: PASS"
    ex = _executor(_llm_pass)
    assert ex._verify("g", [{"step": 1, "tool": "t"}], {1: "ok"},
                      plan={"task_type": "general"}) is None

    systems.clear()

    def _llm_garbage(prompt, system):
        systems.append(system)
        if "independent grader" in system:
            return "garbage, no json"
        return "VERDICT: PASS"
    ex = _executor(_llm_garbage)
    assert ex._verify("g", [{"step": 1, "tool": "t"}], {1: "ok"},
                      plan={"task_type": "general"}) is None
    # cannot_grade fell through to the legacy one-line verifier.
    assert any("skeptical reviewer" in s for s in systems)


def test_taskexec_off_switch_never_consults_grader():
    systems = []

    def _llm(prompt, system):
        systems.append(system)
        return "VERDICT: PASS"
    ex = _executor(_llm)
    assert ex._verify("g", [{"step": 1, "tool": "t"}], {1: "ok"},
                      plan={"task_type": "general"}) is None
    assert systems, "legacy verifier should have run"
    assert all("independent grader" not in s for s in systems)


def test_taskexec_execute_parks_with_structured_reason(
        grader_on, monkeypatch):
    monkeypatch.setattr(planner, "known_tools",
                        lambda: {"fake_tool"})
    monkeypatch.setattr(
        planner, "create_plan",
        lambda goal, llm=None: {
            "goal": goal, "task_type": "general",
            "steps": [{"step": 1, "tool": "fake_tool",
                       "description": "only step",
                       "parameters": {}, "critical": True}],
            "warnings": []})

    def _llm(prompt, system):
        if "independent grader" in system:
            return _grade_json(
                "general", fail_must=("goal_accomplished",))
        return "VERDICT: PASS"
    ex = _executor(_llm)
    with pytest.raises(taskexec.TaskBlocked) as ei:
        ex.execute("do the thing", verify=True)
    assert "grader (rubric general v1) failed:" in ei.value.reason
    assert "goal_accomplished" in ei.value.reason


# -- missions wiring ---------------------------------------------------

data_dir = importlib.import_module("aria.data_dir")
approval = importlib.import_module("aria.approval")
dispatch = importlib.import_module("aria.tools.dispatch")
missions = importlib.import_module("aria.missions")
grader_mod = grader

CALLS = []


def _grader_note(a):
    CALLS.append(("grader_note", dict(a)))
    return f"noted {a.get('text', '')}"


def _grader_read(a):
    CALLS.append(("grader_read", dict(a)))
    return f"read {a.get('text', '')}"


dispatch.register_tool("grader_note", _grader_note)
dispatch.register_tool("grader_read", _grader_read)
tool_policy.declare_replay_safe("grader_read", True)
dispatch.set_spine_hook(lambda *a, **k: None)
dispatch.set_log_hook(lambda *a, **k: None)

_SAFE = {"on": False}


@pytest.fixture()
def menv(tmp_path, monkeypatch):
    monkeypatch.setattr(data_dir, "_ROOT", str(tmp_path / "ARIA"))
    monkeypatch.setattr(memory, "DB_PATH",
                        str(tmp_path / "aria_memory.db"))
    memory.init_databases()
    monkeypatch.setattr(config, "is_safe_mode",
                        lambda: _SAFE["on"])
    monkeypatch.delenv("ARIA_SAFE_MODE", raising=False)
    approval._PENDING.clear()
    CALLS.clear()
    _SAFE["on"] = False
    missions.set_verifier(None)
    missions._worker_alive = False
    missions._LAST_GRADE.clear()
    approval.set_mode("auto")
    dispatch.reset_turn_state()
    yield types.SimpleNamespace(tmp=tmp_path)
    approval._PENDING.clear()
    missions.set_verifier(None)
    missions._worker_alive = False
    missions._LAST_GRADE.clear()
    dispatch.reset_turn_state()


def _mcreate(goal, steps, **kw):
    args = {"goal": goal,
            "steps": json.dumps(
                [{"tool": t, "args": a} for t, a in steps])}
    args.update(kw)
    out = missions.tool_mission_create(args)
    assert "Mission #" in out, out
    return int(out.split("Mission #", 1)[1].split(":", 1)[0])


def _mapprove(mid):
    tok = missions._mission_row(mid)["plan_token"]
    assert tok, "plan was not parked"
    result = approval.approve_token(tok)
    assert "plan approved" in result, result


def _mpump(mid, want, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        missions.executor_tick()
        row = missions._mission_row(mid)
        if row["status"] in want:
            return row
        time.sleep(0.05)
    return missions._mission_row(mid)


def _script_grader(monkeypatch, script):
    """script: fn(call_number, prompt) -> grade JSON text."""
    calls = {"n": 0}

    def _fake_chat(prompt, system, model=None):
        calls["n"] += 1
        return script(calls["n"], prompt), "mission-grader-model"
    monkeypatch.setattr(grader_mod, "_default_chat", _fake_chat)
    return calls


def test_mission_grader_pass_renders_grade(menv, monkeypatch,
                                           grader_on):
    _script_grader(monkeypatch,
                   lambda n, p: _grade_json("general"))
    mid = _mcreate("graded pass",
                   [("grader_note", {"text": "hi"})])
    _mapprove(mid)
    row = _mpump(mid, {"done"})
    assert row["status"] == "done", row
    summary = row["summary"] or ""
    assert "Grade: general v1 PASS" in summary, summary
    assert "criterion goal_accomplished: pass" in summary
    assert "failure case claimed_not_done: absent" in summary


def test_mission_grader_fail_retry_unsafe_done_unverified(
        menv, monkeypatch, grader_on):
    _script_grader(monkeypatch, lambda n, p: _grade_json(
        "general", fail_must=("goal_accomplished",)))
    mid = _mcreate("graded fail",
                   [("grader_note", {"text": "hi"})])
    _mapprove(mid)
    row = _mpump(mid, {"done_unverified"})
    assert row["status"] == "done_unverified", row
    # grader_note is not retry-safe: graded exactly once, the
    # effect step was never blindly re-run.
    assert len([c for c in CALLS if c[0] == "grader_note"]) == 1
    assert "failed criteria: goal_accomplished" in \
        (row["summary"] or "")


def test_mission_grader_pass_cannot_override_failure_marker(
        menv, monkeypatch, grader_on):
    # Pass-18 invariant under the grader: the grade says PASS,
    # the recorded step output is a failure marker — the
    # mission must NOT certify done.
    _script_grader(monkeypatch,
                   lambda n, p: _grade_json("general"))
    mid = _mcreate("grader veto proof",
                   [("grader_note", {"text": "only"})])
    _mapprove(mid)
    step = missions._step_rows(mid)[0]
    missions._set_step(step["id"], status="done",
                       output="[Tool Error: smtp exploded]")
    assert missions._verify(mid) is True  # terminal state
    row = missions._mission_row(mid)
    assert row["status"] == "done_unverified", row


def test_mission_grader_repair_targets_by_scope(
        menv, monkeypatch, grader_on):
    # Research rubric: question_answered is must with scope
    # "last" — only the LAST step is a repair target.
    def _script(n, prompt):
        if n == 1:
            return _grade_json(
                "research", fail_must=("question_answered",))
        return _grade_json("research")
    calls = _script_grader(monkeypatch, _script)
    mid = _mcreate("scoped repair",
                   [("grader_read", {"text": "one"}),
                    ("grader_read", {"text": "two"})],
                   task_type="research")
    _mapprove(mid)
    row = _mpump(mid, {"done"})
    assert row["status"] == "done", row
    texts = [c[1].get("text") for c in CALLS
             if c[0] == "grader_read"]
    assert texts.count("one") == 1, texts   # step 1 never re-ran
    assert texts.count("two") == 2, texts   # step 2 repaired once
    assert calls["n"] == 2                  # graded twice


def test_mission_cannot_grade_falls_back_to_evidence_check(
        menv, monkeypatch, grader_on):
    _script_grader(monkeypatch, lambda n, p: "definitely not json")
    mid = _mcreate("ungradeable",
                   [("grader_note", {"text": "hi"})])
    _mapprove(mid)
    row = _mpump(mid, {"done"})
    assert row["status"] == "done", row
    assert "Grade:" not in (row["summary"] or "")


def test_mission_off_switch_never_consults_grader(
        menv, monkeypatch):
    seen = {"n": 0}

    def _counting_grade(*a, **k):
        seen["n"] += 1
        raise AssertionError("grader consulted while off")
    monkeypatch.setattr(grader_mod, "grade", _counting_grade)
    mid = _mcreate("legacy only",
                   [("grader_note", {"text": "hi"})])
    _mapprove(mid)
    row = _mpump(mid, {"done"})
    assert row["status"] == "done", row
    assert seen["n"] == 0
