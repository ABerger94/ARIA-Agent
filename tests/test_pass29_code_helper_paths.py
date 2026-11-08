"""Pass 29 #41: code_helper reads resolve relative paths the same
way dispatch does — workspace-relative, never cwd-first.

Before: _read_code_arg preferred an existing process-cwd file over
the workspace copy (dispatch root-checked the workspace copy, so
with same.py in both places the check and the read disagreed about
which file they meant). Relative paths now resolve once against
the workspace dir; absolute paths pass through unchanged and are
still root-checked.
"""
import importlib
import os
import sys
import types
from pathlib import Path

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


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    cwd = tmp_path / "cwd"
    ws.mkdir()
    cwd.mkdir()
    monkeypatch.setattr(gap, "_workspace_dir", lambda: ws)
    monkeypatch.chdir(cwd)
    return types.SimpleNamespace(ws=ws, cwd=cwd)


def test_relative_path_reads_workspace_copy_not_cwd(dirs):
    (dirs.cwd / "same.py").write_text("print('cwd copy')")
    (dirs.ws / "same.py").write_text("print('workspace copy')")
    code, err = gap._read_code_arg({"file_path": "same.py"})
    assert err is None, err
    assert code == "print('workspace copy')"


def test_relative_path_only_in_cwd_is_not_found(dirs):
    (dirs.cwd / "only_here.py").write_text("print('cwd only')")
    code, err = gap._read_code_arg({"file_path": "only_here.py"})
    assert code is None
    assert err == "file not found: only_here.py"


def test_absolute_path_still_works(dirs):
    target = dirs.cwd / "abs.py"
    target.write_text("print('absolute')")
    code, err = gap._read_code_arg({"file_path": str(target)})
    assert err is None, err
    assert code == "print('absolute')"


def test_nested_relative_path_resolves_under_workspace(dirs):
    (dirs.ws / "sub").mkdir()
    (dirs.ws / "sub" / "mod.py").write_text("x = 1")
    code, err = gap._read_code_arg({"file_path": "sub/mod.py"})
    assert err is None, err
    assert code == "x = 1"


def test_root_check_still_enforced(dirs, monkeypatch):
    (dirs.ws / "same.py").write_text("print('workspace copy')")
    fake_ws = types.ModuleType("aria.workspaces")
    fake_ws.file_allowed = lambda p: False
    monkeypatch.setitem(sys.modules, "aria.workspaces", fake_ws)
    # `from aria import workspaces` prefers the parent package's
    # attribute once the real module was imported by an earlier
    # test, so set that too.
    monkeypatch.setattr(sys.modules["aria"], "workspaces", fake_ws)
    code, err = gap._read_code_arg({"file_path": "same.py"})
    assert code is None
    assert "outside this project" in err, err
