"""Pass 29 #42: the default project's file namespace must not
include every named project.

Before: named projects live at WORKSPACE_DIR/projects/<id>, beneath
the default project's root, and resolve_workspace_file rooted the
default project at WORKSPACE_DIR with no projects/ exclusion — so
a default-project read_file/write_file of projects/<id>/private.txt
passed. The reserved projects/ subtree is now refused to generic
default-project file tools; named projects keep full access to
their own files, and project-owned storage (workspaces.path,
backup mirror) is untouched — the only cross-project accessor,
backup_mirror, resolves through workspaces.current() directly,
never through the file tools.
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

builtins = importlib.import_module("aria.tools.builtins")
gap = importlib.import_module("aria.tools.gaptools")
workspaces = importlib.import_module("aria.workspaces")
config = importlib.import_module("aria.config")
data_dir = importlib.import_module("aria.data_dir")

PROJ = "a1b2c3d4e5f6"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(builtins, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(data_dir, "_ROOT", str(tmp_path / "ARIA"))
    (tmp_path / "projects" / PROJ).mkdir(parents=True)
    (tmp_path / "projects" / PROJ / "private.txt").write_text(
        "other project secret", encoding="utf-8")
    return tmp_path


def test_default_read_and_write_refuse_projects_subtree(ws):
    rel = f"projects/{PROJ}/private.txt"
    with pytest.raises(ValueError, match="reserved projects"):
        builtins.tool_read_file(rel)
    with pytest.raises(ValueError, match="reserved projects"):
        builtins.tool_write_file(rel, "overwrite")
    assert (ws / "projects" / PROJ / "private.txt").read_text(
        encoding="utf-8") == "other project secret"
    with pytest.raises(ValueError, match="reserved projects"):
        workspaces.resolve_workspace_file(rel, str(ws))


def test_default_ordinary_files_still_work(ws):
    assert "Successfully wrote" in str(
        builtins.tool_write_file("notes.txt", "hello"))
    assert builtins.tool_read_file("notes.txt") == "hello"
    # Only the top-level reserved subtree is excluded: a folder
    # merely named "projects" deeper down is an ordinary folder.
    assert "Successfully wrote" in str(builtins.tool_write_file(
        "sub/projects/x.txt", "nested"))
    assert builtins.tool_read_file("sub/projects/x.txt") == "nested"
    # And a top-level sibling (projectx) is unaffected.
    assert "Successfully wrote" in str(builtins.tool_write_file(
        "projects-backup.txt", "sibling"))
    assert builtins.tool_read_file("projects-backup.txt") == "sibling"


def test_named_project_own_files_still_work(ws):
    project = workspaces.create("Project Forty-Two")
    with workspaces.use(project):
        assert "Successfully wrote" in str(
            builtins.tool_write_file("notes.txt", "mine"))
        assert builtins.tool_read_file("notes.txt") == "mine"
    assert (ws / "projects" / project / "notes.txt").read_text(
        encoding="utf-8") == "mine"
    # Nothing leaked into the default workspace.
    assert not (ws / "notes.txt").exists()


def test_project_storage_itself_untouched(ws):
    # The exclusion is scoped to the generic file tools: project
    # storage still resolves under projects/<id> (this is the path
    # backup_mirror and memory use — no trusted cross-project admin
    # path goes through resolve_workspace_file).
    project = workspaces.create("Project Forty-Two")
    with workspaces.use(project):
        assert workspaces.path("aria_memory.db") == str(
            ws / "projects" / project / "aria_memory.db")


def test_code_helper_refuses_projects_path(ws):
    code, err = gap._read_code_arg(
        {"file_path": f"projects/{PROJ}/private.txt"})
    assert code is None
    assert "reserved projects" in err, err
