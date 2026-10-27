"""Shell command tool (the Mark-LVI port, 2026-10-27).

The tool's one rule: it shows the command, runs only on CONFIRM,
and never runs in routines/delegation. These pin execution
(_execute, no gate) and the gate order (bad cwd before anything,
preauthorized refusal, one confirmation at a time, live calls
park through approval.request_approval with the exact text).
aria.approval / aria.tools.dispatch are stubbed at the
sys.modules seam, as in tests/test_confirm_banner.py."""
import sys
import types
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

tools_pkg = types.ModuleType('aria.tools')
tools_pkg.__path__ = [str(Path(__file__).parents[1] / 'aria' / 'tools')]
sys.modules.setdefault('aria.tools', tools_pkg)

import importlib
shelltool = importlib.import_module('aria.tools.shelltool')


def _py(code):
    return f'"{sys.executable}" -c "{code}"'


# ── _execute ───────────────────────────────────────────────────────

def test_execute_echo_and_exit_code(tmp_path):
    out = shelltool._execute(_py('print(12345)'), str(tmp_path), 30)
    assert out.startswith('Exit code: 0')
    assert '--- stdout ---' in out and '12345' in out


def test_execute_nonzero_exit_and_stderr(tmp_path):
    out = shelltool._execute(
        _py('import sys; sys.stderr.write(chr(101)); sys.exit(3)'),
        str(tmp_path), 30)
    assert out.startswith('Exit code: 3')
    assert '--- stderr ---' in out


def test_execute_no_output(tmp_path):
    out = shelltool._execute(_py('pass'), str(tmp_path), 30)
    assert out == 'Exit code: 0\n(no output)'


def test_execute_timeout_is_failed_not_output(tmp_path):
    # _execute directly, below the handler's 5s clamp: a 3s sleep
    # against a 1s timeout.
    out = shelltool._execute(
        _py('import time; time.sleep(3)'), str(tmp_path), 1)
    assert 'status=failed' in out and 'timed out after 1s' in out


def test_execute_truncates_to_tail(tmp_path):
    out = shelltool._execute(
        _py('print(chr(120)*9000)'), str(tmp_path), 30)
    assert '[output truncated]' in out
    body = out.split('\n', 1)[1]
    assert len(body) <= shelltool.OUTPUT_LIMIT + len('[output truncated]\n')
    assert body.endswith('x' * 100)


def test_execute_start_error_is_failed(tmp_path):
    out = shelltool._execute(_py('pass'), str(tmp_path / 'missing'), 30)
    assert 'status=failed' in out and 'could not be started' in out


# ── _normalize ─────────────────────────────────────────────────────

def test_normalize_clamps_and_is_idempotent(tmp_path):
    n = shelltool._normalize(
        {'command': '  echo hi  ', 'cwd': str(tmp_path),
         'timeout_seconds': 9999})
    assert n == {'command': 'echo hi', 'cwd': str(tmp_path.resolve()),
                 'timeout_seconds': 300}
    assert shelltool._normalize(n) == n
    low = shelltool._normalize({'command': 'x', 'timeout_seconds': 1})
    assert low['timeout_seconds'] == 5 and low['cwd'] == ''
    dflt = shelltool._normalize({'command': 'x'})
    assert dflt['timeout_seconds'] == 60


# ── The gate ───────────────────────────────────────────────────────

class _Gate:
    """Fake approval + dispatch modules at the sys.modules seam."""

    def __init__(self, monkeypatch, *, replay=False, preauth=False,
                 delegated=None, pending=()):
        self.parked = []
        approval = types.ModuleType('aria.approval')
        approval.in_approve_replay = (
            lambda tool, args: replay)
        approval.pending_entries = lambda: list(pending)
        approval.request_approval = self._request
        dispatch = types.ModuleType('aria.tools.dispatch')
        dispatch.in_preauthorized_scope = lambda: preauth
        if delegated is not None:
            dispatch.in_delegated_scope = lambda: delegated
        monkeypatch.setitem(sys.modules, 'aria.approval', approval)
        monkeypatch.setitem(sys.modules, 'aria.tools.dispatch', dispatch)

    def _request(self, tool, args, desc=None):
        self.parked.append((tool, args, desc))
        return f'[AWAITING_APPROVAL token=abcd] {desc}'


def test_bad_cwd_refused_before_any_gate(monkeypatch, tmp_path):
    gate = _Gate(monkeypatch, replay=True, preauth=True)
    out = shelltool.tool_run_shell_command(
        {'command': 'echo hi', 'cwd': str(tmp_path / 'no-such-dir')})
    assert 'status=failed' in out
    assert 'working directory does not exist' in out
    assert gate.parked == []


def test_preauthorized_scope_refuses(monkeypatch, tmp_path):
    gate = _Gate(monkeypatch, preauth=True)
    out = shelltool.tool_run_shell_command(
        {'command': 'echo hi', 'cwd': str(tmp_path)})
    assert 'status=failed' in out
    assert 'never run inside a routine replay or delegated task' in out
    assert 'Nothing was run' in out
    assert gate.parked == []


def test_delegated_scope_refuses(monkeypatch, tmp_path):
    gate = _Gate(monkeypatch, delegated=True)
    out = shelltool.tool_run_shell_command(
        {'command': 'echo hi', 'cwd': str(tmp_path)})
    assert 'status=failed' in out and 'Nothing was run' in out
    assert gate.parked == []


def test_missing_delegated_scope_falls_back_to_false(monkeypatch, tmp_path):
    # No in_delegated_scope attribute at all: the live call parks.
    gate = _Gate(monkeypatch, delegated=None)
    out = shelltool.tool_run_shell_command(
        {'command': 'echo hi', 'cwd': str(tmp_path)})
    assert out.startswith('[AWAITING_APPROVAL')
    assert len(gate.parked) == 1


def test_pending_confirmation_asks_instead_of_parking(monkeypatch, tmp_path):
    gate = _Gate(monkeypatch, pending=[{'token': 't', 'tool': 'x'}])
    out = shelltool.tool_run_shell_command(
        {'command': 'echo hi', 'cwd': str(tmp_path)})
    assert 'status=ask' in out
    assert 'already a confirmation waiting on screen' in out
    assert gate.parked == []


def test_approve_replay_executes(monkeypatch, tmp_path):
    gate = _Gate(monkeypatch, replay=True)
    out = shelltool.tool_run_shell_command(
        {'command': _py('print(777)'), 'cwd': str(tmp_path)})
    assert out.startswith('Exit code: 0') and '777' in out
    assert gate.parked == []


def test_live_call_parks_with_exact_desc(monkeypatch, tmp_path):
    gate = _Gate(monkeypatch)
    cmd = _py('print(1)')
    out = shelltool.tool_run_shell_command(
        {'command': cmd, 'cwd': str(tmp_path), 'timeout_seconds': 45})
    assert out.startswith('[AWAITING_APPROVAL')
    assert len(gate.parked) == 1
    tool, args, desc = gate.parked[0]
    assert tool == 'run_shell_command'
    # The stashed args are the normalized dict the replay re-sends.
    assert args == shelltool._normalize(
        {'command': cmd, 'cwd': str(tmp_path), 'timeout_seconds': 45})
    expected = (
        f"Run shell command\n"
        f"Command: {cmd}\n"
        f"Working directory: {tmp_path.resolve()}\n"
        f"Timeout: 45s\n"
        f"This runs in the system shell with the signed-in user's "
        f"full permissions, exactly as if typed into a terminal. "
        f"It runs only if the user presses CONFIRM (or approves "
        f"the token).")
    assert desc == expected


def test_no_command_is_failed(monkeypatch, tmp_path):
    gate = _Gate(monkeypatch)
    out = shelltool.tool_run_shell_command(
        {'command': '   ', 'cwd': str(tmp_path)})
    assert 'status=failed' in out and 'no command was given' in out
    assert gate.parked == []
