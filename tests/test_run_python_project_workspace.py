"""Pass 28 #25: run_python_code must execute inside the CURRENT
project, not the default workspace.

Before: tool_run_python defaulted workspace_dir to WORKSPACE_DIR
(the install/default workspace, bound at import), and the
dispatch registry passed nothing — so inside a named project
the script was written to, and ran with cwd in, the default
workspace: project files were invisible, and side effects
landed in the wrong project.

Fix contract pinned here:
* with no workspace_dir given, tool_run_python resolves the
  current project's root at call time (workspaces.path, which
  returns WORKSPACE_DIR for the default project);
* an explicit workspace_dir still wins (callers that pin one
  are unaffected).
"""
import importlib
import os
import sys
import types
from pathlib import Path

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg

config = importlib.import_module("aria.config")
data_dir = importlib.import_module("aria.data_dir")
workspaces = importlib.import_module("aria.workspaces")
sandbox = importlib.import_module("aria.tools.sandbox")

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(data_dir, "_ROOT", str(tmp_path / "ARIA"))
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path / "ws"))
    yield types.SimpleNamespace(tmp=tmp_path)


CODE = (
    "import os\n"
    "print(os.getcwd())\n"
    "open('marker_25.txt', 'w').write('here')\n"
)


def test_run_python_runs_in_named_project(env):
    project = workspaces.create("Proj Twenty-Five")
    with workspaces.use(project):
        out = sandbox.tool_run_python(CODE)
    root = Path(str(env.tmp / "ws")) / "projects" / project
    assert str(root) in out, out
    assert (root / "marker_25.txt").read_text() == "here"
    assert not (Path(str(env.tmp / "ws")) / "marker_25.txt").exists()


def test_run_python_runs_in_default_workspace(env):
    out = sandbox.tool_run_python(CODE)
    assert str(env.tmp / "ws") in out, out
    assert (Path(str(env.tmp / "ws")) / "marker_25.txt").exists()


def test_explicit_workspace_dir_still_wins(env):
    pinned = Path(str(env.tmp / "pinned"))
    pinned.mkdir()
    out = sandbox.tool_run_python(CODE, workspace_dir=str(pinned))
    assert str(pinned) in out, out
    assert (pinned / "marker_25.txt").exists()
