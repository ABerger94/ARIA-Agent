"""find_file must honour configured project roots (pass 28 #34).

The old code always walked Desktop/Documents/Downloads and the
E: drive, ignoring file_roots entirely — so with roots configured
it searched everywhere EXCEPT the project (and leaked the names
of files outside the project). With roots configured, only those
roots are searched; with none configured, the legacy search
stands.

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import tempfile
import types

import pytest

_TMPHOME = tempfile.mkdtemp(prefix="aria_find_file_roots_")
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
workspaces = importlib.import_module("aria.workspaces")


@pytest.fixture
def layout(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "resume.pdf").write_text("project resume")
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Documents" / "resume.pdf").write_text("home resume")
    (home / "Documents" / "elsewhere.txt").write_text("x")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return {"project": project, "home": home}


def _set_roots(monkeypatch, roots):
    monkeypatch.setattr(
        workspaces, "get",
        lambda project=None: {"settings": {"file_roots": list(roots)}})


def test_find_file_searches_configured_roots_only(layout, monkeypatch):
    _set_roots(monkeypatch, [str(layout["project"])])
    out = builtins.tool_find_file("resume", "pdf")
    assert str(layout["project"]) in out, out
    assert str(layout["home"]) not in out, out
    print("ok find_file_searches_configured_roots_only")


def test_find_file_nothing_outside_roots(layout, monkeypatch):
    _set_roots(monkeypatch, [str(layout["project"])])
    out = builtins.tool_find_file("elsewhere", "txt")
    assert "No files matching" in out, out
    assert str(layout["home"]) not in out, out
    print("ok find_file_nothing_outside_roots")


def test_find_file_legacy_roots_when_none_configured(layout, monkeypatch):
    _set_roots(monkeypatch, [])
    out = builtins.tool_find_file("resume", "pdf")
    assert str(layout["home"]) in out, out
    print("ok find_file_legacy_roots_when_none_configured")
