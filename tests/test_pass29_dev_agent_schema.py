"""Pass 29 #56: the model-facing dev_agent schema still advertised
removed execution behaviour.

The gap: schemas.py described dev_agent as "With run=true a
Python program is also executed once and its output reported",
while gaptools.py has refused run=true since 2026-10-27
("the program was saved but NOT executed: dev_agent never runs
generated code"). The model was being told about a capability
that does not exist.

The fix: the description now says save-only and points at
run_python_code for execution, and the `run` parameter is gone
from the schema. The handler still accepts — and refuses —
run=true, so stored routines that retain run=true get the clear
saved-not-executed refusal instead of a crash: dispatch
validation skips undeclared params (sandbox.py), so the arg
still reaches the handler.
"""
import importlib
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg

gap = importlib.import_module("aria.tools.gaptools")
schemas = importlib.import_module("aria.tools.schemas")
sandbox = importlib.import_module("aria.tools.sandbox")


def _dev_agent_decl():
    for decl in schemas.ALL_FUNCTION_DECLARATIONS:
        if decl["name"] == "dev_agent":
            return decl
    raise AssertionError("dev_agent declaration missing")


def test_schema_no_longer_advertises_execution():
    decl = _dev_agent_decl()
    desc = decl["description"]
    assert "With run=true" not in desc
    assert "also executed once" not in desc
    assert "never executes" in desc
    assert "run_python_code" in desc
    assert "run" not in decl["parameters"]["properties"]
    assert "description" in decl["parameters"]["properties"]
    assert "output_path" in decl["parameters"]["properties"]


class _FakeWriteDispatch:
    def __init__(self):
        self.writes = {}

    def __call__(self, path, content):
        self.writes[path] = content
        return (f"Successfully wrote {len(content)} characters "
                f"to {path}."), False


def _no_exec(monkeypatch):
    monkeypatch.setattr(
        gap, "_provider_text",
        lambda s, p, role="code":
        "```python\nprint('MARKER_SHOULD_NEVER_RUN')\n```")

    def _boom(code):
        raise AssertionError("dev_agent executed generated code")

    monkeypatch.setattr(gap, "_run_python", _boom)
    fake = _FakeWriteDispatch()
    monkeypatch.setattr(gap, "_write_file_via_dispatch", fake)
    return fake


def test_handler_with_run_true_still_refuses_clearly(monkeypatch):
    fake = _no_exec(monkeypatch)
    out = gap.tool_dev_agent({"description": "write a marker program",
                              "output_path": "marker.py", "run": True})
    assert out.startswith("status=failed")
    assert "NOT executed" in out
    assert "never runs generated code" in out
    assert "run_python_code" in out
    assert fake.writes["marker.py"] == (
        "print('MARKER_SHOULD_NEVER_RUN')")


def test_stored_routine_carrying_run_true_gets_refusal_not_crash(
        monkeypatch):
    """A routine recorded before the schema change replays its
    step verbatim — description, output_path, run=true — through
    dispatch, which passes args through and validates only
    declared params. The step must end in the refusal, never a
    crash or an execution."""
    fake = _no_exec(monkeypatch)
    routine_step = {"description": "write a marker program",
                    "output_path": "routine_marker.py", "run": True}
    # Dispatch validation must not reject the now-undeclared arg.
    assert sandbox.missing_required_args(
        "dev_agent", routine_step) == []
    assert sandbox.mistyped_optional_args(
        "dev_agent", routine_step) == []
    out = gap.tool_dev_agent(routine_step)
    assert out.startswith("status=failed")
    assert "NOT executed" in out
    assert fake.writes["routine_marker.py"] == (
        "print('MARKER_SHOULD_NEVER_RUN')")


def test_no_run_flag_still_saves_cleanly(monkeypatch):
    fake = _no_exec(monkeypatch)
    out = gap.tool_dev_agent({"description": "write a marker program",
                              "output_path": "marker.py"})
    assert out.startswith("status=done") and "path=marker.py" in out
    assert fake.writes["marker.py"] == (
        "print('MARKER_SHOULD_NEVER_RUN')")
