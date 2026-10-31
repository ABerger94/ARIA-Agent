"""The SECOND dead Confirm of 2026-10-31: confirm-all mode.

Alek (approval mode: confirm-all) asked ARIA to pull her code:
`git -C 'E:\\ARIA' pull origin main`. He clicked Confirm. The OPS
log recorded: "token=4861ecd6 executed: status=failed
action=run_shell_command ... reason=\"shell commands never run
inside a routine replay or delegated task — every command needs
a fresh on-screen confirmation...\"". The Confirm worked
mechanically — the token was consumed and approve_token replayed
the call — but the run_shell_command HANDLER refused its own
confirmed call.

Root cause: in confirm-all, approval.needs_approval parks EVERY
tool, so dispatch stashes the model's RAW args
({"command": ...} — no cwd, no timeout_seconds) before the
handler ever runs. The handler's approve-replay check compared
the marker only against its NORMALIZED arg shape
({command, cwd, timeout_seconds}), which can never equal the
raw stash — the marker missed, and the replay fell into the
handler's routine/delegated scope refusal (execute_tool sets the
preauthorized scope around every replay, so that branch always
fires when the marker does). In confirm-risky the same chain
worked, because there dispatch does not park this tool — the
HANDLER parks it, with normalized args, and the replay matched
(tests/test_approve_replay_dupguard.py). Nobody had pinned the
confirm-all path end to end.

The fix (aria/tools/shelltool.py branch (a)): the handler checks
the approve-replay marker against BOTH the args exactly as
received (the raw stash dispatch is replaying) and the
normalized form (its own stash). The scope refusal is untouched:
a genuine routine replay or delegated call carries no marker
for its (tool, args) and is still refused, and a marker set for
one command does not leak to another.

NOTHING in the chain is faked: real aria.approval, real
dispatch.execute_tool, real shelltool handler — same seams as
tests/test_approve_replay_dupguard.py.
"""
import importlib
import os
import sys
import threading
import types
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pkg = types.ModuleType("aria")
pkg.__path__ = [str(Path(__file__).parents[1] / "aria")]
sys.modules.setdefault("aria", pkg)

for _name in ("aria.scheduler", "aria.speech", "aria.hud",
              "aria.frame", "aria.vision", "aria.hardware",
              "aria.spotify", "aria.agent.taskqueue"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

import pytest

approval = importlib.import_module("aria.approval")
config = importlib.import_module("aria.config")
dispatch = importlib.import_module("aria.tools.dispatch")

REFUSAL = "never run inside a routine replay or delegated task"


@pytest.fixture()
def confirm_all(tmp_path, monkeypatch):
    """Temp HOME/data root with approval mode 'confirm-all' and
    a captured OPS log."""
    h = tmp_path / "home"
    (h / "ARIA").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    _data_dir = importlib.import_module("aria.data_dir")
    monkeypatch.setattr(_data_dir, "_ROOT", str(h / "ARIA"))
    logs = []
    monkeypatch.setattr(
        config, "add_log",
        lambda msg, *a, **k: logs.append(str(msg)))
    approval._PENDING.clear()
    dispatch.reset_turn_state()
    approval.set_mode("confirm-all")
    assert approval.get_mode() == "confirm-all"
    yield types.SimpleNamespace(dir=h, logs=logs)
    approval._PENDING.clear()
    dispatch.reset_turn_state()


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


def _park_shell_minimal(tmp_path):
    """One live run_shell_command call in confirm-all with the
    model's minimal raw args (command only — the shape of
    Alek's git pull). Returns (args, marker_path, token).
    Asserts dispatch parked it with the RAW stash."""
    tag = uuid.uuid4().hex[:8]
    marker = tmp_path / f"pulled-{tag}.txt"
    args = {"command": f"echo pulled-{tag} > {marker}"}
    result, _ok = dispatch.execute_tool("run_shell_command", args)
    assert "AWAITING_APPROVAL" in str(result), result
    assert not marker.exists()
    entries = approval.pending_entries()
    assert len(entries) == 1
    assert entries[0]["tool"] == "run_shell_command"
    # The precondition of the bug: in confirm-all the stash is
    # the model's raw args, NOT the handler's normalized shape.
    token = entries[0]["token"]
    assert approval._PENDING[token]["args"] == args
    return args, marker, token


# ── (1) The bug: confirm-all Confirm must EXECUTE the call ────

def test_confirm_all_confirm_executes_the_held_shell_call(
        confirm_all, tmp_path):
    _args, marker, token = _park_shell_minimal(tmp_path)
    ret = _approve_on_worker_thread(token)
    assert ret.startswith("Exit code: 0"), ret
    assert marker.read_text().startswith("pulled-")
    assert approval.pending_entries() == []
    # The OPS verdict must be a real execution, not the refusal
    # dressed as one (Alek's 11:58 log line).
    assert any("executed:" in line and "status=failed" not in line
               for line in confirm_all.logs), confirm_all.logs


def test_confirm_all_confirm_executes_with_cwd_shaped_args(
        confirm_all, tmp_path):
    tag = uuid.uuid4().hex[:8]
    marker = tmp_path / f"pulled-{tag}.txt"
    args = {"command": f"echo pulled-{tag} > {marker.name}",
            "cwd": str(tmp_path.resolve())}
    result, _ok = dispatch.execute_tool("run_shell_command", args)
    assert "AWAITING_APPROVAL" in str(result), result
    token = approval.pending_entries()[0]["token"]
    ret = _approve_on_worker_thread(token)
    assert ret.startswith("Exit code: 0"), ret
    assert marker.read_text().startswith("pulled-")


# ── (2) The guard survives: no marker, no execution ───────────

def test_routine_replay_without_marker_still_refused(
        confirm_all, tmp_path):
    tag = uuid.uuid4().hex[:8]
    marker = tmp_path / f"routine-{tag}.txt"
    args = {"command": f"echo ran > {marker}"}
    # The routine executor's exact shape: a plain preauthorized
    # call. It carries no approve-replay marker, so the handler's
    # scope refusal must fire and nothing may run.
    result, _ok = dispatch.execute_tool(
        "run_shell_command", args, preauthorized=True)
    assert REFUSAL in str(result), result
    assert not marker.exists()


def test_delegated_scope_without_marker_still_refused(
        confirm_all, tmp_path):
    tag = uuid.uuid4().hex[:8]
    marker = tmp_path / f"delegated-{tag}.txt"
    args = {"command": f"echo ran > {marker}"}
    with dispatch.delegated_scope():
        result, _ok = dispatch.execute_tool(
            "run_shell_command", args, preauthorized=True)
    assert REFUSAL in str(result), result
    assert not marker.exists()


# ── (3) The bypass is scoped to exactly the confirmed call ────

def test_marker_for_one_command_does_not_leak_to_another(
        confirm_all, tmp_path):
    args_a, marker_a, token_a = _park_shell_minimal(tmp_path)
    tag_b = uuid.uuid4().hex[:8]
    marker_b = tmp_path / f"other-{tag_b}.txt"
    args_b = {"command": f"echo other-{tag_b} > {marker_b}"}
    result, _ok = dispatch.execute_tool("run_shell_command", args_b)
    assert "AWAITING_APPROVAL" in str(result), result
    assert len(approval.pending_entries()) == 2

    # Confirming A executes A — and only A.
    ret = _approve_on_worker_thread(token_a)
    assert ret.startswith("Exit code: 0"), ret
    assert marker_a.exists()
    assert not marker_b.exists()

    # B is still parked, and a marker-less preauthorized attempt
    # at B's exact call (routine-style) is refused: A's marker
    # did not leak across commands.
    dispatch.reset_turn_state()
    result, _ok = dispatch.execute_tool(
        "run_shell_command", args_b, preauthorized=True)
    assert REFUSAL in str(result), result
    assert not marker_b.exists()
    assert len(approval.pending_entries()) == 1
