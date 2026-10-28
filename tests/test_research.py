"""Research briefs (Alek, approved 2026-10-28): search -> read ->
synthesize (cited) -> cross-check -> file. All seams faked; the
filing, index, summary extraction and tool actions are real."""

import importlib
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")


def _pkg(name, path):
    m = types.ModuleType(name)
    m.__path__ = [path]
    sys.modules[name] = m
    return m


# Same harness convention as test_bridge: package stubs keep the
# real aria/__init__ (and its speech chain) out of the sandbox,
# whose venv lacks speech_recognition.
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


_HITS = [
    {"title": "Alpha report", "href": "https://a.example/x",
     "body": "snippet a"},
    {"title": "Beta study", "href": "https://b.example/y",
     "body": "snippet b"},
]

_BRIEF = ("SHORT ANSWER: The widget lasts 40 hours.\n"
          "FINDINGS:\n"
          "- Battery life is 40 hours under test conditions [1]\n"
          "- The beta study measured 38-42 hours [2]\n"
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


def _use_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "RESEARCH_DIR",
                        str(tmp_path / "research"))


def test_run_files_a_cited_crosschecked_brief(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    result = research.run_research("How long does the widget last?",
                                   **_fakes())
    assert result["ok"], result
    assert result["id"] == 1
    assert result["summary"] == "The widget lasts 40 hours."
    assert result["sources"] == 2
    path = os.path.join(research.RESEARCH_DIR, result["file"])
    doc = open(path, encoding="utf-8").read()
    assert "SHORT ANSWER: The widget lasts 40 hours." in doc
    assert "## Cross-check" in doc
    assert "No unsupported claims found." in doc
    assert "1. Alpha report — https://a.example/x" in doc
    assert "2. Beta study — https://b.example/y" in doc
    index = json.load(open(os.path.join(research.RESEARCH_DIR,
                                        "research_index.json"),
                           encoding="utf-8"))
    assert len(index) == 1 and index[0]["id"] == 1
    print("ok run_files_brief")


def test_no_sources_files_nothing(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    result = research.run_research("anything", **_fakes(search=lambda q: []))
    assert not result["ok"] and "No sources" in result["error"]
    assert research.list_research() == []
    print("ok no_sources")


def test_unreadable_sources_fail(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    result = research.run_research("anything",
                                   **_fakes(fetch=lambda url: ""))
    assert not result["ok"] and "none could be read" in result["error"]
    assert research.list_research() == []
    print("ok unreadable_sources")


def test_synth_failure_files_nothing(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    result = research.run_research(
        "anything",
        **_fakes(synth=lambda s, u: "[No provider responded.]"))
    assert not result["ok"] and "Synthesis failed" in result["error"]
    assert research.list_research() == []
    print("ok synth_failure")


def test_tool_list_and_read_round_trip(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    research.run_research("How long does the widget last?", **_fakes())
    listing = research.tool_research({"action": "list"})
    assert "#1" in listing and "widget" in listing
    doc = research.tool_research({"action": "read", "id": "1"})
    assert "Research brief: How long does the widget last?" in doc
    assert "No research brief" in research.tool_research(
        {"action": "read", "id": "99"})
    assert "unknown action" in research.tool_research(
        {"action": "bogus"})
    print("ok tool_round_trip")


def test_section_text_for_briefing_tab(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    assert research.research_section_text() == ""
    research.run_research("How long does the widget last?", **_fakes())
    section = research.research_section_text()
    assert "RESEARCH BRIEFS" in section
    assert "How long does the widget last?" in section
    assert "The widget lasts 40 hours." in section
    print("ok section_text")
