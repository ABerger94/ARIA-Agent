"""Pass 26 B3#4: GitHub push / create-repo failures must type FAILED.

Bug: tool_github_push returned "Push failed: {err}" as a plain
string; "Push failed:" is not a FAILED prefix, so
outcome.normalize mapped it to DONE. Same shape in
tool_github_create_repo ("Repo creation failed: ...").

Fix contract: return outcome.failed(...) (via the
legacy_error_results decorator used elsewhere) so normalize
yields FAILED; success paths are unchanged.
"""
import os
import sys
import tempfile
import types

import pytest

_TMPHOME = tempfile.mkdtemp(prefix="aria_github_typed_")
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


def _failing_request(monkeypatch):
    def fake(path, method="GET", payload=None):
        return None, "GitHub API error 500: boom"
    monkeypatch.setattr(builtins, "_github_request", fake)


def test_push_http_error_is_failed(monkeypatch):
    _failing_request(monkeypatch)
    out = builtins.tool_github_push("u/r", "x.py", "print(1)")
    assert normalize(out).status == "failed", out
    assert "Push failed" in out


def test_create_repo_http_error_is_failed(monkeypatch):
    _failing_request(monkeypatch)
    out = builtins.tool_github_create_repo("new-repo")
    assert normalize(out).status == "failed", out
    assert "Repo creation failed" in out


def test_push_success_still_done(monkeypatch):
    def fake(path, method="GET", payload=None):
        if method == "GET":
            return None, None  # no existing file
        return {"sha": "abc"}, None
    monkeypatch.setattr(builtins, "_github_request", fake)
    out = builtins.tool_github_push("u/r", "x.py", "print(1)")
    assert normalize(out).status == "done", out


def test_create_repo_success_still_done(monkeypatch):
    def fake(path, method="GET", payload=None):
        return {"html_url": "https://github.com/u/new-repo"}, None
    monkeypatch.setattr(builtins, "_github_request", fake)
    out = builtins.tool_github_create_repo("new-repo")
    assert normalize(out).status == "done", out
