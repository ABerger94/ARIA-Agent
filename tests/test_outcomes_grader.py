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
             # Pass 36 #22: a passing MUST criterion must cite
             # a real step or probe from the bundle.
             "evidence": f"step 1: evidence for {c['id']}"}
            for c in rubric["criteria"]],
        "failure_cases": [
            {"id": f["id"], "present": f["id"] in present_cases,
             "evidence": f"step 1: case evidence {f['id']}"}
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


def test_probes_file_and_journal(tmp_path, monkeypatch):
    # The probed file lives INSIDE the workspace (pass 36 #20:
    # a destination outside it is unavailable evidence, never
    # read). The journal row attests the destination via
    # add_evidence, so the probe reads the receipt's path.
    from aria.tools import builtins
    monkeypatch.setattr(builtins, "WORKSPACE_DIR", str(tmp_path))
    target = tmp_path / "out.txt"
    target.write_text("landed content", encoding="utf-8")
    steps = grader.normalize_steps([{
        "step": 1, "tool": "write_file",
        "parameters": {"filename": "out.txt"},
        "output": "wrote it"}])
    op = "test:probe:1"
    assert action_journal.begin(op, "write_file",
                                {"filename": "out.txt"}) is None
    evidenced = action_journal.add_evidence(
        "write_file", {"filename": "out.txt"}, "file written")
    action_journal.finish(op, evidenced)
    probes = grader.build_probes(steps, journal_ids={1: op})
    kinds = {(p["kind"], p.get("state") or p.get("exists"))
             for p in probes}
    assert ("journal", "done") in kinds
    file_probes = [p for p in probes if p["kind"] == "file"]
    assert file_probes and file_probes[0]["exists"] is True
    # Pass 36 #24: the journal attested this file's hash at
    # write time and the bytes still match — verified, not
    # merely present.
    assert file_probes[0]["status"] == "verified", file_probes[0]
    assert file_probes[0]["size"] == len("landed content")
    bundle = grader.build_bundle("g", steps,
                                 rubrics.get_rubric("code_change"),
                                 probes=probes)
    assert "landed content" in bundle


def test_file_probe_changed_and_missing_pass36_24(
        tmp_path, monkeypatch):
    # Pass 36 #24: a file edited after the journal recorded its
    # hash reads "changed" (not verified), and a file deleted
    # after attestation reads "missing" — each distinct, each
    # rendered so in the bundle.
    from aria.tools import builtins
    monkeypatch.setattr(builtins, "WORKSPACE_DIR", str(tmp_path))
    target = tmp_path / "out.txt"
    target.write_text("original content", encoding="utf-8")
    steps = grader.normalize_steps([{
        "step": 1, "tool": "write_file",
        "parameters": {"filename": "out.txt"},
        "output": "wrote it"}])
    op = "test:probe:24"
    assert action_journal.begin(op, "write_file",
                                {"filename": "out.txt"}) is None
    evidenced = action_journal.add_evidence(
        "write_file", {"filename": "out.txt"}, "file written")
    action_journal.finish(op, evidenced)

    target.write_text("someone edited this", encoding="utf-8")
    probes = grader.build_probes(steps, journal_ids={1: op})
    fp = [p for p in probes if p["kind"] == "file"][0]
    assert fp["status"] == "changed", fp
    assert fp["exists"] is True
    # The sample comes from the same read as the hash: it is
    # the NEW bytes, not the recorded ones.
    assert fp["sample"] == "someone edited this"
    bundle = grader.build_bundle(
        "g", steps, rubrics.get_rubric("code_change"), probes=probes)
    assert "DIFFERS from the recorded artifact hash" in bundle

    target.unlink()
    probes = grader.build_probes(steps, journal_ids={1: op})
    fp = [p for p in probes if p["kind"] == "file"][0]
    assert fp["status"] == "missing", fp
    assert fp["exists"] is False


def test_file_probe_rejected_path_is_unavailable(tmp_path,
                                                monkeypatch):
    # Pass 36 #20: a path workspace policy rejects — absolute
    # outside the roots, or inside the reserved projects/
    # subtree — is unavailable evidence. The probe never falls
    # back to the raw path, never opens it, and no sample of
    # its content reaches the bundle.
    from aria.tools import builtins
    monkeypatch.setattr(builtins, "WORKSPACE_DIR", str(tmp_path))
    # The reserved-projects check reads config.WORKSPACE_DIR.
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET-CONTENT", encoding="utf-8")
    steps = grader.normalize_steps([
        {"step": 1, "tool": "write_file",
         "parameters": {"filename": str(secret)},
         "output": "wrote it"},
        {"step": 2, "tool": "write_file",
         "parameters": {"filename": "projects/other/out.txt"},
         "output": "wrote it"}])
    probes = grader.build_probes(steps)
    file_probes = [p for p in probes if p["kind"] == "file"]
    assert len(file_probes) == 2
    for fp in file_probes:
        assert fp["status"] == "unavailable", fp
        assert fp["exists"] is False
        assert "sample" not in fp
    bundle = grader.build_bundle(
        "g", steps, rubrics.get_rubric("code_change"), probes=probes)
    assert "TOPSECRET-CONTENT" not in bundle
    assert "unavailable" in bundle


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


def test_grader_unknown_worker_labelled_not_presumed():
    # Pass 36 #29: worker_model None is unknown provenance —
    # roster[0] is never presumed to be the worker, self_graded
    # stays False, and the note says so.
    roster = grader._roster()
    assert len(roster) >= 2
    steps = [{"step": 1, "tool": "web_search", "output": "x"}]
    out = grader.grade("g", steps,
                       chat=_chat_returning(_grade_json("research")),
                       worker_model=None, policy="different")
    assert out["verdict"] == "pass", out
    assert out["worker_model"] is None, out
    assert out["self_graded"] is False, out
    assert "worker unknown" in grader.format_grade_note(out), out
    print("ok grader_unknown_worker_labelled_not_presumed")


def test_grader_deadline_already_passed_is_cannot_grade():
    # Pass 36 #31: a grade whose deadline already passed never
    # calls the pool — it is cannot_grade immediately.
    def _never(prompt, system, model=None):
        raise AssertionError("grader chat must not be called")

    steps = [{"step": 1, "tool": "web_search", "output": "x"}]
    out = grader.grade("g", steps, chat=_never,
                       deadline=time.monotonic() - 1)
    assert out["verdict"] == "cannot_grade", out
    print("ok grader_deadline_already_passed_is_cannot_grade")


def test_grader_cancelled_propagates():
    # Pass 36 #31 (with #30): a cancelled grade propagates as
    # runtime.Cancelled — it must not be swallowed into a
    # cannot_grade the engines then treat as "unverifiable".
    runtime = importlib.import_module("aria.runtime")

    def _cancelled(prompt, system, model=None):
        raise runtime.Cancelled("cancelled mid-grade")

    steps = [{"step": 1, "tool": "web_search", "output": "x"}]
    try:
        grader.grade("g", steps, chat=_cancelled)
    except runtime.Cancelled:
        print("ok grader_cancelled_propagates")
        return
    raise AssertionError("expected runtime.Cancelled")


def test_grader_strict_contract_pass36_22():
    # Omitted failure_cases no longer silently passes.
    steps = [{"step": 1, "tool": "web_search", "output": "x"}]
    rubric = rubrics.get_rubric("research")
    data = json.loads(_grade_json("research"))
    del data["failure_cases"]
    out = grader.grade("g", steps,
                       chat=_chat_returning(json.dumps(data)))
    assert out["verdict"] == "cannot_grade", out

    # A duplicated criterion id is ambiguous, not a grade.
    data = json.loads(_grade_json("research"))
    data["criteria"].append(dict(data["criteria"][0]))
    out = grader.grade("g", steps,
                       chat=_chat_returning(json.dumps(data)))
    assert out["verdict"] == "cannot_grade", out

    # Wrong types: a non-string verdict, a non-bool present.
    data = json.loads(_grade_json("research"))
    data["criteria"][0]["verdict"] = 1
    out = grader.grade("g", steps,
                       chat=_chat_returning(json.dumps(data)))
    assert out["verdict"] == "cannot_grade", out
    data = json.loads(_grade_json("research"))
    data["failure_cases"][0]["present"] = "yes"
    out = grader.grade("g", steps,
                       chat=_chat_returning(json.dumps(data)))
    assert out["verdict"] == "cannot_grade", out

    # A missing required criterion id is cannot_grade.
    data = json.loads(_grade_json("research"))
    data["criteria"] = [c for c in data["criteria"]
                        if c["id"] != "sourced_claims"]
    out = grader.grade("g", steps,
                       chat=_chat_returning(json.dumps(data)))
    assert out["verdict"] == "cannot_grade", out


def test_grader_hard_negative_beats_unknown_pass36_22():
    # A must fail stays a FAIL even when another must is na;
    # a present failure case does the same.
    steps = [{"step": 1, "tool": "web_search", "output": "x"}]
    out = grader.grade(
        "g", steps,
        chat=_chat_returning(_grade_json(
            "research", fail_must=("question_answered",),
            na_must=("sourced_claims",))))
    assert out["verdict"] == "fail", out
    assert "question_answered" in out["failed_criteria"]
    out = grader.grade(
        "g", steps,
        chat=_chat_returning(_grade_json(
            "research", present_cases=("unsourced_assertion",),
            na_must=("sourced_claims",))))
    assert out["verdict"] == "fail", out
    assert "unsourced_assertion" in out["failed_criteria"]
    # With no hard negative, the unknown must still blocks.
    out = grader.grade(
        "g", steps,
        chat=_chat_returning(_grade_json(
            "research", na_must=("sourced_claims",))))
    assert out["verdict"] == "cannot_grade", out


def test_grader_pass_needs_cited_evidence_pass36_22():
    steps = [{"step": 1, "tool": "web_search", "output": "x"}]

    def _with_evidence(text):
        data = json.loads(_grade_json("research"))
        for c in data["criteria"]:
            if c["id"] == "question_answered":
                c["evidence"] = text
        return json.dumps(data)

    # Empty evidence for a passing must: no pass.
    out = grader.grade("g", steps, chat=_chat_returning(
        _with_evidence("")))
    assert out["verdict"] == "cannot_grade", out
    # Evidence citing a step that does not exist: no pass.
    out = grader.grade("g", steps, chat=_chat_returning(
        _with_evidence("step 99 shows it worked")))
    assert out["verdict"] == "cannot_grade", out
    # Evidence citing the real step: pass.
    out = grader.grade("g", steps, chat=_chat_returning(
        _with_evidence("step 1 output answers the question")))
    assert out["verdict"] == "pass", out


# -- pass 36 #25 ------------------------------------------------------

def test_journal_ids_use_prefix_results_for_prev():
    # {prev} in step 2 means step 1's result, not the final
    # result map's — the recomputed op id must hash the
    # substituted params as they were at execution time.
    from aria.action_journal import signature
    ex = _executor(lambda p, s: "x")
    completed = [
        {"step": 1, "tool": "write_file",
         "parameters": {"filename": "first.txt"}},
        {"step": 2, "tool": "write_file",
         "parameters": {"filename": "{prev}.txt"}},
    ]
    results = {1: "first", 2: "second"}
    ids = ex._journal_ids(
        completed, results,
        resume={"execution_scope": "task:test25"})
    assert ids[2] == ("task:test25:step:2:" + signature(
        "write_file", {"filename": "first.txt"}))
    assert ids[2] != ("task:test25:step:2:" + signature(
        "write_file", {"filename": "second.txt"}))
    assert ex._step_params[2] == {"filename": "first.txt"}


def test_journal_ids_prefer_persisted_evidence(
        tmp_path, monkeypatch):
    from aria.agent import taskstore as _ts
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    _ts.create("persist1", "goal")
    _ts.merge_step_evidence("persist1", {1: {
        "op_id": "persisted-op-1",
        "params": {"filename": "saved.txt"}}})
    ex = _executor(lambda p, s: "x")
    completed = [{"step": 1, "tool": "write_file",
                  "parameters": {"filename": "{prev}.txt"}}]
    ids = ex._journal_ids(
        completed, {1: "r"},
        resume={"execution_scope": "task:persist1"})
    assert ids[1] == "persisted-op-1"
    assert ex._step_params[1] == {"filename": "saved.txt"}


def test_probes_use_substituted_params_and_skip_explain(
        tmp_path, monkeypatch):
    from aria.tools import builtins
    monkeypatch.setattr(builtins, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "real.txt").write_text("real", encoding="utf-8")
    (tmp_path / "dev_agent_output.py").write_text(
        "code", encoding="utf-8")
    (tmp_path / "input.py").write_text("x = 1", encoding="utf-8")
    steps = grader.normalize_steps([
        {"step": 1, "tool": "write_file",
         "parameters": {"filename": "{prev}.txt"}, "output": "ok"},
        {"step": 2, "tool": "code_helper",
         "parameters": {"action": "explain",
                        "file_path": "input.py"},
         "output": "it adds one"},
        {"step": 3, "tool": "code_helper",
         "parameters": {"action": "write",
                        "output_path": "real.txt"},
         "output": "wrote"},
        {"step": 4, "tool": "dev_agent",
         "parameters": {"language": "python"}, "output": "saved"},
    ])
    probes = grader.build_probes(
        steps, step_params={1: {"filename": "real.txt"}})
    by_step = {p["step"]: p for p in probes if p["kind"] == "file"}
    # Step 1 probed the substituted destination, not the
    # template path.
    assert by_step[1]["path"].endswith("real.txt")
    assert by_step[1]["exists"] is True
    # Step 2 (explain) reads an input file — no file probe.
    assert 2 not in by_step
    # Step 3 (write) is probed.
    assert by_step[3]["exists"] is True
    # Step 4 (dev_agent, no output_path) probes the default.
    assert by_step[4]["path"].endswith("dev_agent_output.py")
    assert by_step[4]["exists"] is True


def test_taskstore_step_evidence_merge_and_checkpoint(
        tmp_path, monkeypatch):
    from aria.agent import taskstore as _ts
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    _ts.create("ev1", "goal")
    assert _ts.merge_step_evidence("ev1", {1: {
        "op_id": "op1", "params": {"a": 1},
        "artifact_path": "/x"}}) is True
    got = _ts.get("ev1")
    assert got["step_evidence"]["1"]["op_id"] == "op1"
    # A checkpoint without step_evidence leaves the merged
    # record untouched.
    _ts.checkpoint("ev1", {"plan": None, "completed": [],
                           "results": {}})
    assert _ts.get("ev1")["step_evidence"]["1"]["op_id"] == "op1"
    # A checkpoint carrying step_evidence replaces it.
    _ts.checkpoint("ev1", {"plan": None, "completed": [],
                           "results": {},
                           "step_evidence": {2: {"op_id": "op2"}}})
    assert _ts.get("ev1")["step_evidence"] == {
        "2": {"op_id": "op2"}}
    assert _ts.merge_step_evidence("nope", {1: {}}) is False


# -- pass 36 #28 ------------------------------------------------------

def test_settle_keeps_started_state_and_renders():
    op = "test:settle:1"
    assert action_journal.begin(
        op, "delegate_task", {"goal": "g"}) is None
    action_journal.finish(
        op, "status=started action=delegate_task task_id=child1 "
            "project=default")
    row = action_journal.get(op)
    assert row["state"] == "started"
    assert action_journal.settle(
        op, "child1", "completed", "all done") is True
    row = action_journal.get(op)
    assert row["state"] == "started"  # never rewritten
    assert row["receipt"]["settlement"]["status"] == "completed"
    assert row["receipt"]["settlement"]["child_task_id"] == "child1"
    assert action_journal.settle(
        "missing-op", "c", "completed") is False
    probe = {"kind": "journal", "step": 1, "tool": "delegate_task",
             "state": "started",
             "settlement": row["receipt"]["settlement"]}
    rendered = grader._render_probe(probe)
    assert "state=started" in rendered
    assert "settled: completed (child task child1)" in rendered


def test_verify_settles_delegate_receipt_before_grading(
        tmp_path, monkeypatch, grader_on):
    from aria.agent import taskstore as _ts
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    _ts.create("child1", "child goal")
    _ts.finish("child1", "completed", result="child did it")
    op = "task:test28:step:1:abc"
    assert action_journal.begin(
        op, "delegate_task", {"goal": "child goal"}) is None
    action_journal.finish(
        op, "status=started action=delegate_task task_id=child1 "
            "project=default")

    def _llm(prompt, system):
        if "independent grader" in system:
            return _grade_json("general")
        return "VERDICT: PASS"
    ex = _executor(_llm)
    reason = ex._verify(
        "do the thing",
        [{"step": 1, "tool": "delegate_task",
          "parameters": {"goal": "child goal"}}],
        {1: "status=started action=delegate_task task_id=child1 "
            "project=default"},
        plan={"task_type": "general"}, journal_ids={1: op})
    assert reason is None
    row = action_journal.get(op)
    assert row["receipt"]["settlement"]["status"] == "completed"


def test_settle_mission_delegates(tmp_path, monkeypatch):
    from aria.agent import taskstore as _ts
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    _ts.create("child9", "g")
    _ts.finish("child9", "completed", result="done")
    op = "mission:7:step:3"
    assert action_journal.begin(
        op, "delegate_task", {"goal": "g"}) is None
    action_journal.finish(
        op, "status=started action=delegate_task task_id=child9 "
            "project=default")
    steps = [{"id": 3, "idx": 0, "tool": "delegate_task",
              "child_handle": json.dumps(
                  {"kind": "delegated_task", "task_id": "child9",
                   "project": None})}]
    missions._settle_mission_delegates(7, steps)
    row = action_journal.get(op)
    assert row["state"] == "started"
    assert row["receipt"]["settlement"]["status"] == "completed"


def test_grader_model_policies_and_self_graded():
    roster = grader._roster()
    assert len(roster) >= 2
    seen = {}

    def _chat(prompt, system, model=None):
        seen["model"] = model
        # web_search selects the research rubric — grade
        # against it, so the verdict is a real pass, not a
        # rubric mismatch.
        return _grade_json("research"), model

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
    assert "[goal_accomplished] step 1: evidence for " \
        "goal_accomplished" in reason


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


def test_mission_grade_never_leaks_across_projects(
        menv, monkeypatch, grader_on, tmp_path):
    # Pass 36 #21: mission ids are per-project, so a grade for
    # mission #1 in one project must never publish in another
    # project's mission #1.
    _script_grader(monkeypatch,
                   lambda n, p: _grade_json("general"))
    mid = _mcreate("graded pass",
                   [("grader_note", {"text": "hi"})])
    _mapprove(mid)
    row = _mpump(mid, {"done"})
    assert row["status"] == "done", row
    assert "Grade: general v1 PASS" in (row["summary"] or "")

    db2 = str(tmp_path / "aria_memory_2.db")
    monkeypatch.setattr(memory, "DB_PATH", db2)
    memory.init_databases()
    _script_grader(monkeypatch, lambda n, p: "definitely not json")
    mid2 = _mcreate("other project",
                    [("grader_note", {"text": "yo"})])
    assert mid2 == 1  # same numeric id, different project
    _mapprove(mid2)
    row2 = _mpump(mid2, {"done"})
    assert row2["status"] == "done", row2
    assert "Grade:" not in (row2["summary"] or ""), row2["summary"]


def test_build_summary_ignores_stale_last_grade(menv):
    # Pass 36 #21: the summary is built from the grade passed
    # in, never from the process-wide map — even a populated
    # _LAST_GRADE must not render when this verify graded
    # nothing.
    missions._LAST_GRADE[1] = {
        "rubric_id": "general", "rubric_version": 1,
        "verdict": "pass", "criteria": [], "failure_cases": []}
    m = {"id": 1, "goal": "g", "status": "done"}
    assert "Grade:" not in missions._build_summary(m, [])


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
