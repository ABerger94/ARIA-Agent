"""Life sim core (Alek, 2026-10-29): determinism, drive bounds,
activity gating, presence overrides, walking, plant, day rollover.

The sim is pure: fixed seed + fixed control/dt script must give
the same life twice. All tests drive step() with synthetic time.
"""
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

from aria.life_sim import ACTIVITIES, OBJECTS, LifeSim

TZ = timezone(timedelta(hours=-4))
T0 = datetime(2026, 10, 29, 14, 0, tzinfo=TZ)
EPOCH0 = T0.timestamp()

AWAY = {"asleep": False, "with_alek": False,
        "media_playing": False, "quiet": False}


def run(sim, control, seconds, dt=60.0, start=T0):
    """Step the sim forward, collecting events."""
    events = []
    now_dt = start
    remaining = float(seconds)
    while remaining > 0:
        step_dt = min(dt, remaining)
        events += sim.step(control, step_dt,
                           now_dt.timestamp(), now_dt)
        now_dt += timedelta(seconds=step_dt)
        remaining -= step_dt
    return events


def test_deterministic_same_seed_same_life():
    a, b = LifeSim(seed=7), LifeSim(seed=7)
    ea = run(a, AWAY, 6 * 3600)
    eb = run(b, AWAY, 6 * 3600)
    acts_a = [e["name"] for e in ea if e["kind"] == "activity"]
    acts_b = [e["name"] for e in eb if e["kind"] == "activity"]
    assert acts_a == acts_b and len(acts_a) >= 3
    assert a.snapshot(T0)["drives"] == b.snapshot(T0)["drives"]
    assert a.day["did"] == b.day["did"]


def test_drives_stay_in_bounds_over_a_day():
    sim = LifeSim(seed=3)
    run(sim, AWAY, 24 * 3600)
    for v in sim.drives.values():
        assert 0.0 <= v <= 100.0
    assert 0.0 <= sim.plant["thirst"] <= 100.0


def test_listen_music_requires_media():
    sim = LifeSim(seed=5)
    assert sim._available("listen_music", AWAY) is False
    assert sim._available("listen_music",
                          {**AWAY, "media_playing": True}) is True
    events = run(sim, AWAY, 12 * 3600)
    names = [e["name"] for e in events if e["kind"] == "activity"]
    assert "listen_music" not in names


def test_low_energy_at_night_chooses_sleep():
    sim = LifeSim(seed=11)
    sim.drives["energy"] = 12
    for _ in range(20):
        assert sim._choose_next({**AWAY, "quiet": True}, 23.5) == "sleep"


def test_asleep_control_powers_down():
    sim = LifeSim(seed=13)
    sim.drives["energy"] = 40
    events = run(sim, {**AWAY, "asleep": True}, 3600)
    assert sim.mode == "asleep"
    assert sim.activity is None
    assert sim.drives["energy"] > 40           # trickle regen
    assert [e for e in events if e["kind"] == "activity"] == []


def test_with_alek_goes_to_desk_and_stays():
    sim = LifeSim(seed=17)
    run(sim, {**AWAY, "with_alek": True}, 600)
    assert sim.mode == "with_alek"
    assert abs(sim.pos_x - OBJECTS["desk"]) < 0.01
    assert sim.activity["name"] == "with_alek"
    # and leaving the interaction returns her to her own life
    run(sim, AWAY, 120)
    assert sim.mode == "active"


def test_walking_arrives_and_activity_starts():
    sim = LifeSim(seed=19)
    sim.pos_x = 0.02
    events = run(sim, AWAY, 1800)
    started = [e for e in events if e["kind"] == "activity"]
    assert started, "she should have completed a first activity"
    assert sim.pos_x >= 0.0 and sim.pos_x <= 1.0


def test_watering_resets_thirst_and_records_day():
    sim = LifeSim(seed=23)
    sim.plant["thirst"] = 85
    # make watering the obvious choice: everything else unattractive
    sim.drives.update({"energy": 90, "curiosity": 0,
                       "restlessness": 0, "comfort": 55})
    picked = {sim._choose_next(AWAY, 14.0) for _ in range(10)}
    assert picked == {"water_plant"}
    events = run(sim, AWAY, 1200)
    assert sim.plant["thirst"] < 85
    assert "watered the plant" in sim.day["did"]


def test_day_rollover_resets_did():
    sim = LifeSim(seed=29)
    sim.day = {"date": "2026-10-28", "did": ["read"]}
    events = run(sim, AWAY, 120)
    assert sim.day["date"] == "2026-10-29"
    assert {"kind": "day", "date": "2026-10-29"} in events


def test_snapshot_shape_matches_spec():
    sim = LifeSim(seed=31)
    run(sim, AWAY, 600)
    snap = sim.snapshot(T0)
    assert snap["version"] == 1
    for key in ("mode", "pos_x", "facing", "anim", "activity",
                "drives", "mood", "current_thought", "plant", "day"):
        assert key in snap
    assert set(snap["drives"]) == {"energy", "curiosity",
                                   "restlessness", "comfort"}
    assert snap["activity"]["label"]


def test_state_round_trip_restores_position_and_drives():
    sim = LifeSim(seed=37)
    run(sim, AWAY, 3600)
    snap = sim.snapshot(T0)
    clone = LifeSim(state=snap)
    assert clone.pos_x == snap["pos_x"]
    assert clone.drives["energy"] == snap["drives"]["energy"]
    assert clone.plant["growth"] == snap["plant"]["growth"]
    assert clone.day["did"] == snap["day"]["did"]


def test_every_activity_object_exists():
    for spec in ACTIVITIES.values():
        assert spec["obj"] in OBJECTS
