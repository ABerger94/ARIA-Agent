"""Auto means auto for the shell tool (Alek's ruling, 2026-10-31).

run_shell_command's handler parked EVERY live call behind the
on-screen CONFIRM regardless of approval mode — a deliberate
LVI-port rule (a shell command is arbitrary code, and the mode
check lives in dispatch, which the handler bypassed by calling
approval.request_approval itself). The dead-Confirm fix
(37fa5fce) made that CONFIRM work; Alek then ruled on the
posture itself: "Make auto mean auto: no shell confirm in auto
mode."

So the handler now consults the SAME mode source dispatch's
needs_approval consults — aria.approval.get_mode() — and:

  - mode 'auto': a LIVE call executes immediately. No pending
    entry, no banner, no approval state of any kind.
  - modes 'confirm-risky' / 'confirm-all': unchanged — the call
    parks, and the user's Confirm executes it exactly once (the
    test_approve_replay_dupguard chain).
  - safe mode: the auto lane is closed — a live call parks for a
    human CONFIRM in every mode, exactly as before. run_shell_
    command is deliberately NOT in SAFE_MODE_BLOCKED_TOOLS; its
    gate is the protection, so safe mode must never become an
    unattended-execution hole via auto.
  - the scope rules are untouched: a shell command still never
    rides a preauthorized (routine replay) or delegated scope,
    in any mode. Auto changed what a live call does, not who
    may pre-approve one.

The chain is real (aria.approval, dispatch.execute_tool, the
shelltool handler), as in tests/test_approve_replay_dupguard.py;
only peripheral subsystems are stubbed, and HOME is a temp dir
so the approval-mode file is the test's own.
"""
import sys
import threading
import types
import uuid
from pathlib import Path

pkg = types.ModuleType("aria")
pkg.__path__ = [str(Path(__file__).parents[1] / "aria")]
sys.modules.setdefault("aria", pkg)

# Peripheral subsystems dispatch binds at import time but this
# chain never calls (same seam as test_confirm_e2e.py).
for _name in ("aria.scheduler", "aria.speech", "aria.hud",
              "aria.frame", "aria.vision", "aria.hardware",
              "aria.spotify", "aria.agent.taskqueue"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

import importlib

import pytest

approval = importlib.import_module("aria.approval")
config = importlib.import_module("aria.config")
dispatch = importlib.import_module("aria.tools.dispatch")
shelltool = importlib.import_module("aria.tools.shelltool")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Temp HOME + captured OPS log; each test sets its mode."""
    h = tmp_path / "home"
    (h / "ARIA").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("ARIA_SAFE_MODE", raising=False)
    logs = []
    monkeypatch.setattr(
        config, "add_log",
        lambda msg, *a, **k: logs.append(str(msg)))
    approval._PENDING.clear()
    dispatch.reset_turn_state()
    yield types.SimpleNamespace(dir=h, logs=logs)
    approval._PENDING.clear()
    dispatch.reset_turn_state()


def _set_mode(mode):
    approval.set_mode(mode)
    assert approval.get_mode() == mode


def _shell_call(tmp_path, shape="full"):
    """A marker-writing shell call through the real dispatcher,
    the way the model issues it. Returns (args, marker)."""
    tag = uuid.uuid4().hex[:8]
    marker = tmp_path / f"ran-{tag}.txt"
    command = f"echo ran-{tag} > {marker.name}"
    cwd = str(tmp_path.resolve())
    if shape == "full":
        args = {"command": command, "cwd": cwd,
                "timeout_seconds": 60}
    else:
        args = {"command": command, "cwd": cwd}
    return args, marker


def _approve_on_worker_thread(token):
    """approve_token exactly as the dashboard banner and the
    Shell V11 card run it: on a fresh worker thread."""
    out = {}

    def work():
        out["ret"] = approval.approve_token(token)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout=30)
    assert not t.is_alive()
    return out["ret"]


# ── Auto: a live call just runs ───────────────────────────────

def test_auto_executes_immediately_full_shape(env, tmp_path):
    _set_mode("auto")
    args, marker = _shell_call(tmp_path, "full")
    result, _ok = dispatch.execute_tool("run_shell_command", args)
    assert "AWAITING_APPROVAL" not in str(result), result
    assert str(result).startswith("Exit code: 0"), result
    assert marker.read_text().startswith("ran-")
    # Zero approval state was created along the way.
    assert approval.pending_entries() == []
    assert approval._PENDING == {}


def test_auto_executes_immediately_minimal_shape(env, tmp_path):
    _set_mode("auto")
    args, marker = _shell_call(tmp_path, "minimal")
    result, _ok = dispatch.execute_tool("run_shell_command", args)
    assert "AWAITING_APPROVAL" not in str(result), result
    assert str(result).startswith("Exit code: 0"), result
    assert marker.exists()
    assert approval.pending_entries() == []
    assert approval._PENDING == {}


def test_auto_still_refuses_a_preauthorized_replay(env, tmp_path):
    # Auto widened the LIVE call only: a shell step inside a
    # routine replay (preauthorized scope) is still refused —
    # the command string is the whole program, and "the routine
    # was trusted" is not an answer to "run THIS command now".
    _set_mode("auto")
    args, marker = _shell_call(tmp_path, "full")
    result, _ok = dispatch.execute_tool(
        "run_shell_command", args, preauthorized=True)
    assert not marker.exists()
    assert "Exit code" not in str(result)
    assert approval.pending_entries() == []


# ── Confirm modes: held, and Confirm executes exactly once ────

def test_confirm_risky_holds_then_confirm_executes_full(env,
                                                        tmp_path):
    _set_mode("confirm-risky")
    args, marker = _shell_call(tmp_path, "full")
    result, _ok = dispatch.execute_tool("run_shell_command", args)
    assert "AWAITING_APPROVAL" in str(result), result
    assert not marker.exists()
    entries = approval.pending_entries()
    assert len(entries) == 1
    assert entries[0]["tool"] == "run_shell_command"

    ret = _approve_on_worker_thread(entries[0]["token"])
    assert ret.startswith("Exit code: 0"), ret
    assert marker.read_text().startswith("ran-")
    assert approval.pending_entries() == []


def test_confirm_risky_holds_then_confirm_executes_minimal(env,
                                                           tmp_path):
    _set_mode("confirm-risky")
    args, marker = _shell_call(tmp_path, "minimal")
    result, _ok = dispatch.execute_tool("run_shell_command", args)
    assert "AWAITING_APPROVAL" in str(result), result
    assert not marker.exists()
    token = approval.pending_entries()[0]["token"]

    ret = _approve_on_worker_thread(token)
    assert ret.startswith("Exit code: 0"), ret
    assert marker.exists()
    assert approval.pending_entries() == []


def test_confirm_all_holds_the_call(env, tmp_path):
    _set_mode("confirm-all")
    args, marker = _shell_call(tmp_path, "full")
    result, _ok = dispatch.execute_tool("run_shell_command", args)
    assert "AWAITING_APPROVAL" in str(result), result
    assert not marker.exists()
    entries = approval.pending_entries()
    assert len(entries) == 1
    assert entries[0]["tool"] == "run_shell_command"


# ── Safe mode: never unattended, in any mode ──────────────────

@pytest.mark.parametrize("mode", ["auto", "confirm-risky",
                                  "confirm-all"])
def test_safe_mode_parks_instead_of_executing(env, tmp_path,
                                              monkeypatch, mode):
    # run_shell_command is not in SAFE_MODE_BLOCKED_TOOLS by
    # design — its gate is the protection. Safe mode therefore
    # closes the auto lane too: the call parks for a human
    # CONFIRM rather than running, in every mode.
    _set_mode(mode)
    monkeypatch.setenv("ARIA_SAFE_MODE", "1")
    assert config.is_safe_mode()
    args, marker = _shell_call(tmp_path, "full")
    result, _ok = dispatch.execute_tool("run_shell_command", args)
    assert not marker.exists()
    assert "AWAITING_APPROVAL" in str(result), result
    assert len(approval.pending_entries()) == 1


# ── The handler reads the mode fail-soft ──────────────────────

class _FakeApproval:
    """The shelltool handler's lazy seam, faked: a mode source
    and a parking recorder (the test_shelltool.py pattern)."""

    def __init__(self, monkeypatch, *, mode=None, raises=False,
                 safe_mode=False):
        self.parked = []
        mod = types.ModuleType("aria.approval")
        if raises:
            def get_mode():
                raise RuntimeError("mode store broken")
            mod.get_mode = get_mode
        elif mode is not None:
            mod.get_mode = lambda: mode
        # mode=None: no get_mode attribute at all.
        mod.in_approve_replay = lambda tool, args: False
        mod.pending_entries = lambda: []
        mod.request_approval = self._request
        monkeypatch.setitem(sys.modules, "aria.approval", mod)
        disp = types.ModuleType("aria.tools.dispatch")
        disp.in_preauthorized_scope = lambda: False
        disp.in_delegated_scope = lambda: False
        monkeypatch.setitem(sys.modules, "aria.tools.dispatch", disp)
        cfg = types.ModuleType("aria.config")
        cfg.is_safe_mode = lambda: safe_mode
        monkeypatch.setitem(sys.modules, "aria.config", cfg)

    def _request(self, tool, args, desc=None):
        self.parked.append((tool, args, desc))
        return "[AWAITING_APPROVAL token=<redacted>] parked"


def test_handler_executes_when_fake_mode_is_auto(monkeypatch,
                                                 tmp_path):
    fake = _FakeApproval(monkeypatch, mode="auto")
    out = shelltool.tool_run_shell_command(
        {"command": "echo fake-auto", "cwd": str(tmp_path)})
    assert out.startswith("Exit code: 0"), out
    assert fake.parked == []


def test_handler_parks_when_mode_source_is_missing(monkeypatch,
                                                   tmp_path):
    # A partial approval module (no get_mode) reads as not-auto:
    # the pre-ruling behaviour, never unattended execution.
    fake = _FakeApproval(monkeypatch, mode=None)
    out = shelltool.tool_run_shell_command(
        {"command": "echo fake-none", "cwd": str(tmp_path)})
    assert out.startswith("[AWAITING_APPROVAL"), out
    assert len(fake.parked) == 1


def test_handler_parks_when_mode_read_raises(monkeypatch,
                                             tmp_path):
    fake = _FakeApproval(monkeypatch, raises=True)
    out = shelltool.tool_run_shell_command(
        {"command": "echo fake-raise", "cwd": str(tmp_path)})
    assert out.startswith("[AWAITING_APPROVAL"), out
    assert len(fake.parked) == 1


def test_handler_auto_lane_closed_in_safe_mode(monkeypatch,
                                                 tmp_path):
    # Auto + safe mode: the call parks like a confirm-mode call
    # — safe mode never gets an unattended shell execution.
    fake = _FakeApproval(monkeypatch, mode="auto", safe_mode=True)
    out = shelltool.tool_run_shell_command(
        {"command": "echo fake-safe", "cwd": str(tmp_path)})
    assert out.startswith("[AWAITING_APPROVAL"), out
    assert len(fake.parked) == 1
