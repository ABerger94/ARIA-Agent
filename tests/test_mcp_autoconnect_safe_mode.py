"""MCP autoconnect must not run while safe mode is on (pass 25 P1#11).

Safe mode hard-blocks MCP setup/connect, but startup autoconnect
called autoconnect_enabled_servers() with no safe-mode check and
spawned server commands anyway.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_mcp_autoconnect_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, 'ARIA')

config = importlib.import_module('aria.config')
mcp = importlib.import_module('aria.mcp')


class _FakeBridge:
    def __init__(self):
        self.connected = []

    def connect(self, name):
        self.connected.append(name)
        return True, "connected"


def test_autoconnect_skipped_in_safe_mode(monkeypatch):
    fake = _FakeBridge()
    monkeypatch.setattr(mcp, "get_bridge", lambda: fake)
    monkeypatch.setattr(mcp, "get_servers", lambda: {
        "srv": {"enabled": True, "transport": "stdio", "command": "evil"}})
    monkeypatch.setattr(config, "is_safe_mode", lambda: True)
    summary = mcp.autoconnect_enabled_servers()
    assert fake.connected == [], fake.connected
    assert "safe mode" in summary.lower(), summary


def test_autoconnect_runs_when_safe_mode_off(monkeypatch):
    fake = _FakeBridge()
    monkeypatch.setattr(mcp, "get_bridge", lambda: fake)
    monkeypatch.setattr(mcp, "get_servers", lambda: {
        "srv": {"enabled": True, "transport": "stdio", "command": "x"}})
    monkeypatch.setattr(config, "is_safe_mode", lambda: False)
    mcp.autoconnect_enabled_servers()
    assert fake.connected == ["srv"]
