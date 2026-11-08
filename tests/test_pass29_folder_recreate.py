"""Pass 29 #37: a folder trigger must not miss a deletion
followed by recreation of the same filename. A deletion-only
scan records the removal in the snapshot, so the recreated
file is new again — while additions that arrived inside the
cooldown stay unconsumed (B5#4)."""

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
_inbox = types.ModuleType("aria.inbox")
_inbox.INBOX_DIR = "/nonexistent-test-inbox"
sys.modules["aria.inbox"] = _inbox

triggers = importlib.import_module("aria.triggers")


class _Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _engine(tmp_path, monkeypatch, **seams):
    monkeypatch.setattr(triggers, "STORE_DIR", str(tmp_path))
    seams.setdefault("now_fn", _Clock())
    return triggers.TriggerEngine(**seams)


def test_folder_delete_then_recreate_same_name_fires(
        tmp_path, monkeypatch):
    """Delete a.txt, then recreate a.txt: the recreation fires —
    the deletion-only scan must have dropped a.txt from the
    snapshot."""
    folder = {"a.txt": 1.0}
    clock = _Clock()
    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        folder_lister=lambda path: dict(folder),
        fire_fn=lambda t, d: "done")
    triggers.create_trigger("Drops", "folder", "sort it",
                            rule={"path": "/watched"}, mode="auto")
    assert engine.tick() == []           # first sight = baseline
    del folder["a.txt"]
    assert engine.tick() == []           # deletion-only scan: no fire
    folder["a.txt"] = 2.0                # recreated under the same name
    out = engine.tick()
    assert len(out) == 1 and "a.txt" in out[0]["detail"]
    clock.t += 301
    assert engine.tick() == []           # no change, no refire
    print("ok folder_delete_recreate_fires")


def test_folder_deletion_scan_keeps_other_files_baseline(
        tmp_path, monkeypatch):
    """A deletion-only scan updates only the snapshot: deleting
    b.txt leaves a.txt baselined, and a later c.txt still fires
    normally (and b.txt recreated fires too)."""
    folder = {"a.txt": 1.0, "b.txt": 1.0}
    clock = _Clock()
    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        folder_lister=lambda path: dict(folder),
        fire_fn=lambda t, d: "done")
    triggers.create_trigger("Drops", "folder", "sort it",
                            rule={"path": "/watched"}, mode="auto")
    assert engine.tick() == []           # baseline
    del folder["b.txt"]
    assert engine.tick() == []           # deletion-only scan
    folder["c.txt"] = 2.0
    out = engine.tick()
    assert len(out) == 1 and "c.txt" in out[0]["detail"]
    clock.t += 301
    folder["b.txt"] = 3.0                # b.txt recreated: fires too
    out = engine.tick()
    assert len(out) == 1 and "b.txt" in out[0]["detail"]
    print("ok folder_deletion_scan_keeps_baseline")


def test_folder_addition_inside_cooldown_still_not_lost(
        tmp_path, monkeypatch):
    """B5#4 regression guard: an addition that arrives inside the
    cooldown still fires after expiry — the deletion-recording
    step must not swallow unconsumed additions."""
    folder = {"a.txt": 1.0}
    clock = _Clock()
    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        folder_lister=lambda path: dict(folder),
        fire_fn=lambda t, d: "done")
    triggers.create_trigger("Drops", "folder", "sort it",
                            rule={"path": "/watched"}, mode="auto")
    assert engine.tick() == []           # baseline
    folder["b.pdf"] = 2.0
    out = engine.tick()
    assert len(out) == 1 and "b.pdf" in out[0]["detail"]
    folder["c.pdf"] = 3.0
    assert engine.tick() == []           # inside the 300s cooldown
    clock.t += 301
    out = engine.tick()
    assert len(out) == 1 and "c.pdf" in out[0]["detail"]
    print("ok folder_cooldown_addition_kept")
