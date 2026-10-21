"""Unit tests for aria/agent/failures.py — Merger Phase 1 (blueprint SS3.2).

Classifier table, <=15-word user messages, dual-write to the event
ring + incident DB, throttle, and the FAILURE_SURFACING rollback.
Pure local state: the incident DB is redirected to a temp file.

Run:  python3 tests/_run_suite.py tests/test_failures.py
"""
import os
import sys
import tempfile
import types

# Bypass aria/__init__.py's eager hardware imports (same pattern as
# test_providers.py / test_headless.py).
PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

import importlib

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")

# Redirect the memory DB to a temp file BEFORE anything writes to it.
_TMPDB = os.path.join(tempfile.mkdtemp(prefix="aria_failures_"), "test.db")
memory.DB_PATH = _TMPDB
memory.init_databases()

failures = importlib.import_module("aria.agent.failures")


def _fresh():
    failures.clear_throttle()
    config._EVENT_RING.clear()


def _failure_incidents():
    return [r for r in memory.incident_db_list(limit=200)
            if r["category"] == "failure"]


def test_classifier_transient_retry():
    c = failures.classify_failure
    assert c("t", "HTTP 429: rate limit exceeded")["decision"] == "retry"
    assert c("t", "Connection timed out after 30s")["decision"] == "retry"
    assert c("t", "HTTP 503 Service Unavailable")["decision"] == "retry"
    assert c("t", "network drop: connection reset")["decision"] == "retry"
    print("ok classifier_transient_retry")


def test_classifier_budget_escalation():
    c = failures.classify_failure
    # Transient with the budget out: replan when critical, skip when not.
    assert c("t", "HTTP 429", attempts=3, max_attempts=3, critical=True)["decision"] == "replan"
    assert c("t", "HTTP 429", attempts=3, max_attempts=3)["decision"] == "skip"
    # Unknown opaque failure: replan while budget remains, abort when out.
    assert c("t", "opaque boom", attempts=0, max_attempts=3)["decision"] == "replan"
    assert c("t", "opaque boom", attempts=3, max_attempts=3)["decision"] == "abort"
    print("ok classifier_budget_escalation")


def test_classifier_permanent():
    c = failures.classify_failure
    assert c("t", "Permission denied: /etc/x", critical=True)["decision"] == "abort"
    assert c("t", "Permission denied: /etc/x")["decision"] == "skip"
    assert c("t", "HTTP 403 Forbidden", critical=True)["decision"] == "abort"
    print("ok classifier_permanent")


def test_classifier_approach():
    c = failures.classify_failure
    assert c("t", "FileNotFoundError: No such file or directory: 'a.txt'",
             critical=True)["decision"] == "replan"
    assert c("t", "FileNotFoundError: No such file or directory: 'a.txt'")["decision"] == "skip"
    assert c("t", "ModuleNotFoundError: No module named 'cv2'",
             critical=True)["decision"] == "replan"
    assert c("t", "ValueError: invalid literal", critical=True,
             attempts=3, max_attempts=3)["decision"] == "abort"
    print("ok classifier_approach")


def test_classifier_unknown_tool():
    c = failures.classify_failure
    assert c("frobnicate", "Unknown tool: frobnicate",
             unknown_tool=True)["decision"] == "replan"
    print("ok classifier_unknown_tool")


def test_user_messages_within_cap():
    for dec in failures.DECISIONS:
        rec = failures.classify_failure("t", "x", critical=True)
        assert len(rec["user_message"].split()) <= 15, rec
    # Even a rambling caller override is hard-capped at 15 words.
    _fresh()
    rec = failures.surface_failure(
        "cap_test", "opaque boom",
        user_message="one two three four five six seven eight nine ten "
                     "eleven twelve thirteen fourteen fifteen sixteen seventeen")
    assert rec is not None
    assert len(rec["user_message"].split()) <= 15, rec["user_message"]
    print("ok user_messages_within_cap")


def test_surface_dual_write():
    _fresh()
    before = len(_failure_incidents())
    rec = failures.surface_failure(
        "harness.forced", RuntimeError("forced failure for the harness"),
        critical=True)
    assert rec is not None and rec["recorded"], rec
    assert rec["decision"] in failures.DECISIONS
    # Store 1: the event ring carries a classified, user-visible entry.
    ring = [e for e in config.recent_events(50)
            if "Failure [" in e[2] and "harness.forced" in e[2]]
    assert ring, config.recent_events(5)
    assert ring[-1][3].get("failure") is True
    assert ring[-1][3].get("decision") == rec["decision"]
    # Store 2: the incident DB carries the same classification.
    rows = _failure_incidents()
    assert len(rows) == before + 1
    row = rows[0]
    assert row["source"] == "harness.forced"
    assert row["action_taken"] == rec["decision"]
    assert "forced failure" in row["error_text"]
    print("ok surface_dual_write")


def test_surface_throttle():
    _fresh()
    before = len(_failure_incidents())
    recs = [failures.surface_failure("harness.repeat",
                                     ValueError("same opaque failure"))
            for _ in range(5)]
    assert recs[0]["recorded"]
    assert all(not r["recorded"] for r in recs[1:]), recs
    assert recs[-1]["suppressed"] == 4
    assert len(_failure_incidents()) == before + 1
    print("ok surface_throttle")


def test_surface_kill_switch():
    _fresh()
    before = len(_failure_incidents())
    ring_before = len(config.recent_events(1000))
    saved = config.FAILURE_SURFACING
    try:
        config.FAILURE_SURFACING = False
        rec = failures.surface_failure("harness.killed", "opaque boom")
        assert rec is None
    finally:
        config.FAILURE_SURFACING = saved
    assert len(_failure_incidents()) == before
    assert len(config.recent_events(1000)) == ring_before
    print("ok surface_kill_switch")


def test_note_sink_failure_counts():
    before = failures.sink_failure_count("test_sink")
    failures.note_sink_failure("test_sink", RuntimeError("ring broke"))
    assert failures.sink_failure_count("test_sink") == before + 1
    print("ok note_sink_failure_counts")
