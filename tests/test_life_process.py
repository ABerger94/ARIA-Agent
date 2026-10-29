"""Life subprocess + supervisor (Alek, 2026-10-29): file IPC,
staleness pause, journal budget, state restore, crash backoff.

Everything runs against a tmp ARIA dir; no subprocess is actually
spawned except the in-process run() smoke test.
"""
import json
import sys
import time
import types
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import life_process as lp
from aria.life_supervisor import LifeSupervisor


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _control(ts_offset=0, **kw):
    base = {"ts": time.time() + ts_offset, "running": True,
            "asleep": False, "with_alek": False,
            "media_playing": False, "quiet": False}
    base.update(kw)
    return base


def test_decide_control_fresh_maps_fields():
    control, paused = lp.decide_control(
        _control(asleep=True, media_playing=True), time.time())
    assert paused is False
    assert control == {"asleep": True, "with_alek": False,
                       "media_playing": True, "quiet": False}


def test_decide_control_stale_or_missing_pauses():
    now = time.time()
    assert lp.decide_control(None, now)[1] is True
    assert lp.decide_control(_control(ts_offset=-3600), now)[1] is True
    assert lp.decide_control("junk", now)[1] is True


def test_journal_budget_caps():
    now = 1_800_000_000.0
    assert lp.can_request_journal([], now) is True
    assert lp.can_request_journal([now - 10] * 4, now) is False
    # an hour-old burst no longer counts against the hourly cap,
    # but 20 today still blocks
    assert lp.can_request_journal([now - 3700] * 4, now) is True
    day_start = now - (now % 86400)
    assert lp.can_request_journal(
        [day_start + 60] * 20, now) is False


def test_state_restore_and_corrupt_state(tmp_path):
    p = lp.paths(str(tmp_path))
    _write(Path(p["state"]), {"version": 1, "pos_x": 0.5,
                              "drives": {"energy": 33},
                              "plant": {"thirst": 10, "growth": 4},
                              "day": {"date": "2026-10-29", "did": ["read"]}})
    sim = lp.load_sim(str(tmp_path))
    assert sim.pos_x == 0.5 and sim.drives["energy"] == 33
    assert sim.plant["growth"] == 4 and sim.day["did"] == ["read"]
    Path(p["state"]).write_text("{not json", encoding="utf-8")
    sim2 = lp.load_sim(str(tmp_path))
    assert 0.0 <= sim2.pos_x <= 1.0        # fresh day, no crash


def test_diary_append_and_rotation(tmp_path):
    base = str(tmp_path)
    lp.append_diary(base, {"kind": "day", "date": "2026-10-29"},
                    max_bytes=60)
    lp.append_diary(base, {"kind": "activity", "name": "read"},
                    max_bytes=60)
    lp.append_diary(base, {"kind": "activity", "name": "tinker"},
                    max_bytes=60)
    p = lp.paths(base)
    lines = Path(p["diary"]).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    assert Path(p["diary"] + ".old").exists()   # rotated at the cap


def test_run_writes_state_and_stops_on_running_false(tmp_path):
    base = str(tmp_path)
    p = lp.paths(base)
    _write(Path(p["control"]), _control())
    assert lp.run(base, tick_s=0.01, max_ticks=4) == 0
    snap = json.loads(Path(p["state"]).read_text(encoding="utf-8"))
    assert snap["version"] == 1 and "drives" in snap
    _write(Path(p["control"]), _control(running=False))
    assert lp.run(base, tick_s=0.01, max_ticks=50) == 0


class _FakeProc:
    def __init__(self, alive=True):
        self._alive = alive
        self.terminated = False

    def poll(self):
        return None if self._alive else 1

    def terminate(self):
        self.terminated = True
        self._alive = False


def test_supervisor_writes_control_and_stops_child(tmp_path):
    spawned = []

    def fake_popen(args, **kw):
        proc = _FakeProc(alive=True)
        spawned.append((args, proc))
        return proc

    sup = LifeSupervisor(lambda: {"asleep": True, "with_alek": False,
                                  "media_playing": False, "quiet": True},
                         base_dir=str(tmp_path), popen=fake_popen)
    sup._spawn()
    sup._write_control(running=True)
    p = lp.paths(str(tmp_path))
    raw = json.loads(Path(p["control"]).read_text(encoding="utf-8"))
    assert raw["running"] is True and raw["asleep"] is True
    assert raw["quiet"] is True and "ts" in raw
    assert spawned[0][0][1:3] == ["-m", "aria.life_process"]
    assert spawned[0][0][3] == str(tmp_path)   # its ARIA dir
    sup.stop()
    raw = json.loads(Path(p["control"]).read_text(encoding="utf-8"))
    assert raw["running"] is False
    assert spawned[0][1].terminated is True


def test_supervisor_restart_backoff_and_give_up(tmp_path):
    procs = []

    def dead_popen(args, **kw):
        proc = _FakeProc(alive=False)
        procs.append(proc)
        return proc

    sup = LifeSupervisor(lambda: {}, base_dir=str(tmp_path),
                         popen=dead_popen)
    sup._spawn()
    now = 1000.0
    assert sup._supervise_once(now) == "respawned"
    assert sup._supervise_once(now) == "waiting"     # backoff gate
    assert sup._supervise_once(now + 100) == "respawned"
    assert sup._supervise_once(now + 200) == "respawned"
    result = sup._supervise_once(now + 290)
    assert result == "gave_up" and sup.gave_up is True
    assert sup._supervise_once(now + 400) == "gave_up"
    assert len(procs) == 4                            # 1 + 3 respawns
