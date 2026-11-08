"""Pass 29 #43: research briefs and the index are per-project.

Before: RESEARCH_DIR was global (WORKSPACE_DIR/research) and
_research_dir()/_index_path() never consulted workspaces.current(),
so a brief filed in project B appeared in the default project's
list. Briefs now resolve through workspaces.path("research") per
current project; the default project's path is the legacy global
dir, so existing briefs stay with the default project — history is
neither moved nor duplicated.
"""
import importlib
import json
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")


def _pkg(name, path):
    m = types.ModuleType(name)
    m.__path__ = [path]
    sys.modules[name] = m
    return m


_pkg("aria", PKG)
_pkg("aria.tools", os.path.join(PKG, "tools"))
_pkg("aria.agent", os.path.join(PKG, "agent"))
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
_speech = types.ModuleType("aria.speech")
sys.modules["aria.speech"] = _speech

research = importlib.import_module("aria.research")
config = importlib.import_module("aria.config")
data_dir = importlib.import_module("aria.data_dir")
workspaces = importlib.import_module("aria.workspaces")

_HITS = [
    {"title": "Alpha report", "href": "https://a.example/x",
     "body": "snippet a"},
    {"title": "Beta study", "href": "https://b.example/y",
     "body": "snippet b"},
]

_BRIEF = ("SHORT ANSWER: The widget lasts 40 hours.\n"
          "FINDINGS:\n"
          "- Battery life is 40 hours under test conditions [1]\n"
          "UNCERTAINTIES:\n"
          "- Cold-weather performance is not covered")


def _fakes(**over):
    fakes = {
        "search": lambda q: list(_HITS),
        "fetch": lambda url: f"Full text from {url} with facts.",
        "synth": lambda system, user: _BRIEF,
        "check": lambda system, user: "No unsupported claims found.",
    }
    fakes.update(over)
    return fakes


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(data_dir, "_ROOT", str(tmp_path / "ARIA"))
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path / "ws"))
    monkeypatch.setattr(research, "RESEARCH_DIR",
                        str(tmp_path / "ws" / "research"))
    return tmp_path


def test_default_project_keeps_legacy_global_dir(env):
    # The default project's research dir IS the old global dir:
    # existing briefs stay put, nothing is moved or duplicated.
    assert research._research_dir() == str(env / "ws" / "research")
    result = research.run_research("How long does the widget last?",
                                   **_fakes())
    assert result["ok"], result
    assert (env / "ws" / "research" / result["file"]).exists()
    assert [e["question"] for e in research.list_research()] == [
        "How long does the widget last?"]


def test_brief_in_named_project_invisible_to_default(env):
    research.run_research("How long does the widget last?",
                          **_fakes())
    project_b = workspaces.create("Project Bee")
    with workspaces.use(project_b):
        assert research._research_dir() == str(
            env / "ws" / "projects" / project_b / "research")
        result = research.run_research("How fast is the sprocket?",
                                       **_fakes())
        assert result["ok"], result
        assert result["id"] == 1  # B's own index starts fresh
        assert [e["question"] for e in research.list_research()] == [
            "How fast is the sprocket?"]
        assert (env / "ws" / "projects" / project_b / "research"
                / result["file"]).exists()
    # Back in the default project: only the default brief is listed,
    # and B's brief file is not in the default research dir.
    assert [e["question"] for e in research.list_research()] == [
        "How long does the widget last?"]
    assert not (env / "ws" / "research" / result["file"]).exists()
    # And the default brief is not visible from B.
    with workspaces.use(project_b):
        assert [e["question"] for e in research.list_research()] == [
            "How fast is the sprocket?"]
        assert "How long does the widget last?" not in research.tool_research(
            {"action": "list"})


def test_two_named_projects_do_not_share_briefs(env):
    proj_a = workspaces.create("Project Ay")
    proj_b = workspaces.create("Project Bee")
    with workspaces.use(proj_a):
        research.run_research("Question for A?", **_fakes())
    with workspaces.use(proj_b):
        research.run_research("Question for B?", **_fakes())
        assert [e["question"] for e in research.list_research()] == [
            "Question for B?"]
    with workspaces.use(proj_a):
        assert [e["question"] for e in research.list_research()] == [
            "Question for A?"]
    assert research.list_research() == []  # default sees neither


def test_read_research_is_project_scoped(env):
    project_b = workspaces.create("Project Bee")
    with workspaces.use(project_b):
        research.run_research("How fast is the sprocket?", **_fakes())
    # Default project has no brief #1 of its own.
    assert "No research brief" in research.read_research(1)
