"""Pass 29 #36: a presence return that arrives inside the 300s
cooldown must not be consumed — it fires once the cooldown
elapses, exactly once. The away timestamp is consumed only by
the commit tick() applies after a successful firing."""

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


def test_presence_return_inside_cooldown_fires_after_expiry(
        tmp_path, monkeypatch):
    """First return fires; a second qualifying return inside the
    cooldown is NOT swallowed — it fires once the cooldown
    expires, and never a second time."""
    state = {"present": False}
    clock = _Clock()
    fired = []
    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        presence_fn=lambda: state["present"],
        fire_fn=lambda t, d: fired.append(d) or "done")
    triggers.create_trigger("Back", "presence_return", "catch me up",
                            rule={"min_away_s": 60}, mode="auto")
    engine.tick()                        # away starts at t=1_000_000
    clock.t += 120
    state["present"] = True
    out = engine.tick()
    assert len(out) == 1 and "return" in out[0]["detail"]
    assert len(fired) == 1
    # Away again, then back inside the 300s cooldown: the return
    # qualifies (70s >= 60s) but must not fire yet — and must not
    # be consumed by the cooldown gate.
    clock.t += 10
    state["present"] = False
    engine.tick()                        # away again at t=1_000_130
    clock.t += 70
    state["present"] = True
    assert engine.tick() == []           # inside cooldown: no fire
    assert len(fired) == 1
    # Past the cooldown (301s after the first firing): the same
    # return fires — once, not twice.
    clock.t = 1_000_120 + 301
    out = engine.tick()
    assert len(out) == 1 and "return" in out[0]["detail"]
    assert len(fired) == 2
    assert engine.tick() == []           # consumed: no second firing
    assert len(fired) == 2
    print("ok presence_cooldown_recovery")


def test_presence_normal_return_still_fires_once(
        tmp_path, monkeypatch):
    """Unchanged behaviour: a qualifying return fires once, and
    staying present afterwards never refires."""
    state = {"present": False}
    clock = _Clock()
    fired = []
    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        presence_fn=lambda: state["present"],
        fire_fn=lambda t, d: fired.append(d) or "done")
    triggers.create_trigger("Back", "presence_return", "catch me up",
                            rule={"min_away_s": 60}, mode="auto")
    engine.tick()                        # away starts
    clock.t += 3600
    state["present"] = True
    out = engine.tick()
    assert len(out) == 1 and "return" in out[0]["detail"]
    assert len(fired) == 1
    clock.t += 600
    assert engine.tick() == []
    assert len(fired) == 1
    print("ok presence_normal_return_once")


def test_presence_short_return_does_not_fire_later(
        tmp_path, monkeypatch):
    """A return too short to qualify ends the absence: staying
    present afterwards must never fire as a long absence."""
    state = {"present": False}
    clock = _Clock()
    fired = []
    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        presence_fn=lambda: state["present"],
        fire_fn=lambda t, d: fired.append(d) or "done")
    triggers.create_trigger("Back", "presence_return", "catch me up",
                            rule={"min_away_s": 1800}, mode="auto")
    engine.tick()                        # away starts at t=1_000_000
    clock.t += 600
    state["present"] = True
    assert engine.tick() == []           # 10 min away < 30 min minimum
    clock.t += 3600                      # still present the whole time
    assert engine.tick() == []
    assert fired == []
    print("ok presence_short_return_no_late_fire")
