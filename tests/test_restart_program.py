"""tests/test_restart_program.py — restart_program: "restart your
program" relaunches ARIA herself, never the computer.

The gap this closes (reported by Alek 2026-10-27: "she responds
like she'll restart, but doesn't actually restart the program"):
ARIA-Agent's only restart path was the brain's self-edit watcher,
which fires solely when a watched .py file changed since boot.
A plain "restart" request had NO tool to call — she answered as
if she would restart and nothing relaunched. restart_program is
the Mark-LVI port, Agent-shaped: a schemas declaration in the
core toolkit, a dispatch handler that refuses routine replays
(preauthorized scope) and delegated workers (delegated scope),
routine-capture exclusion, and the relaunch itself in
aria/agent/brain.py riding the self-edit machinery (bounded
speech drain, then _restart_process) behind a one-shot arbiter.

Pinned here:
  * registration — declaration, core toolkit, command guide,
    dispatch registry;
  * the sequence — arm, drain, settle, relaunch, in that order,
    with the arbiter held;
  * honest unavailability — no running script returns a refusal
    and arms/announces nothing; a restart already armed (by this
    tool or by the self-edit timer) is reported, not doubled;
  * exclusions — never approval-gated, never risky-listed, never
    captured into a routine, refused in preauthorized and
    delegated scopes;
  * the prompt routes the phrases to restart_program;
  * _restart_process with no script is a logged False, never a
    silent no-op.
"""
import importlib
import os
import sys
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = str(ROOT / "aria")

_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules.setdefault("aria", _pkg)
_tp = types.ModuleType("aria.tools")
_tp.__path__ = [os.path.join(PKG, "tools")]
sys.modules.setdefault("aria.tools", _tp)
_ap = types.ModuleType("aria.agent")
_ap.__path__ = [os.path.join(PKG, "agent")]
sys.modules.setdefault("aria.agent", _ap)
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
for _name in ("aria.scheduler", "aria.vision", "aria.hardware",
              "aria.spotify", "aria.hud"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["aria.spotify"].tool_media_key = lambda *a, **k: "ok"
sys.modules["aria.spotify"].tool_spotify = lambda *a, **k: "ok"
sys.modules["aria.hud"].tool_show_commands = lambda: "ok"
sys.modules["aria.hud"].tool_hide_commands = lambda: "ok"
sys.modules["aria.hud"].set_hud_state = lambda s: None
sys.modules["aria.hud"].draw_hud = lambda: None


class _Queue:
    unfinished_tasks = 0


_speech = types.ModuleType("aria.speech")
_speech._SPEECH_QUEUE = _Queue()
_speech._AUDIO_PLAY_QUEUE = _Queue()
_speech.edge_tts_bytes = lambda text: b""
sys.modules.setdefault("aria.speech", _speech)

brain = importlib.import_module("aria.agent.brain")
dispatch = importlib.import_module("aria.tools.dispatch")
schemas = importlib.import_module("aria.tools.schemas")


class _SyncThread:
    """threading.Thread stand-in: start() runs the target inline,
    so arm -> drain -> settle -> relaunch lands in one sequence
    the test can read end to end."""

    def __init__(self, target=None, daemon=None, name=None):
        self._target = target

    def start(self):
        self._target()


def setup_function():
    brain._PROGRAM_RESTART_ARMED = False
    brain._RESTART_TIMER = None
    brain.speech._SPEECH_QUEUE.unfinished_tasks = 0
    brain.speech._AUDIO_PLAY_QUEUE.unfinished_tasks = 0


def _armable(monkeypatch, tmp_path, events):
    """Point the brain at a real relaunchable script and record
    the relaunch instead of performing it."""
    script = tmp_path / "aria.py"
    script.write_text("# fake launcher\n")
    monkeypatch.setattr(brain, "_RUNNING_SCRIPT", str(script))
    monkeypatch.setattr(brain, "_restart_process",
                        lambda: events.append(("restart",)) or True)
    monkeypatch.setattr(threading, "Thread", _SyncThread)
    monkeypatch.setattr(time, "sleep",
                        lambda s: events.append(("sleep", s)))


# ── Registration ─────────────────────────────────────────────────────

def test_declared_once_with_no_required_params():
    decls = [d for d in schemas.ALL_FUNCTION_DECLARATIONS
             if d["name"] == "restart_program"]
    assert len(decls) == 1
    assert decls[0]["description"]
    assert not (decls[0]["parameters"].get("required") or [])


def test_in_core_toolkit_and_command_guide():
    assert "restart_program" in schemas.TOOLKITS["core"]["tools"]
    pairs = {(tool, example) for _cat, tool, example
             in schemas.COMMAND_GUIDE}
    assert ("restart_program", "restart your program") in pairs


def test_registered_in_dispatch():
    if not dispatch.get_registered_tools():
        dispatch._init_default_registry()
    assert "restart_program" in dispatch.get_registered_tools()


# ── The sequence: arm -> drain -> settle -> relaunch ─────────────────

def test_restart_drains_settles_and_relaunches(monkeypatch, tmp_path):
    events = []
    _armable(monkeypatch, tmp_path, events)
    result = brain.request_program_restart()
    assert result == "Restarting my program now."
    # Speech never starts: the grace phase polls its full 20
    # attempts, then the settle, then the relaunch.
    assert events[:20] == [("sleep", 0.25)] * 20
    assert events[20:] == [("sleep", 2.0), ("restart",)]
    assert brain._PROGRAM_RESTART_ARMED is True


def test_restart_waits_for_speech_to_drain(monkeypatch, tmp_path):
    events = []
    _armable(monkeypatch, tmp_path, events)
    brain.speech._SPEECH_QUEUE.unfinished_tasks = 2

    def fake_sleep(s):
        events.append(("sleep", s))
        if s == 0.5 and brain.speech._SPEECH_QUEUE.unfinished_tasks:
            brain.speech._SPEECH_QUEUE.unfinished_tasks -= 1

    monkeypatch.setattr(time, "sleep", fake_sleep)
    assert brain.request_program_restart() == "Restarting my program now."
    sleeps = [e[1] for e in events if e[0] == "sleep"]
    assert sleeps == [0.5, 0.5, 2.0]
    assert events[-1] == ("restart",)


# ── Honest unavailability ────────────────────────────────────────────

def test_no_script_to_relaunch_is_an_honest_refusal(monkeypatch):
    monkeypatch.setattr(brain, "_RUNNING_SCRIPT", None)
    result = brain.request_program_restart()
    assert "can't restart my program" in result
    assert brain._PROGRAM_RESTART_ARMED is False


def test_restart_already_armed_is_reported_not_doubled(monkeypatch, tmp_path):
    events = []
    _armable(monkeypatch, tmp_path, events)
    assert brain.request_program_restart() == "Restarting my program now."
    result = brain.request_program_restart()
    assert "already on its way" in result
    assert events.count(("restart",)) == 1


def test_self_edit_restart_pending_is_reported_not_doubled(monkeypatch, tmp_path):
    events = []
    _armable(monkeypatch, tmp_path, events)
    brain._RESTART_TIMER = object()  # a self-edit restart is armed
    result = brain.request_program_restart()
    assert "already on its way" in result
    assert events == []
    assert brain._PROGRAM_RESTART_ARMED is False


def test_restart_process_without_script_is_a_logged_false(monkeypatch):
    monkeypatch.setattr(brain, "_RUNNING_SCRIPT", None)
    assert brain._restart_process() is False


# ── Exclusions ───────────────────────────────────────────────────────

def test_refused_in_preauthorized_scope(monkeypatch, tmp_path):
    events = []
    _armable(monkeypatch, tmp_path, events)
    dispatch._SCOPE.preauthorized = True
    try:
        result = dispatch._tool_restart_program({})
    finally:
        dispatch._SCOPE.preauthorized = False
    assert "refused" in result and "direct" in result
    assert events == [] and brain._PROGRAM_RESTART_ARMED is False


def test_refused_in_delegated_scope(monkeypatch, tmp_path):
    events = []
    _armable(monkeypatch, tmp_path, events)
    with dispatch.delegated_scope():
        assert dispatch.in_delegated_scope() is True
        result = dispatch._tool_restart_program({})
    assert dispatch.in_delegated_scope() is False
    assert "refused" in result and "delegated" in result
    assert events == [] and brain._PROGRAM_RESTART_ARMED is False


def test_never_gated_never_risky_never_captured():
    approval = importlib.import_module("aria.approval")
    sandbox = importlib.import_module("aria.tools.sandbox")
    routines = importlib.import_module("aria.routines")
    assert "restart_program" not in approval.DESTRUCTIVE_TOOLS
    assert "restart_program" not in approval.NEVER_GATED
    assert "restart_program" not in sandbox.RISKY_TOOLS
    assert "restart_program" in routines._EXCLUDED_TOOLS


# ── Prompt routing ───────────────────────────────────────────────────

def test_prompt_routes_the_phrases_to_the_tool():
    src = (ROOT / "aria" / "agent" / "brain.py").read_text(encoding="utf-8")
    assert r'\"restart the app\", or a bare \"restart\" said to you' in src
    assert "as a command, mean restart_program" in src
    assert "never just say you will restart" in src


# ── The voice shortcut: no model in the loop ─────────────────────────

def test_shortcut_restart_bypasses_the_model(monkeypatch):
    shortcuts = importlib.import_module("aria.agent.shortcuts")
    monkeypatch.setattr(brain, "request_program_restart",
                        lambda: "Restarting my program now.")
    out = shortcuts.check_voice_shortcut("restart")
    assert out == ("Restarting my program now.",
                   "Restarting my program now.")
    out = shortcuts.check_voice_shortcut("Restart!")
    assert out[1] == "Restarting my program now."
    out = shortcuts.check_voice_shortcut("restart your program")
    assert out[1] == "Restarting my program now."


def test_shortcut_restart_never_matches_the_computer(monkeypatch):
    shortcuts = importlib.import_module("aria.agent.shortcuts")
    called = []
    monkeypatch.setattr(brain, "request_program_restart",
                        lambda: called.append(1) or "x")
    assert shortcuts.check_voice_shortcut("restart the computer") is None
    assert shortcuts.check_voice_shortcut("restart my pc") is None
    assert called == []


def test_shortcut_restart_speaks_the_refusal(monkeypatch):
    shortcuts = importlib.import_module("aria.agent.shortcuts")
    monkeypatch.setattr(
        brain, "request_program_restart",
        lambda: "I can't restart my program from here — no script.")
    out = shortcuts.check_voice_shortcut("restart yourself")
    assert out[1].startswith("I can't restart my program")
