"""Pass 26 B1#8: invalid content must not truncate the existing file,
and a mid-write failure must never leave a truncated destination.

Bug: tool_write_file opened the destination with "w" (truncate)
then f.write(content); content=42 raised TypeError after the file
was already empty.

Fix contract:
- non-string content is rejected with a failed outcome BEFORE
  the file is opened;
- writes go through a temp file in the same directory + os.replace,
  so a failure at any point leaves the original byte-identical;
- normal writes still land, preserving the existing file's mode.
"""
import os
import stat
import sys
import tempfile
import types

import pytest

_TMPHOME = tempfile.mkdtemp(prefix="aria_write_atomic_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg

import importlib  # noqa: E402

builtins = importlib.import_module("aria.tools.builtins")
from aria.outcome import normalize  # noqa: E402


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(builtins, "WORKSPACE_DIR", str(tmp_path))
    return tmp_path


def test_non_string_content_leaves_file_intact(ws):
    target = ws / "notes.txt"
    target.write_text("original content", encoding="utf-8")
    out = builtins.tool_write_file("notes.txt", 42)
    assert normalize(out).status == "failed", out
    assert target.read_text(encoding="utf-8") == "original content"
    assert target.read_bytes() == b"original content"


def test_non_string_content_types_rejected(ws):
    target = ws / "notes.txt"
    target.write_text("keep me", encoding="utf-8")
    for bad in (42, 3.5, None, ["a"], {"a": 1}, True):
        out = builtins.tool_write_file("notes.txt", bad)
        assert normalize(out).status == "failed", (bad, out)
        assert target.read_text(encoding="utf-8") == "keep me"


def test_mid_write_failure_leaves_original_intact(ws, monkeypatch):
    target = ws / "notes.txt"
    target.write_text("original content", encoding="utf-8")
    # Fail at the replace step: temp file is written, the
    # destination is never swapped in.
    monkeypatch.setattr(os, "replace",
                        lambda src, dst: (_ for _ in ()).throw(
                            OSError("disk full")))
    out = builtins.tool_write_file("notes.txt", "new content")
    assert normalize(out).status == "failed", out
    assert target.read_text(encoding="utf-8") == "original content"
    # No temp litter left behind in the workspace.
    assert [p.name for p in ws.iterdir()] == ["notes.txt"]


def test_normal_write_lands_and_preserves_mode(ws):
    target = ws / "sub" / "notes.txt"
    target.parent.mkdir()
    target.write_text("old", encoding="utf-8")
    os.chmod(target, 0o640)
    out = builtins.tool_write_file("sub/notes.txt", "new content")
    assert "Successfully wrote" in out, out
    assert target.read_text(encoding="utf-8") == "new content"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o640


def test_new_file_write_still_works(ws):
    out = builtins.tool_write_file("fresh.txt", "hello")
    assert "Successfully wrote" in out, out
    assert (ws / "fresh.txt").read_text(encoding="utf-8") == "hello"
