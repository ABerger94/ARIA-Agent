"""Pass 29 #38: deleted trigger ids are never reused, and
deleting a trigger clears its definition-owned state (folder
snapshots, cooldowns, per-trigger seen mail) with the
definition — so a later trigger never inherits a stale
snapshot or cooldown."""

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


def _read_state(tmp_path):
    with open(os.path.join(str(tmp_path), "triggers_state.json"),
              encoding="utf-8") as f:
        return json.load(f)


def _mail(sender, subject, date="d1"):
    return {"sender": sender, "subject": subject, "date": date}


def test_ids_never_reused_after_delete(tmp_path, monkeypatch):
    """Delete the highest-id trigger and create another: the new
    id is fresh, never the deleted one — even after deleting
    everything."""
    monkeypatch.setattr(triggers, "STORE_DIR", str(tmp_path))
    a = triggers.create_trigger("A", "folder", "do it",
                                rule={"path": "/watched"}, mode="auto")
    b = triggers.create_trigger("B", "folder", "do it",
                                rule={"path": "/watched"}, mode="auto")
    assert a["trigger"]["id"] == 1 and b["trigger"]["id"] == 2
    assert triggers.delete_trigger(2) is True
    c = triggers.create_trigger("C", "folder", "do it",
                                rule={"path": "/watched"}, mode="auto")
    assert c["trigger"]["id"] == 3, c
    assert triggers.delete_trigger(1) is True
    assert triggers.delete_trigger(3) is True
    d = triggers.create_trigger("D", "folder", "do it",
                                rule={"path": "/watched"}, mode="auto")
    assert d["trigger"]["id"] == 4, d
    print("ok ids_never_reused")


def test_delete_clears_definition_owned_state(tmp_path, monkeypatch):
    """Deleting a trigger drops its folder snapshot, cooldown,
    and per-trigger seen mail from triggers_state.json."""
    folder = {"a.txt": 1.0}
    mail = [_mail("bank@x.com", "Your bill is ready")]
    clock = _Clock()
    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        folder_lister=lambda path: dict(folder),
        mail_fetcher=lambda: list(mail),
        fire_fn=lambda t, d: "done")
    triggers.create_trigger("Drops", "folder", "sort it",
                            rule={"path": "/watched"}, mode="auto")
    triggers.create_trigger("Bills", "email", "flag it",
                            rule={"subject_contains": "bill"}, mode="auto")
    out = engine.tick()
    assert len(out) == 1 and out[0]["trigger"] == "Bills"
    # folder baselined on that same tick; mail fired
    folder["b.pdf"] = 2.0
    clock.t += 301
    out = engine.tick()
    assert len(out) == 1 and "b.pdf" in out[0]["detail"]
    state = _read_state(tmp_path)
    assert state["folder_snaps"].get("1") == {"a.txt": 1.0,
                                              "b.pdf": 2.0}
    assert "1" in state["last_fired"]
    assert "2" in state.get("seen_mail_by_trigger", {})
    assert "2" in state["last_fired"]
    assert triggers.delete_trigger(1) is True
    assert triggers.delete_trigger(2) is True
    state = _read_state(tmp_path)
    assert "1" not in state.get("folder_snaps", {})
    assert "1" not in state.get("last_fired", {})
    assert "2" not in state.get("seen_mail_by_trigger", {})
    assert "2" not in state.get("last_fired", {})
    print("ok delete_clears_state")


def test_new_trigger_after_delete_gets_no_stale_snapshot(
        tmp_path, monkeypatch):
    """End to end: a folder trigger fires, is deleted, and its
    replacement (new id, same path) baselines instead of firing
    on the predecessor's snapshot — then fires normally on a
    genuinely new file."""
    folder = {"a.txt": 1.0}
    clock = _Clock()
    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        folder_lister=lambda path: dict(folder),
        fire_fn=lambda t, d: "done")
    first = triggers.create_trigger("Drops", "folder", "sort it",
                                    rule={"path": "/watched"}, mode="auto")
    assert first["trigger"]["id"] == 1
    assert engine.tick() == []           # baseline
    folder["b.pdf"] = 2.0
    out = engine.tick()
    assert len(out) == 1 and "b.pdf" in out[0]["detail"]
    assert triggers.delete_trigger(1) is True
    second = triggers.create_trigger("Drops2", "folder", "sort it",
                                     rule={"path": "/watched"}, mode="auto")
    assert second["trigger"]["id"] == 2, second
    # Same folder contents, new trigger: first sight is a
    # baseline, not a firing on the stale snapshot.
    assert engine.tick() == []
    folder["c.pdf"] = 3.0
    out = engine.tick()
    assert len(out) == 1 and "c.pdf" in out[0]["detail"]
    print("ok no_stale_snapshot_after_delete")


def test_legacy_high_id_without_counter_file(tmp_path, monkeypatch):
    """Backwards compatibility: a pre-existing triggers.json
    with a high id but no counter file yet must not collide —
    the next id is max(existing) + 1."""
    monkeypatch.setattr(triggers, "STORE_DIR", str(tmp_path))
    legacy = [{"id": 7, "name": "Legacy", "source": "folder",
               "rule": {"path": "/watched"}, "task": "sort it",
               "mode": "ask", "enabled": True, "policy": {}}]
    with open(triggers._triggers_path(), "w",
              encoding="utf-8") as f:
        json.dump(legacy, f)
    created = triggers.create_trigger("New", "folder", "do it",
                                      rule={"path": "/watched"},
                                      mode="auto")
    assert created["ok"], created
    assert created["trigger"]["id"] == 8, created
    print("ok legacy_high_id_no_collision")
