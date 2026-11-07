"""Pass 27 #2: file tools must never write ARIA's own control files.

settings.json carries safe_mode, so a model write of
{"safe_mode": false} through write_file switched safe mode off
from inside auto mode — and file_organize moving settings.json
out of the workspace root would do the same (settings_load then
reads an empty dict). Reserved workspace-root control files are
refused by write_file and skipped by file_organize; reads stay
allowed and same-named files in subfolders are untouched.
"""
import os
import sys
import tempfile
import types

import pytest

_TMPHOME = tempfile.mkdtemp(prefix="aria_reserved_ctl_")
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
fileops = importlib.import_module("aria.tools.fileops")
workspaces = importlib.import_module("aria.workspaces")
config = importlib.import_module("aria.config")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(builtins, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path))
    return tmp_path


def test_write_file_refuses_settings_json(ws):
    target = ws / "settings.json"
    target.write_text('{"safe_mode": true}', encoding="utf-8")
    out = builtins.tool_write_file("settings.json", '{"safe_mode": false}')
    assert "control file" in str(out), out
    assert target.read_text(encoding="utf-8") == '{"safe_mode": true}'


def test_write_file_refuses_missing_control_file(ws):
    out = builtins.tool_write_file("robot_memory.db", "garbage")
    assert "control file" in str(out), out
    assert not (ws / "robot_memory.db").exists()


def test_write_file_allows_ordinary_and_nested_names(ws):
    out = builtins.tool_write_file("notes.txt", "hello")
    assert "Successfully wrote" in str(out), out
    assert (ws / "notes.txt").read_text(encoding="utf-8") == "hello"
    out = builtins.tool_write_file("sub/settings.json", '{"safe_mode": false}')
    assert "Successfully wrote" in str(out), out
    assert (ws / "sub" / "settings.json").exists()


def test_organize_never_moves_control_files(ws):
    (ws / "settings.json").write_text('{"safe_mode": true}',
                                      encoding="utf-8")
    (ws / "a.txt").write_text("source-a", encoding="utf-8")
    out = fileops.tool_file_organize(str(ws), dry_run=False)
    assert (ws / "settings.json").exists()
    assert (ws / "settings.json").read_text(
        encoding="utf-8") == '{"safe_mode": true}'
    assert (ws / "Documents" / "a.txt").exists()


def test_is_reserved_control_file(ws):
    assert workspaces.is_reserved_control_file(
        str(ws / "settings.json"), str(ws))
    assert workspaces.is_reserved_control_file(
        str(ws / "SETTINGS.JSON"), str(ws))
    assert not workspaces.is_reserved_control_file(
        str(ws / "sub" / "settings.json"), str(ws))
    assert not workspaces.is_reserved_control_file(
        str(ws / "notes.txt"), str(ws))
