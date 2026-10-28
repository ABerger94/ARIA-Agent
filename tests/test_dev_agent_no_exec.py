"""dev_agent never executes generated code (ChatGPT loop, 2026-10-27).

The bypass: tool_dev_agent called _run_python directly, so a
dev_agent(run=true) call executed arbitrary generated code while the
dispatcher checked safe mode and approval only against the outer tool
name 'dev_agent' — which appears in neither SAFE_MODE_BLOCKED_TOOLS
(aria/config.py) nor DESTRUCTIVE_TOOLS (aria/approval.py), unlike a
direct run_python_code call, which is in both. Sandbox execution is
process isolation only, with ARIA's own filesystem permissions.

The fix: generation and execution are separate. dev_agent saves the
file and refuses run=true; execution happens only through
run_python_code, whose dispatcher checks (safe mode + approval, bound
to the exact code) then apply. These tests pin the refusal from every
angle the old bypass could re-enter through: python, non-python, and
a run helper that raises if it is ever touched.
"""
import importlib
import os
import sys
import types

import pytest

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg

gap = importlib.import_module("aria.tools.gaptools")


class _FakeBuiltins:
    def __init__(self):
        self.writes = {}

    def tool_write_file(self, filename, content):
        self.writes[filename] = content
        return f"Successfully wrote {len(content)} characters to {filename}."


@pytest.fixture
def no_exec(monkeypatch):
    """Provider generates marker code; any execution attempt explodes."""
    monkeypatch.setattr(
        gap, "_provider_text",
        lambda s, p, role="code":
        "```python\nprint('MARKER_SHOULD_NEVER_RUN')\n```")

    def _boom(code):
        raise AssertionError("dev_agent executed generated code")

    monkeypatch.setattr(gap, "_run_python", _boom)
    fake = _FakeBuiltins()
    monkeypatch.setattr(gap, "_builtins", lambda: fake)
    return fake


def test_run_true_python_saves_but_never_executes(no_exec):
    out = gap.tool_dev_agent({"description": "write a marker program",
                              "output_path": "marker.py", "run": True})
    assert out.startswith("status=failed")
    assert "NOT executed" in out
    assert "run_python_code" in out
    # The file IS saved — only execution is refused.
    assert no_exec.writes["marker.py"] == (
        "print('MARKER_SHOULD_NEVER_RUN')")
    # And the marker never ran: _run_python would have raised.


def test_run_true_non_python_also_refused(no_exec):
    out = gap.tool_dev_agent({"description": "write a marker program",
                              "language": "javascript",
                              "output_path": "marker.js", "run": True})
    assert out.startswith("status=failed")
    assert "NOT executed" in out
    assert "marker.js" in no_exec.writes


def test_run_truthy_values_all_refused(no_exec):
    for truthy in (True, 1, "yes", "true"):
        out = gap.tool_dev_agent({"description": "x",
                                  "output_path": "m.py", "run": truthy})
        assert out.startswith("status=failed"), truthy
        assert "NOT executed" in out, truthy


def test_no_run_flag_still_saves_cleanly(no_exec):
    out = gap.tool_dev_agent({"description": "write a marker program",
                              "output_path": "marker.py"})
    assert out.startswith("status=done") and "path=marker.py" in out
    assert no_exec.writes["marker.py"] == (
        "print('MARKER_SHOULD_NEVER_RUN')")


def test_run_python_code_carries_the_policy_dev_agent_skipped():
    """The gated route the fix points at: run_python_code is in
    BOTH policy sets, so routing execution through it (instead of
    dev_agent's direct _run_python call) restores safe-mode and
    approval coverage. This pins the sets, not the dispatcher."""
    import re
    config_src = open(os.path.join(PKG, "config.py"),
                      encoding="utf-8").read()
    approval_src = open(os.path.join(PKG, "approval.py"),
                        encoding="utf-8").read()
    safe_block = re.search(
        r"SAFE_MODE_BLOCKED_TOOLS = frozenset\(\{(.*?)\}\)",
        config_src, re.S).group(1)
    destructive = re.search(
        r"DESTRUCTIVE_TOOLS = frozenset\(\{(.*?)\}\)",
        approval_src, re.S).group(1)
    assert '"run_python_code"' in safe_block
    assert '"run_python_code"' in destructive
    assert '"dev_agent"' not in safe_block
    assert '"dev_agent"' not in destructive
