"""Trigger cooldown recovery (B5#4): a match that arrives inside
the 300s cooldown — or whose firing raises — must NOT be consumed.
It stays unseen / still-new and fires on the next tick once the
cooldown has expired (or the firing succeeds). Same shape as the
existing test_triggers.py cooldown test, extended here to the
folder source and the _fire-raises case."""

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


def _mail(sender, subject, date="d1"):
    return {"sender": sender, "subject": subject, "date": date}


def test_folder_file_inside_cooldown_fires_after_expiry(
        tmp_path, monkeypatch):
    """A file landing during the cooldown must not be swallowed
    by the folder snapshot: it is still new once the cooldown
    expires, so it fires then."""
    folder = {"a.txt": 1.0}
    clock = _Clock()
    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        folder_lister=lambda path: dict(folder),
        fire_fn=lambda t, d: "done")
    triggers.create_trigger("Drops", "folder", "sort it",
                            rule={"path": "/watched"}, mode="auto")
    assert engine.tick() == []           # first sight = baseline
    folder["b.pdf"] = 2.0
    out = engine.tick()
    assert len(out) == 1 and "b.pdf" in out[0]["detail"]
    folder["c.pdf"] = 3.0
    assert engine.tick() == []           # inside the 300s cooldown
    clock.t += 301
    out = engine.tick()
    assert len(out) == 1 and "c.pdf" in out[0]["detail"]
    print("ok folder_cooldown_recovery")


def test_email_match_retried_after_fire_raises(tmp_path, monkeypatch):
    """If _fire raises, the cursor must not have advanced: the
    same matching mail fires on the next tick once firing works."""
    mail = [_mail("bank@x.com", "bill one", date="d1")]
    clock = _Clock()
    calls = {"n": 0}

    def flaky_fire(t, d):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("queue down (fake)")
        return "done"

    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        mail_fetcher=lambda: list(mail),
        fire_fn=flaky_fire)
    triggers.create_trigger("Bills", "email", "flag it",
                            rule={"subject_contains": "bill"}, mode="auto")
    assert engine.tick() == []           # firing raised: nothing fired
    out = engine.tick()
    assert len(out) == 1 and "bill one" in out[0]["detail"]
    print("ok email_fire_retry")


def test_folder_file_retried_after_fire_raises(tmp_path, monkeypatch):
    """Same shape for the folder source: a raising _fire must
    not advance the snapshot past the new file."""
    folder = {"a.txt": 1.0}
    clock = _Clock()
    calls = {"n": 0}

    def flaky_fire(t, d):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("queue down (fake)")
        return "done"

    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        folder_lister=lambda path: dict(folder),
        fire_fn=flaky_fire)
    triggers.create_trigger("Drops", "folder", "sort it",
                            rule={"path": "/watched"}, mode="auto")
    assert engine.tick() == []           # first sight = baseline
    folder["b.pdf"] = 2.0
    assert engine.tick() == []           # firing raised: nothing fired
    out = engine.tick()
    assert len(out) == 1 and "b.pdf" in out[0]["detail"]
    print("ok folder_fire_retry")
