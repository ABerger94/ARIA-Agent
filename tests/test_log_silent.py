"""log_silent must never kill the process (root-caused 2026-10-28).

The old log_silent handled a failed log write by calling itself,
so a persistent write failure (locked / unwritable
~/ARIA/logs/silent-errors.log) recursed to RecursionError. It
fired at every boot on Alek's laptop: memory.init_databases'
routine duplicate-column ALTER error is the first log_silent
caller in start_all, so ARIA died during "Initializing OS
subsystems..." on every launch, on every build. Reproduced in
isolation: log_silent('probe', Exception()) alone crashed the
interpreter with the RecursionError object dump.

These tests pin the contract: whatever the filesystem does,
log_silent returns quietly — and the happy path still logs.
"""

import builtins
import importlib
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
sys.modules["aria.speech"] = types.ModuleType("aria.speech")

config = importlib.import_module("aria.config")


def test_write_failure_returns_quietly(monkeypatch):
    def _boom(*a, **k):
        raise PermissionError("log file locked")

    monkeypatch.setattr(builtins, "open", _boom)
    # The exact 2026-10-28 repro: this call used to kill the
    # interpreter with RecursionError. Three in a row for good
    # measure — and the guard must reset between calls.
    for _ in range(3):
        assert config.log_silent("probe", Exception("x")) is None
    assert config._log_silent_active is False


def test_rotation_failure_returns_quietly(monkeypatch, tmp_path):
    # Boot-killer shape: log is over the cap and rotating it is
    # refused (e.g. the .1 / source is held by another process).
    logdir = tmp_path / "ARIA" / "logs"
    logdir.mkdir(parents=True)
    p = logdir / "silent-errors.log"
    p.write_bytes(b"x" * (config._SILENT_LOG_MAX + 1))
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))

    def _no_replace(*a, **k):
        raise PermissionError("rotation blocked")

    monkeypatch.setattr(os, "replace", _no_replace)
    assert config.log_silent("init_databases", Exception("dup column")) is None
    assert config._log_silent_active is False


def test_nested_call_during_failure_is_dropped(monkeypatch):
    def _reentrant_open(*a, **k):
        # A failure path that itself tries to log (the old
        # except-handler shape): the nested call must be dropped
        # by the guard, not served, not recursed.
        config.log_silent("log_silent", Exception("nested"))
        raise PermissionError("still locked")

    monkeypatch.setattr(builtins, "open", _reentrant_open)
    assert config.log_silent("probe", Exception("x")) is None
    assert config._log_silent_active is False


def test_happy_path_still_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    config.log_silent("unit_test", ValueError("boom"))
    p = tmp_path / "ARIA" / "logs" / "silent-errors.log"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "[unit_test] ValueError: boom" in text
