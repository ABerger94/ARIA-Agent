"""Pass 29 #47: research must never report a successful
cross-check when the checker was unavailable.

Before: a missing/bracketed checker reply was replaced with an
"(cross-check unavailable...)" note in the filed doc only, while
run_research returned no verification status, the index entry
carried none, and tool_research unconditionally said
"cross-checked." The verified/unverified status now propagates
from run_research into the index entry, the filed doc, and the
tool response.
"""
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


def _index(tmp_path):
    return json.load(open(os.path.join(
        str(tmp_path / "research"), "research_index.json"),
        encoding="utf-8"))


def test_checker_unavailable_is_unverified_everywhere(tmp_path,
                                                      monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    # Empty reply and bracketed provider-error reply both mean the
    # checker never ran.
    for bad in ("", "[No provider responded.]"):
        result = research.run_research(
            "How long does the widget last?",
            **_fakes(check=lambda s, u, _bad=bad: _bad))
        assert result["ok"], result
        assert result["verification"] == "unverified", result
    index = _index(tmp_path)
    assert len(index) == 2
    assert all(e["verification"] == "unverified" for e in index)
    # The brief is still filed (an unverified draft is useful).
    assert all("cross-check unavailable" in research.read_research(e["id"])
               for e in index)


def test_tool_run_response_says_unverified(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(research, "_ddgs_search", lambda q: list(_HITS))
    monkeypatch.setattr(research, "_fetch_text",
                        lambda url: "Full text with facts.")
    monkeypatch.setattr(research, "_provider",
                        lambda system, user: _BRIEF
                        if "research analyst" in system
                        else "[No provider responded.]")
    out = research.tool_research({"action": "run",
                                  "question": "How long is the widget?"})
    assert "unverified" in out, out
    assert "cross-checked" not in out, out
    listing = research.tool_research({"action": "list"})
    assert "(unverified)" in listing, listing


def test_checker_available_is_verified(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    result = research.run_research("How long does the widget last?",
                                   **_fakes())
    assert result["ok"], result
    assert result["verification"] == "verified", result
    index = _index(tmp_path)
    assert index[0]["verification"] == "verified"
    doc = research.read_research(1)
    assert "· verified" in doc
    # tool_research runs the real seams — patch them to the fakes
    # so the run path is exercised with a working checker.
    monkeypatch.setattr(research, "_ddgs_search", lambda q: list(_HITS))
    monkeypatch.setattr(research, "_fetch_text",
                        lambda url: "Full text with facts.")
    monkeypatch.setattr(research, "_provider",
                        lambda system, user: _BRIEF
                        if "research analyst" in system
                        else "No unsupported claims found.")
    out = research.tool_research({"action": "run",
                                  "question": "How long is the widget?"})
    assert "cross-checked" in out, out
    assert "unverified" not in out, out
    assert "(unverified)" not in research.tool_research(
        {"action": "list"})
