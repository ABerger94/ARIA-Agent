"""Pass 26 B1#9: provider failures must never be saved as generated code.

Bug: provider_text returns NONEMPTY bracketed error strings on every
failure path ("[No provider responded.]", "[provider_text error: ...]",
"[Unparseable provider response.]", "[No text in provider response.]").
code_helper "write" checked only `if not code:`, so the error string
passed, was saved via _save_generated, and returned _done; dev_agent
did the same twice over (plan and code).

Fix contract (gaptools consumer seam only — provider_text's contract
is untouched):
- a provider result that is a known failure marker, or empty /
  whitespace-only, is a generation failure: failed outcome, and
  NOTHING is saved.
- genuine code still saves exactly as before.
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

FAILURES = [
    "[No provider responded.]",
    "[provider_text error: boom]",
    "[Unparseable provider response.]",
    "[No text in provider response.]",
    "",
    "   \n  ",
]


@pytest.fixture
def no_save(monkeypatch):
    saved = []

    def fake_save(path, code):
        saved.append((path, code))
        return "saved", f"Successfully wrote {len(code)} characters to {path}."

    monkeypatch.setattr(gap, "_save_generated", fake_save)
    return saved


@pytest.mark.parametrize("bad", FAILURES)
def test_code_helper_write_rejects_provider_failure(bad, no_save, monkeypatch):
    monkeypatch.setattr(gap, "_provider_text", lambda *a, **k: bad)
    out = gap.tool_code_helper({
        "action": "write", "description": "make a thing",
        "output_path": "out.py"})
    assert out.startswith("status=failed"), out
    assert no_save == [], "failure text must never be saved"


@pytest.mark.parametrize("bad", FAILURES)
def test_dev_agent_rejects_provider_failure(bad, no_save, monkeypatch):
    monkeypatch.setattr(gap, "_provider_text", lambda *a, **k: bad)
    out = gap.tool_dev_agent({
        "description": "make a thing", "output_path": "out.py"})
    assert out.startswith("status=failed"), out
    assert no_save == [], "failure text must never be saved"


def test_dev_agent_rejects_failed_code_after_good_plan(no_save, monkeypatch):
    calls = {"n": 0}

    def provider(system, prompt, role="code"):
        calls["n"] += 1
        return "a fine plan" if calls["n"] == 1 else "[No provider responded.]"

    monkeypatch.setattr(gap, "_provider_text", provider)
    out = gap.tool_dev_agent({
        "description": "make a thing", "output_path": "out.py"})
    assert out.startswith("status=failed"), out
    assert no_save == []


def test_genuine_code_still_saves(no_save, monkeypatch):
    monkeypatch.setattr(gap, "_provider_text",
                        lambda *a, **k: "print('hi')\n")
    out = gap.tool_code_helper({
        "action": "write", "description": "make a thing",
        "output_path": "out.py"})
    assert out.startswith("status=done"), out
    assert no_save == [("out.py", "print('hi')")]

    out = gap.tool_dev_agent({
        "description": "make a thing", "output_path": "out.py"})
    assert out.startswith("status=done"), out
    assert len(no_save) == 2
