"""Event triggers (Alek, approved 2026-10-28): when X happens, run
task Y — journaled, gated, cooled down. Engine seams faked; the
store, matchers, cooldown and mode routing are real."""

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


def _mail(sender, subject, date="d1"):
    return {"sender": sender, "subject": subject, "date": date}


def test_create_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(triggers, "STORE_DIR", str(tmp_path))
    bad = triggers.create_trigger("x", "smoke-signal", "do it")
    assert not bad["ok"]
    bad = triggers.create_trigger("x", "email", "do it", rule={})
    assert not bad["ok"] and "matcher" in bad["error"]
    bad = triggers.create_trigger("x", "folder", "", rule={})
    assert not bad["ok"]
    bad = triggers.create_trigger("x", "folder", "do it", mode="yolo")
    assert not bad["ok"]
    ok = triggers.create_trigger("Bills", "email", "flag it",
                                 rule={"subject_contains": "bill"},
                                 mode="ask")
    assert ok["ok"], ok
    dup = triggers.create_trigger("bills", "folder", "do it")
    assert not dup["ok"] and "exists" in dup["error"]
    print("ok create_validation")


def test_email_fires_once_per_new_match(tmp_path, monkeypatch):
    mail = [_mail("bank@x.com", "Your bill is ready"),
            _mail("pal@x.com", "lunch?")]
    fired = []
    clock = _Clock()
    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        mail_fetcher=lambda: list(mail),
        fire_fn=lambda t, d: fired.append((t["name"], d)) or "done")
    triggers.create_trigger("Bills", "email", "flag it",
                            rule={"subject_contains": "bill"}, mode="auto")
    out = engine.tick()
    assert len(out) == 1 and "Your bill is ready" in out[0]["detail"]
    assert engine.tick() == []          # same mail: already seen
    clock.t += 301                       # past the cooldown
    mail.append(_mail("bank@x.com", "Your bill is ready", date="d2"))
    out = engine.tick()
    assert len(out) == 1                 # a NEW matching mail fires again
    print("ok email_fires_once")


def test_email_cooldown_blocks_burst(tmp_path, monkeypatch):
    mail = [_mail("bank@x.com", "bill one", date="d1")]
    clock = _Clock()
    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        mail_fetcher=lambda: list(mail),
        fire_fn=lambda t, d: "done")
    triggers.create_trigger("Bills", "email", "flag it",
                            rule={"subject_contains": "bill"}, mode="auto")
    assert len(engine.tick()) == 1
    mail.append(_mail("bank@x.com", "bill two", date="d2"))
    assert engine.tick() == []           # inside the 300s cooldown
    print("ok email_cooldown")


def test_folder_baseline_then_new_file(tmp_path, monkeypatch):
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
    clock.t += 301
    assert engine.tick() == []           # no change, no fire
    print("ok folder_trigger")


def test_presence_return_needs_real_absence(tmp_path, monkeypatch):
    state = {"present": False}
    clock = _Clock()
    engine = _engine(
        tmp_path, monkeypatch, now_fn=clock,
        presence_fn=lambda: state["present"],
        fire_fn=lambda t, d: "done")
    triggers.create_trigger("Back", "presence_return", "catch me up",
                            rule={"min_away_s": 1800}, mode="auto")
    engine.tick()                        # away starts
    clock.t += 600
    state["present"] = True
    assert engine.tick() == []           # 10 min away < 30 min minimum
    state["present"] = False
    engine.tick()                        # away again
    clock.t += 3600
    state["present"] = True
    out = engine.tick()
    assert len(out) == 1 and "return" in out[0]["detail"]
    print("ok presence_trigger")


def test_mode_routing_ask_vs_auto(tmp_path, monkeypatch):
    approvals = []
    submitted = []
    fake_approval = types.ModuleType("aria.approval")
    fake_approval.request_approval = lambda tool, args, desc=None: \
        approvals.append((tool, args, desc)) or "[AWAITING]"
    sys.modules["aria.approval"] = fake_approval
    fake_tq = types.ModuleType("aria.agent.taskqueue")
    fake_tq.TaskPriority = types.SimpleNamespace(
        LOW=1, NORMAL=2, HIGH=3)
    fake_tq.get_queue = lambda: types.SimpleNamespace(
        submit=lambda goal, priority=None, speak=None:
            submitted.append(goal) or "tid-1")
    sys.modules["aria.agent.taskqueue"] = fake_tq

    trig_ask = {"name": "AskMe", "task": "do the thing", "mode": "ask"}
    out = triggers._default_fire(trig_ask, "email: hi")
    assert "approval" in out
    assert approvals and approvals[0][0] == "delegate_task"
    assert "AskMe" in approvals[0][1]["goal"]
    assert submitted == []

    trig_auto = {"name": "AutoDo", "task": "do the thing", "mode": "auto"}
    out = triggers._default_fire(trig_auto, "folder: f")
    assert "tid-1" in out and submitted and "AutoDo" in submitted[0]
    print("ok mode_routing")


def test_tool_actions_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(triggers, "STORE_DIR", str(tmp_path))
    created = triggers.tool_event_trigger({
        "action": "create", "name": "Drops", "source": "folder",
        "task": "sort downloads",
        "rule": json.dumps({"path": "/watched"}), "mode": "auto"})
    assert "created" in created, created
    listing = triggers.tool_event_trigger({"action": "list"})
    assert "Drops" in listing and "folder" in listing
    assert "disabled" in triggers.tool_event_trigger(
        {"action": "disable", "id": "1"})
    assert "OFF" in triggers.tool_event_trigger({"action": "list"})
    assert "deleted" in triggers.tool_event_trigger(
        {"action": "delete", "id": "1"})
    assert "No event triggers" in triggers.tool_event_trigger(
        {"action": "list"})
    assert "unknown action" in triggers.tool_event_trigger(
        {"action": "bogus"})
    print("ok tool_round_trip")
