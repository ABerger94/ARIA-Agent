"""Pass 28 #11: the default delegated-task verifier must call the
LLM with the documented (prompt, system) signature.

Before: taskexec._verify called the LLM with ONE argument (the
prompt, with the reviewer instruction embedded in it), but the
LLM contract is llm(prompt, system) — planner._default_llm
requires `system` positionally. The call raised TypeError on
every verification, the except path returned "Verification
unavailable; recorded work needs review", and the executor parked
every verified task as blocked. The default verifier could never
pass a task.

Fix contract pinned here: the goal + evidence go as the prompt,
the reviewer instruction goes as the system argument — the same
two-argument call the planner makes. A strict two-argument LLM
(double with no defaults) must receive both.
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

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_taskexec_verify_"), "test.db")
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

planner = importlib.import_module("aria.agent.planner")
taskexec = importlib.import_module("aria.agent.taskexec")


def _executor(llm):
    return taskexec.AgentExecutor(
        llm=llm, summarizer=lambda goal, completed: "SUMMARY-OK",
        sleep_fn=lambda s: None)


def test_default_verifier_calls_llm_with_prompt_and_system():
    calls = []

    def _llm(prompt, system):
        calls.append((prompt, system))
        return "VERDICT: PASS"

    ex = _executor(_llm)
    result = ex._verify(
        "do the thing",
        [{"step": 1, "tool": "fake_tool"}],
        {1: "did the thing"})
    assert result is None, result
    assert len(calls) == 1, calls
    prompt, system = calls[0]
    assert "do the thing" in prompt and "did the thing" in prompt
    assert "skeptical reviewer" in system, system
    assert "VERDICT: PASS" in system, system


def test_default_verifier_fail_returns_the_reason():
    def _llm(prompt, system):
        return "VERDICT: FAIL — the evidence shows nothing was done"

    ex = _executor(_llm)
    result = ex._verify(
        "do the thing",
        [{"step": 1, "tool": "fake_tool"}],
        {1: "nothing here"})
    assert result == "the evidence shows nothing was done", result
