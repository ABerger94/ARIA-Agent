"""Generated-write approval parity (2026-10-28, at Alek's order —
the pass-2 residual / pass-3 runner-up, closed).

The gap: code_helper(write) and dev_agent saved generated code by
calling builtins.tool_write_file DIRECTLY, while the dispatcher
checks approval only against the OUTER tool name — and neither
code_helper nor dev_agent is in DESTRUCTIVE_TOOLS, whereas
write_file is. So a direct write_file call was held for the
user's tap under confirm-risky, but the same write laundered
through a generator tool landed with no approval at all.

Fix contract pinned here, at the handler level (the dispatcher
level lives in tests/test_dispatch_generated_write.py):
- both tools save through gaptools._write_file_via_dispatch with
  the exact (output_path, code) — fences stripped first;
- a gate result (AWAITING_APPROVAL / duplicate-blocked / …)
  passes through UNCHANGED and is never reported as a save;
- a dispatcher failure or a raising seam is a plain
  status=failed 'saving the file failed', never a save;
- dev_agent with run=true and a HELD write returns the gate
  text: the save has not happened, so there is nothing to run
  and nothing to refuse yet.

Run per-file (own process), like every suite in this repo.
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

HELD = ("[AWAITING_APPROVAL token=abcd1234] write file 'gen.py' "
        "(21 chars). Tell the user what you are about to do and "
        "wait for them to tap Confirm (or Deny) on the approval "
        "banner.")
DUPLICATE = ("[Duplicate call blocked: write_file already ran "
             "with these arguments this turn. Earlier result: x]")


class _WriteFake:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, path, content):
        self.calls.append((path, content))
        if self.result is not None:
            return self.result, False
        return (f"Successfully wrote {len(content)} characters "
                f"to {path}."), False


@pytest.fixture(autouse=True)
def _provider(monkeypatch):
    monkeypatch.setattr(
        gap, "_provider_text",
        lambda s, p, role="code":
        "```python\nprint('generated')\n```")


def _install(monkeypatch, fake):
    monkeypatch.setattr(gap, "_write_file_via_dispatch", fake)
    return fake


def test_code_helper_write_routes_through_gated_dispatch(monkeypatch):
    fake = _install(monkeypatch, _WriteFake())
    out = gap.tool_code_helper({"action": "write",
                                "description": "print generated",
                                "output_path": "gen.py"})
    assert out.startswith("status=done"), out
    # Fences stripped BEFORE the gated write sees the code.
    assert fake.calls == [("gen.py", "print('generated')")]


def test_dev_agent_save_routes_through_gated_dispatch(monkeypatch):
    fake = _install(monkeypatch, _WriteFake())
    out = gap.tool_dev_agent({"description": "say hi",
                              "output_path": "hi.py"})
    assert out.startswith("status=done"), out
    assert fake.calls == [("hi.py", "print('generated')")]


def test_held_write_passes_through_unchanged_from_both_tools(
        monkeypatch):
    _install(monkeypatch, _WriteFake(result=HELD))
    out = gap.tool_code_helper({"action": "write",
                                "description": "print generated",
                                "output_path": "gen.py"})
    assert out == HELD, out
    out = gap.tool_dev_agent({"description": "say hi",
                              "output_path": "gen.py"})
    assert out == HELD, out


def test_dev_agent_run_true_with_held_write_returns_gate_text(
        monkeypatch):
    _install(monkeypatch, _WriteFake(result=HELD))
    out = gap.tool_dev_agent({"description": "say hi",
                              "output_path": "gen.py", "run": True})
    # The write is pending the user's tap — nothing saved, so the
    # run refusal (which speaks of a saved file) must NOT fire.
    assert out == HELD, out
    assert "NOT executed" not in out


def test_duplicate_blocked_passes_through_unchanged(monkeypatch):
    _install(monkeypatch, _WriteFake(result=DUPLICATE))
    out = gap.tool_code_helper({"action": "write",
                                "description": "print generated",
                                "output_path": "gen.py"})
    assert out == DUPLICATE, out


def test_dispatch_failure_is_failed_not_saved(monkeypatch):
    _install(monkeypatch,
             _WriteFake(result="[Tool Error: disk full]"))
    for call in (
            lambda: gap.tool_code_helper(
                {"action": "write", "description": "x",
                 "output_path": "gen.py"}),
            lambda: gap.tool_dev_agent(
                {"description": "x", "output_path": "gen.py"})):
        out = call()
        assert out.startswith("status=failed"), out
        assert "saving the file failed" in out, out


def test_raising_seam_is_failed_not_saved(monkeypatch):
    def _boom(path, content):
        raise RuntimeError("dispatcher down")
    monkeypatch.setattr(gap, "_write_file_via_dispatch", _boom)
    out = gap.tool_dev_agent({"description": "x",
                              "output_path": "gen.py"})
    assert out.startswith("status=failed"), out
    assert "saving the file failed" in out, out


def test_write_file_carries_the_policy_the_generators_lack():
    """Why routing restores parity: write_file is in
    DESTRUCTIVE_TOOLS (approval-gated under confirm-risky); the
    two generator tools are in neither policy set, so the
    dispatcher never gated their direct writes."""
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
    assert '"write_file"' in destructive
    for name in ('"dev_agent"', '"code_helper"'):
        assert name not in safe_block
        assert name not in destructive
