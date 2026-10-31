"""The dead Confirm of 2026-10-31: an approved shell call that
never executed.

Alek (approval mode: auto) asked ARIA to git pull her code. She
used run_shell_command — whose handler then parked EVERY call
behind the on-screen CONFIRM regardless of mode (by design at
the time: a shell command is arbitrary code, and the mode check
lives in dispatch, which this handler deliberately bypassed).
He tapped CONFIRM. Nothing happened: no pull, no error anywhere
he could see.

Follow-up (same day): Alek ruled "make auto mean auto", and the
handler now executes live shell calls unattended in auto mode
(tests/test_shell_auto_mode.py). The hold/confirm chain below
therefore runs under confirm-risky, where the handler still
parks: the duplicate-guard mechanism under test is identical
there, and in auto there is no longer a hold to confirm.

Root cause, reproduced with the real chain: the handler stashes
the NORMALIZED args ({command, cwd-resolved, timeout_seconds})
and the dispatcher's per-turn duplicate guard had already
cached that exact signature on the first pass — whenever the
model's raw args already matched the normalized shape (a full
schema-shaped call: command + cwd + timeout_seconds). The
approve-token replay re-enters execute_tool with the stashed
args, the duplicate guard eats it BEFORE the handler runs, the
pending entry is already popped, and the banner's click path
discards the "[Duplicate call blocked...]" verdict. No
execution, no retry, no visible error.

The fix (aria/tools/dispatch.py): the duplicate guard exempts a
genuine approve-token replay — identified by the thread-local
marker approval.approve_token sets for exactly this (tool,
args) — while plain preauthorized calls (routine replays, skill
steps) stay guarded. And approve_token's OPS verdict now says
"did NOT execute" when a replay comes back unexecuted, so a
dead Confirm can never again log as a success.

NOTHING in the chain is faked: real aria.approval, real
dispatch.execute_tool, real shelltool handler. Only the
peripheral subsystems the test venv lacks (scheduler / speech /
...) are stubbed at the sys.modules seam, as in
tests/test_confirm_e2e.py, and HOME is a temp dir so the
approval-mode file is the test's own.
"""
import os
import sys
import threading
import time
import types
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Temp HOME with approval mode 'confirm-risky' and a
    captured OPS log. (The incident happened in auto, but since
    Alek's auto-means-auto ruling a live shell call no longer
    parks in auto at all; the hold/confirm chain this suite
    pins lives in the confirm modes now.)"""
    h = tmp_path / "home"
    (h / "ARIA").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    # V20: the approval store resolves via aria.data_dir (the
    # install root), not HOME — redirect the root to the same
    # temp ARIA dir this fixture always used.
    _data_dir = importlib.import_module("aria.data_dir")
    monkeypatch.setattr(_data_dir, "_ROOT", str(h / "ARIA"))
    logs = []
    monkeypatch.setattr(
        config, "add_log",
        lambda msg, *a, **k: logs.append(str(msg)))
    approval._PENDING.clear()
    dispatch.reset_turn_state()
    assert approval.set_mode("confirm-risky").startswith(
        "Approval mode set to 'confirm-risky'")
    assert approval.get_mode() == "confirm-risky"
    yield types.SimpleNamespace(dir=h, logs=logs)
    approval._PENDING.clear()
    dispatch.reset_turn_state()


def _hold_shell(tmp_path, args_shape="full"):
    """One live run_shell_command call through the real
    dispatcher, the way the model issues it. Returns
    (args, marker_path). Asserts it parked without executing."""
    tag = uuid.uuid4().hex[:8]
    marker = tmp_path / f"pulled-{tag}.txt"
    command = f"echo pulled-{tag} > {marker.name}"
    cwd = str(tmp_path.resolve())
    if args_shape == "full":
        # The full schema-shaped call: exactly the dict the
        # handler normalizes to and stashes — the collision case.
        args = {"command": command, "cwd": cwd,
                "timeout_seconds": 60}
    else:
        args = {"command": command, "cwd": cwd}
    result, _ok = dispatch.execute_tool("run_shell_command", args)
    assert "AWAITING_APPROVAL" in str(result), result
    assert not marker.exists()
    entries = approval.pending_entries()
    assert len(entries) == 1
    assert entries[0]["tool"] == "run_shell_command"
    return args, marker, entries[0]["token"]


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


# ── The bug: confirm must execute the held shell call ─────────

def test_confirm_executes_when_model_args_match_the_stash(home,
                                                          tmp_path):
    _args, marker, token = _hold_shell(tmp_path, "full")
    ret = _approve_on_worker_thread(token)
    assert ret.startswith("Exit code: 0"), ret
    assert marker.read_text().startswith("pulled-")
    assert approval.pending_entries() == []
    assert any("executed:" in line for line in home.logs), home.logs
    assert not any("did NOT execute" in line for line in home.logs)


def test_confirm_executes_with_minimal_model_args(home, tmp_path):
    _args, marker, token = _hold_shell(tmp_path, "minimal")
    ret = _approve_on_worker_thread(token)
    assert ret.startswith("Exit code: 0"), ret
    assert marker.exists()
    assert approval.pending_entries() == []


def test_approved_shell_call_executes_exactly_once(home, tmp_path):
    tag = uuid.uuid4().hex[:8]
    runs = tmp_path / f"runs-{tag}.txt"
    args = {"command": f"echo ran >> {runs.name}",
            "cwd": str(tmp_path.resolve()), "timeout_seconds": 60}
    result, _ok = dispatch.execute_tool("run_shell_command", args)
    assert "AWAITING_APPROVAL" in str(result)
    token = approval.pending_entries()[0]["token"]

    ret = _approve_on_worker_thread(token)
    assert ret.startswith("Exit code: 0")
    assert runs.read_text().count("ran") == 1

    # The token is single-use: a second approve resolves nothing.
    again = approval.approve_token(token)
    assert "unknown or expired" in again
    assert runs.read_text().count("ran") == 1

    # And the model re-issuing the identical call in the same
    # turn is duplicate-blocked with the REAL result attached —
    # the call still ran exactly once.
    result2, _ok = dispatch.execute_tool("run_shell_command", args)
    assert str(result2).startswith("[Duplicate call blocked")
    assert "Exit code: 0" in str(result2)
    assert runs.read_text().count("ran") == 1


def test_deny_drops_the_hold_without_executing(home, tmp_path):
    _args, marker, token = _hold_shell(tmp_path, "full")
    out = approval.deny_token(token)
    assert out.startswith("Denied and dropped")
    assert not marker.exists()
    assert approval.pending_entries() == []


# ── The guard itself is intact ────────────────────────────────

def test_duplicate_guard_still_blocks_model_and_plain_preauth(home):
    calls = []

    def handler(a):
        calls.append(dict(a or {}))
        return f"ran-{len(calls)}"

    dispatch.register_tool("dupguard_probe", handler)
    try:
        dispatch.reset_turn_state()
        r1, _ = dispatch.execute_tool("dupguard_probe", {"x": "1"})
        assert r1 == "ran-1"
        # A plain repeated model call: blocked, handler not re-run.
        r2, _ = dispatch.execute_tool("dupguard_probe", {"x": "1"})
        assert str(r2).startswith("[Duplicate call blocked")
        assert len(calls) == 1
        # A preauthorized call WITHOUT the approve marker (the
        # routine/skill shape) is still blocked — the exemption
        # belongs to the user's Confirm, not to preauthorization.
        r3, _ = dispatch.execute_tool(
            "dupguard_probe", {"x": "1"}, preauthorized=True)
        assert str(r3).startswith("[Duplicate call blocked")
        assert len(calls) == 1
        # Different args are a different call and run.
        r4, _ = dispatch.execute_tool("dupguard_probe", {"x": "2"})
        assert r4 == "ran-2"
    finally:
        dispatch._REGISTRY.pop("dupguard_probe", None)
        dispatch.reset_turn_state()


def test_safe_mode_still_blocks_live_calls(home, monkeypatch):
    monkeypatch.setenv("ARIA_SAFE_MODE", "1")
    result, _ok = dispatch.execute_tool(
        "gui_click", {"x": 1, "y": 2})
    assert str(result).startswith("[Safe mode:"), result
    # The shell tool is not in the safe-mode set — its own gate is
    # the protection: even in safe mode a live shell call parks
    # for a human CONFIRM instead of running (mode-independent
    # by design).
    result, _ok = dispatch.execute_tool(
        "run_shell_command",
        {"command": "echo nope", "cwd": str(home.dir)})
    assert "AWAITING_APPROVAL" in str(result)
    assert len(approval.pending_entries()) == 1


# ── approve_token's verdict is honest ─────────────────────────

def test_approve_token_logs_non_execution_as_such(monkeypatch):
    stub = types.SimpleNamespace(
        redact=str, add_log=lambda *a: None,
        log_silent=lambda *a: None)
    logs = []
    stub.add_log = lambda msg, *a, **k: logs.append(str(msg))
    monkeypatch.setattr(approval, "config", stub)
    approval._PENDING.clear()

    fake_dispatch = types.ModuleType("aria.tools.dispatch")
    fake_dispatch.execute_tool = lambda *a, **k: (
        "[Duplicate call blocked: run_shell_command already ran]",
        False)
    monkeypatch.setitem(sys.modules, "aria.tools.dispatch",
                        fake_dispatch)

    approval.request_approval(
        "run_shell_command", {"command": "git pull"})
    token = approval.pending_entries()[0]["token"]
    approval.approve_token(token)
    assert any("did NOT execute" in line for line in logs), logs
    assert not any("token=" + token + " executed:" in line
                   for line in logs)
    approval._PENDING.clear()


# ── Surfaces: the Shell V11 card's resolver ───────────────────

def test_shell_card_resolver_executes_the_hold(home, tmp_path):
    shell_panel = importlib.import_module("aria.shell_panel")
    _args, marker, token = _hold_shell(tmp_path, "full")
    shell_panel._default_resolve_approval(token, True)
    deadline = time.time() + 10
    while time.time() < deadline and not marker.exists():
        time.sleep(0.02)
    assert marker.exists()
    assert approval.pending_entries() == []


# ── Surfaces: the real dashboard banner, clicked ──────────────

def _dashboard_stubs(monkeypatch):
    """Copied from tests/test_confirm_e2e.py (the suite's
    self-contained-file convention): the dashboard's unrelated
    subsystems, with the config stub seeded from the real
    module's values."""
    import numpy as np
    calls = []
    data = dict(cpu=25, mem=40, battery='80%', uptime_s=600,
                active_provider='Ollama',
                subsystems=[('Vision', 'ONLINE', True)],
                task_active=0, scheduled=1, voice_enabled=True,
                volume=.5)
    frame = types.ModuleType('aria.frame')
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: '10m'
    frame.spotify_data = lambda: {'title': 'Test track'}
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
    frame.toggle_voice = lambda: calls.append('voice')
    frame.receive_file = lambda p: calls.append(p)
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = 'Ready'
    hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = calls.append
    hud.set_hud_volume = calls.append
    cfg = types.ModuleType('aria.config')
    for _k in dir(config):
        if not _k.startswith('__'):
            setattr(cfg, _k, getattr(config, _k))
    cfg.redact = str; cfg.log_silent = lambda *args: None
    cfg.add_log = lambda *args: None
    for name, mod in [('aria.frame', frame), ('aria.hud', hud),
                      ('aria.config', cfg)]:
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(sys.modules['aria'],
                            name.rsplit('.', 1)[1], mod,
                            raising=False)
    queue = types.ModuleType('aria.agent.taskqueue')
    queue.get_queue = lambda: types.SimpleNamespace(
        get_all_statuses=lambda: [])
    scheduler = types.ModuleType('aria.scheduler')
    scheduler.sched_list = lambda: []
    monkeypatch.setitem(sys.modules, 'aria.agent.taskqueue', queue)
    monkeypatch.setitem(sys.modules, 'aria.scheduler', scheduler)
    speech = types.ModuleType('aria.speech'); speech.VOICE_ENABLED = True
    monkeypatch.setitem(sys.modules, 'aria.speech', speech)
    monkeypatch.setattr(sys.modules['aria'], 'speech', speech,
                        raising=False)
    return types.SimpleNamespace(
        ptt_press=lambda: calls.append('press'),
        ptt_release=lambda: calls.append('release'),
        interrupt=lambda: calls.append('stop'),
        toggle_whisper=lambda: calls.append('whisper'),
        submit=calls.append, request_stop=lambda: calls.append('shutdown'),
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))


def test_dashboard_banner_click_executes_the_pull(tmp_path,
                                                  monkeypatch):
    pytest.importorskip('PyQt6')
    import numpy as np  # noqa: F401  (harness dependency)
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    callbacks = _dashboard_stubs(monkeypatch)
    # Real HOME + real mode file here (the e2e pattern). The
    # banner is a confirm-mode surface: since the auto-means-
    # auto ruling, a live shell call in auto mode runs without
    # ever raising one, so the hold is made in confirm-risky.
    approval._PENDING.clear()
    prior_mode = approval.get_mode()
    approval.set_mode('confirm-risky')
    dispatch.reset_turn_state()
    try:
        _args, marker, _token = _hold_shell(tmp_path, "full")

        from aria.dashboard import create_dashboard
        window = create_dashboard(callbacks)
        window.show(); app.processEvents()
        window._poll_approvals()
        banner = window._confirm_banner
        assert banner is not None and banner.isVisible()
        QTest.mouseClick(banner._confirm_button,
                         Qt.MouseButton.LeftButton)
        app.processEvents()

        deadline = time.time() + 8
        while time.time() < deadline:
            app.processEvents()
            if marker.exists():
                break
            time.sleep(0.02)
        app.processEvents()
        assert marker.exists()
        assert approval.pending_entries() == []
        assert window._confirm_banner is None
        window.close(); app.processEvents()
    finally:
        approval.set_mode(prior_mode)
        approval._PENDING.clear()
        dispatch.reset_turn_state()
