"""Pass 26 B5#2: the first watch added after startup may never
receive hourly checks.

Bug: tool_watch_price inserted the watch and never called
ensure_pricecheck_task(); the only caller was boot-time wiring
(dispatch _init_default_registry, once at import). A boot with
zero watches scheduled nothing, and a later-added watch had no
path to create the hourly pricecheck scheduler task until restart.

Fix contract:
- tool_watch_price() calls pricecheck.ensure_pricecheck_task()
  after a successful insert, best-effort and fail-soft: a
  scheduling failure never breaks adding the watch.
"""
import os
import sys
import tempfile
import types

import pytest

_TMPHOME = tempfile.mkdtemp(prefix="aria_pricecheck_ensure_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg

import importlib  # noqa: E402

from aria import data_dir as _data_dir  # noqa: E402
import aria.config as _config  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "ARIA"
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    _data_dir._ROOT = str(root)
    monkeypatch.setattr(_config, "WORKSPACE_DIR", str(ws))
    legacy = ws / "price_watches.db"
    # Fake scheduler: records pricecheck task creation.
    sched = types.ModuleType("aria.scheduler")
    state = {"kinds": [], "added": []}
    sched.sched_list = lambda: [(i + 1, k) for i, k in enumerate(state["kinds"])]
    sched.sched_add = lambda name, kind, interval_s=None: state["added"].append(
        (name, kind, interval_s))
    monkeypatch.setitem(sys.modules, "aria.scheduler", sched)
    builtins = importlib.import_module("aria.tools.builtins")
    pricecheck = importlib.import_module("aria.pricecheck")
    monkeypatch.setattr(builtins, "PRICE_WATCH_DB", str(legacy))
    monkeypatch.setattr(pricecheck, "PRICE_WATCH_DB", str(legacy))
    monkeypatch.setattr(builtins, "add_log", lambda *a, **k: None)
    monkeypatch.setattr(pricecheck, "add_log", lambda *a, **k: None)
    return {"builtins": builtins, "pricecheck": pricecheck,
            "state": state, "legacy": legacy}


def test_adding_first_watch_schedules_hourly_task(isolated):
    b = isolated["builtins"]
    assert not isolated["legacy"].exists()
    out = b.tool_watch_price("https://example.com/goose", "40", "goose")
    assert "Watching" in out, out
    assert isolated["state"]["added"] == [
        ("pricecheck", "pricecheck", 3600)], isolated["state"]


def test_adding_watch_is_fail_soft_when_scheduling_breaks(isolated, monkeypatch):
    b = isolated["builtins"]
    pc = isolated["pricecheck"]
    monkeypatch.setattr(pc, "ensure_pricecheck_task",
                        lambda: (_ for _ in ()).throw(RuntimeError("no scheduler")))
    out = b.tool_watch_price("https://example.com/goose", "40", "goose")
    assert "Watching" in out, out
    assert "goose" in b.tool_list_price_watches()
