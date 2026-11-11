"""Pass 36 #23 — deterministic gates before a grader pass can
certify, plus the #23 carry-over sub-items.

Finding #23 (P1): a delegated grader pass can override missing
or contradictory execution evidence — _verify returned None on
a grader pass with no deterministic checks for completion,
required skips, receipt identity and terminal state, or
required artifacts, and the parser did not verify the cited
probe. The gates live in taskexec._verify (pass verdicts only);
the receipt/file gates run over the probes the grader build
already produced.

Carried here too: (f) _journal_ids re-keyed by stable step id,
(c) scope-aware citation validation, (h) checkpoint step
evidence, and the (e)/(d) coverage notes (see the report).
"""
import importlib
import json
import os
import sys
import tempfile
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
    tempfile.mkdtemp(prefix="aria_pass36_23_"), "test.db")
memory.init_databases()
memory._embed = lambda text: None

rubrics = importlib.import_module("aria.agent.rubrics")
grader = importlib.import_module("aria.agent.grader")
planner = importlib.import_module("aria.agent.planner")
taskexec = importlib.import_module("aria.agent.taskexec")
action_journal = importlib.import_module("aria.action_journal")
runtime = importlib.import_module("aria.runtime")


def _grade_json(rubric_id, fail_must=(), fail_advisory=(),
                present_cases=(), na_must=()):
    rubric = rubrics.get_rubric(rubric_id)
    return json.dumps({
        "criteria": [
            {"id": c["id"],
             "verdict": ("na" if c["id"] in na_must else
                         "fail" if c["id"] in fail_must or
                         c["id"] in fail_advisory else "pass"),
             "evidence": f"step 1: evidence for {c['id']}"}
            for c in rubric["criteria"]],
        "failure_cases": [
            {"id": f["id"], "present": f["id"] in present_cases,
             "evidence": f"step 1: case evidence {f['id']}"}
            for f in rubric["failure_cases"]]})


@pytest.fixture()
def grader_on(monkeypatch):
    monkeypatch.setattr(
        config, "get_setting",
        lambda key, default=None:
            True if key == "outcomes_grader_enabled" else default)
    yield


def _executor(llm, **kw):
    return taskexec.AgentExecutor(
        llm=llm, tool_runner=kw.pop("tool_runner", None) or
        (lambda name, args: "step output"),
        summarizer=lambda goal, completed: "SUMMARY-OK",
        sleep_fn=lambda s: None, **kw)


# -- (f) _journal_ids keyed by stable step id ------------------------

def test_journal_ids_keyed_by_stable_step_id():
    # The recomputed op id embeds the STABLE step id, exactly as
    # the attempt loop builds it — not the display number. The
    # old code formatted "task:t23:step:2:..." here and the probe
    # never matched the recorded receipt.
    from aria.action_journal import signature
    ex = _executor(lambda p, s: "x")
    sid1, sid2 = "aaa111", "bbb222"
    completed = [
        {"step": 1, "id": sid1, "tool": "write_file",
         "parameters": {"filename": "first.txt"}},
        {"step": 2, "id": sid2, "tool": "write_file",
         "parameters": {"filename": "{prev}.txt"}},
    ]
    results = {1: "first", 2: "second"}
    ids = ex._journal_ids(
        completed, results,
        resume={"execution_scope": "task:t23"})
    assert ids[sid2] == ("task:t23:step:bbb222:" + signature(
        "write_file", {"filename": "first.txt"}))
    assert ids[sid2] != ("task:t23:step:bbb222:" + signature(
        "write_file", {"filename": "second.txt"}))
    assert ids[sid1] == ("task:t23:step:aaa111:" + signature(
        "write_file", {"filename": "first.txt"}))
    assert 1 not in ids and 2 not in ids
    assert ex._step_params[sid2] == {"filename": "first.txt"}
    assert ex._step_params[sid1] == {"filename": "first.txt"}


def test_journal_ids_fallback_to_display_number_without_ids():
    # Steps with no stable id keep the legacy number-keyed
    # behaviour (the pass-36 #25 tests pin this shape).
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
    assert ex._step_params[2] == {"filename": "first.txt"}


def test_journal_ids_prefers_persisted_evidence_by_step_id(
        tmp_path, monkeypatch):
    from aria.agent import taskstore as _ts
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    _ts.create("persist23", "goal")
    _ts.merge_step_evidence("persist23", {
        "aaa111": {"op_id": "persisted-op-1",
                   "params": {"filename": "saved.txt"}}})
    ex = _executor(lambda p, s: "x")
    completed = [{"step": 1, "id": "aaa111", "tool": "write_file",
                  "parameters": {"filename": "{prev}.txt"}}]
    ids = ex._journal_ids(
        completed, {1: "r"},
        resume={"execution_scope": "task:persist23"})
    assert ids["aaa111"] == "persisted-op-1"
    assert ex._step_params["aaa111"] == {"filename": "saved.txt"}


def test_journal_ids_legacy_numbered_evidence_still_found(
        tmp_path, monkeypatch):
    # Evidence persisted by the old number-keyed code is still
    # found for an id-carrying step (display-number fallback).
    from aria.agent import taskstore as _ts
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    _ts.create("persist23b", "goal")
    _ts.merge_step_evidence("persist23b", {1: {
        "op_id": "legacy-op-1",
        "params": {"filename": "old.txt"}}})
    ex = _executor(lambda p, s: "x")
    completed = [{"step": 1, "id": "aaa111", "tool": "write_file",
                  "parameters": {"filename": "{prev}.txt"}}]
    ids = ex._journal_ids(
        completed, {1: "r"},
        resume={"execution_scope": "task:persist23b"})
    assert ids["aaa111"] == "legacy-op-1"
    assert ex._step_params["aaa111"] == {"filename": "old.txt"}


def test_build_probes_resolves_id_keyed_journal_ids(
        tmp_path, monkeypatch):
    from aria.tools import builtins
    monkeypatch.setattr(builtins, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "x.txt").write_text("hi", encoding="utf-8")
    op = "task:probe23:step:aaa111:abc"
    assert action_journal.begin(
        op, "write_file", {"filename": "x.txt"}) is None
    action_journal.finish(op, "wrote x.txt")
    assert action_journal.get(op)["state"] == "done"
    steps = grader.normalize_steps([
        {"step": 1, "id": "aaa111", "tool": "write_file",
         "parameters": {"filename": "x.txt"}, "output": "wrote"}])
    probes = grader.build_probes(steps, journal_ids={"aaa111": op})
    journal = [p for p in probes if p["kind"] == "journal"]
    assert len(journal) == 1, probes
    assert journal[0]["state"] == "done"


def test_build_probes_int_step_id_never_masks_display_number(
        tmp_path, monkeypatch):
    # A mission row's int db id must never be tried as a journal
    # key: the display number resolves, even when the int id
    # collides with another step's display number.
    op1 = "mission:9:step:1"
    assert action_journal.begin(op1, "delegate_task", {}) is None
    action_journal.finish(op1, "status=started task_id=c1")
    steps = grader.normalize_steps([
        {"step": 1, "id": 2, "tool": "delegate_task",
         "output": "started"}])
    probes = grader.build_probes(
        steps, journal_ids={1: op1, 2: "mission:9:step:2"})
    journal = [p for p in probes if p["kind"] == "journal"]
    assert len(journal) == 1, probes
    assert journal[0]["state"] == "started"


def test_persist_step_evidence_keyed_by_step_id(
        tmp_path, monkeypatch):
    from aria.agent import taskstore as _ts
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    _ts.create("ev23", "goal")
    ex = _executor(lambda p, s: "x")
    ex._op_ids = {"aaa111": "op-aaa"}
    ex._step_params = {"aaa111": {"filename": "a.txt"}}
    completed = [{"step": 1, "id": "aaa111", "tool": "write_file",
                  "parameters": {"filename": "a.txt"}}]
    with runtime.execution(kind="tool",
                           execution_id="task:ev23"):
        ex._persist_step_evidence(completed, {"aaa111": "op-aaa"})
    got = _ts.get("ev23")["step_evidence"]
    assert got["aaa111"]["op_id"] == "op-aaa"
    assert got["aaa111"]["params"] == {"filename": "a.txt"}
    assert "1" not in got
