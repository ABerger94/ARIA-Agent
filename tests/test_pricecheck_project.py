"""Pass 26 B5#1: price watches must be per-project, not installation-wide.

Bug: builtins._price_db() opened sqlite3.connect(PRICE_WATCH_DB)
directly — config.PRICE_WATCH_DB is one WORKSPACE_DIR file, never
routed through workspaces.path() (contrast the per-project files
in workspaces.py). A watch added in project A showed up in
project B, and unwatch in one project deleted another's watch.

Fix contract:
- the DB path is resolved via workspaces.path("price_watches.db",
  PRICE_WATCH_DB) in _price_db() and in pricecheck.py, so each
  project gets its own price_watches.db and the default project
  keeps the legacy file.
"""
import os
import sys
import tempfile
import types

import pytest

_TMPHOME = tempfile.mkdtemp(prefix="aria_pricecheck_project_")
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
    builtins = importlib.import_module("aria.tools.builtins")
    pricecheck = importlib.import_module("aria.pricecheck")
    monkeypatch.setattr(builtins, "PRICE_WATCH_DB", str(legacy))
    monkeypatch.setattr(pricecheck, "PRICE_WATCH_DB", str(legacy))
    monkeypatch.setattr(builtins, "add_log", lambda *a, **k: None)
    monkeypatch.setattr(pricecheck, "add_log", lambda *a, **k: None)
    from aria import workspaces
    return {"builtins": builtins, "pricecheck": pricecheck,
            "workspaces": workspaces, "ws": ws, "legacy": legacy}


def test_watch_in_project_a_invisible_from_project_b(isolated):
    b = isolated["builtins"]
    wsmod = isolated["workspaces"]
    proj_a = wsmod.create("Project A")
    proj_b = wsmod.create("Project B")
    with wsmod.use(proj_a):
        out = b.tool_watch_price("https://example.com/goose", "40", "goose")
        assert "Watching" in out, out
        assert "goose" in b.tool_list_price_watches()
    with wsmod.use(proj_b):
        assert b.tool_list_price_watches() == "[No active price watches]"
        assert b.tool_unwatch_price(1).startswith("[No watch")
    with wsmod.use(proj_a):
        assert "goose" in b.tool_list_price_watches()


def test_default_project_keeps_legacy_watches(isolated):
    b = isolated["builtins"]
    wsmod = isolated["workspaces"]
    proj_a = wsmod.create("Project A")
    with wsmod.use("default"):
        b.tool_watch_price("https://example.com/legacy", "10", "legacy-item")
    assert isolated["legacy"].exists()
    with wsmod.use(proj_a):
        assert b.tool_list_price_watches() == "[No active price watches]"
    with wsmod.use("default"):
        assert "legacy-item" in b.tool_list_price_watches()


def test_pricecheck_reads_project_db(isolated):
    b = isolated["builtins"]
    pc = isolated["pricecheck"]
    wsmod = isolated["workspaces"]
    proj_a = wsmod.create("Project A")
    with wsmod.use(proj_a):
        b.tool_watch_price("https://example.com/goose", "40", "goose")
    with wsmod.use("default"):
        # The checker, run in the default project, must NOT see
        # project A's watch (it would report a drop / alert for a
        # watch that is not this project's).
        assert pc.check_price_watches() == "[No price watches to check]"
