"""Frozen-build script running (pass 25 P2#17).

tool_run_python used to pass a .py file to sys.executable — in a
frozen build that is ARIA.exe, which ignores the argument and
boots a second ARIA instead of running the script. aria.py now
has a --run-script early flag (same shape as --diagnostics /
--desktop-node), and the sandbox routes frozen runs through it.
"""
import subprocess
import sys
import types
from pathlib import Path

from aria.tools import sandbox

ARIA_PY = Path(__file__).parents[1] / "aria.py"


def _run_aria_py(script: Path, *extra):
    return subprocess.run(
        [sys.executable, str(ARIA_PY), *extra, str(script)],
        capture_output=True, text=True, timeout=30)


def test_run_script_flag_runs_code(tmp_path):
    script = tmp_path / "hello.py"
    script.write_text("print('frozen-run-works')\n")
    result = _run_aria_py(script, "--run-script")
    assert result.returncode == 0, result.stderr
    assert "frozen-run-works" in result.stdout


def test_run_script_flag_propagates_exit_code(tmp_path):
    script = tmp_path / "exit7.py"
    script.write_text("import sys; sys.exit(7)\n")
    result = _run_aria_py(script, "--run-script")
    assert result.returncode == 7


def test_run_script_flag_failure_exits_nonzero(tmp_path):
    script = tmp_path / "boom.py"
    script.write_text("raise RuntimeError('boom')\n")
    result = _run_aria_py(script, "--run-script")
    assert result.returncode == 1
    assert "RuntimeError" in result.stderr


def test_tool_run_python_frozen_routes_through_flag(monkeypatch, tmp_path):
    # The frozen branch must not pass a bare .py to the exe: the
    # command is [exe, --run-script, script]. The subprocess is
    # stubbed; only the routing is pinned here.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return types.SimpleNamespace(stdout="ok", stderr="",
                                     returncode=0)

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    out = sandbox.tool_run_python("print(1)",
                                  workspace_dir=str(tmp_path))
    assert out == "ok"
    cmd = seen["cmd"]
    assert cmd[0] == sys.executable
    assert cmd[1] == "--run-script"
    assert cmd[2].endswith(".py")


def test_tool_run_python_unfrozen_routes_direct(monkeypatch, tmp_path):
    # Control: without the frozen attribute the command is the
    # plain [interpreter, script] shape the suite already relies on.
    monkeypatch.delattr(sys, "frozen", raising=False)
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return types.SimpleNamespace(stdout="ok", stderr="",
                                     returncode=0)

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    sandbox.tool_run_python("print(1)", workspace_dir=str(tmp_path))
    cmd = seen["cmd"]
    assert cmd == [sys.executable, cmd[1]]
    assert cmd[1].endswith(".py")
