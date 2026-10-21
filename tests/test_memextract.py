"""Unit tests for aria/agent/memextract.py — Merger Phase 2
(blueprint SS2.3). A scripted fake pool stands in for orpool; the
memory DB is redirected to a temp file; _embed is stubbed so no
network is attempted. Verifies: gate NO writes nothing, gate YES
lands facts in ARIA's store with her category mapping, the dedupe
guard, and the kill switch — all without touching the request path.

Run:  python3 tests/_run_suite.py tests/test_memextract.py
"""
import os
import sys
import tempfile
import types

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
memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_memextract_"), "test.db")
memory.init_databases()
memory._embed = lambda text: None  # no network in tests

memextract = importlib.import_module("aria.agent.memextract")


class _FakePool:
    def __init__(self, gate="YES", facts=None, fail=False):
        self.gate = gate
        self.facts = facts or {}
        self.fail = fail
        self.gate_calls = 0
        self.extract_calls = 0

    def chat(self, prompt, system=None, **kwargs):
        self.gate_calls += 1
        if self.fail:
            raise RuntimeError("pool is down")
        return self.gate

    def chat_json(self, prompt, system=None, **kwargs):
        self.extract_calls += 1
        if self.fail:
            raise RuntimeError("pool is down")
        return self.facts


def test_gate_no_writes_nothing():
    pool = _FakePool(gate="NO")
    saved = memextract.extract_now("hello there", "hi", pool=pool)
    assert saved == {}, saved
    assert pool.gate_calls == 1
    assert pool.extract_calls == 0, "gate NO must never reach extraction"
    print("ok gate_no_writes_nothing")


def test_gate_yes_lands_in_her_store():
    facts = {
        "preferences": {"favorite_color": {"value": "blue"}},
        "identity": {"city": {"value": "Baltimore"}},
        "projects": {"robot_body": {"value": "building a desk rover"}},
        "bogus_category": {"x": {"value": "must be dropped"}},
        "notes": {"empty_fact": {"value": "  "}},
    }
    pool = _FakePool(gate="YES", facts=facts)
    saved = memextract.extract_now(
        "my favorite color is blue and I live in Baltimore",
        "Noted.", pool=pool)
    # Her category mapping: preferences -> preference (singular).
    assert saved.get("preference", {}).get("favorite_color") == "blue", saved
    assert saved.get("identity", {}).get("city") == "Baltimore", saved
    assert saved.get("projects", {}).get("robot_body") == "building a desk rover"
    assert "bogus_category" not in saved
    assert "notes" not in saved, "empty values are not saved"
    # And they are really in her store, readable by her own accessors.
    assert memory.memory_get("preference", "favorite_color") == "blue"
    assert memory.memory_get("identity", "city") == "Baltimore"
    print("ok gate_yes_lands_in_her_store")


def test_pool_down_is_soft():
    pool = _FakePool(fail=True)
    saved = memextract.extract_now("remember this thing", "ok", pool=pool)
    assert saved == {}, saved
    print("ok pool_down_is_soft")


def test_async_dedupe_and_short_input():
    memextract._LAST_INPUT = ""
    assert memextract.maybe_extract_async("hi", "hello") is False  # too short
    # First real call spawns the worker (with no key configured in
    # this environment the worker soft-fails inside extract_now).
    assert memextract.maybe_extract_async(
        "my favorite food is pizza", "Noted, pizza.") is True
    # Identical user input never extracts twice (his dedupe guard).
    assert memextract.maybe_extract_async(
        "my favorite food is pizza", "Noted, pizza.") is False
    print("ok async_dedupe_and_short_input")


def test_kill_switch():
    memextract._LAST_INPUT = ""
    saved = config.MEMORY_EXTRACT_ASYNC
    try:
        config.MEMORY_EXTRACT_ASYNC = False
        assert memextract.maybe_extract_async(
            "a completely new input here", "ok") is False
    finally:
        config.MEMORY_EXTRACT_ASYNC = saved
    print("ok kill_switch")
