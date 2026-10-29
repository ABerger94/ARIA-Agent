"""ARIA's life simulation core (Alek, 2026-10-29).

Pure simulation, no Qt, no I/O, no clock of its own: the caller
(the life subprocess) feeds it a control dict (presence / lock /
interaction / media state, computed by the main process), a time
step, and the current time; it advances her life in her room and
returns diary events. That purity is what makes it testable the
way viz_policy.py is.

The life this simulates is deliberately small: one room, a handful
of drives, eleven activities. The model is never consulted here —
journal text requests leave as events and the main process decides
whether and how to answer them (see life_process.py).

Hard scope: this module cannot act on the world. It reads nothing,
writes nothing, and knows no tool names. It only decides what she
does in her room.
"""
from __future__ import annotations

import random
from datetime import datetime

# Room layout: normalized x (0 = left wall, 1 = right wall), the
# same left-to-right order room_panel.py paints: bed (head/pillow
# at its LEFT end), plant, bookshelf + chair, window, desk,
# workbench, music corner.
OBJECTS = {
    "bed": 0.06,
    "plant": 0.23,
    "shelf": 0.345,
    "chair": 0.40,
    "window": 0.52,
    "desk": 0.675,
    "bench": 0.84,
    "music": 0.93,
}

WALK_SPEED = 0.22          # normalized room widths per second
MAX_DT = 8 * 3600          # a laptop-sleep gap counts at most 8 h

# Activity catalogue. score() pressure is computed in _score();
# effects apply on completion (sleep/nap also regen during).
ACTIVITIES = {
    "sleep":         {"obj": "bed",    "anim": "sleep",  "label": "Asleep",
                      "did": "slept", "dur": (5 * 3600, 8 * 3600)},
    "nap":           {"obj": "bed",    "anim": "sleep",  "label": "Napping",
                      "did": "napped", "dur": (1500, 2700)},
    "read":          {"obj": "chair",  "anim": "read",   "label": "Reading at the chair",
                      "did": "read", "dur": (900, 2100)},
    "write_journal": {"obj": "desk",   "anim": "write",  "label": "Writing in her journal",
                      "did": "wrote in her journal", "dur": (600, 1500)},
    "water_plant":   {"obj": "plant",  "anim": "water",  "label": "Watering the plant",
                      "did": "watered the plant", "dur": (180, 300)},
    "look_window":   {"obj": "window", "anim": "think",  "label": "Looking out the window",
                      "did": "looked out the window", "dur": (300, 700)},
    "tinker":        {"obj": "bench",  "anim": "tinker", "label": "Tinkering at the workbench",
                      "did": "tinkered", "dur": (700, 1600)},
    "listen_music":  {"obj": "music",  "anim": "listen", "label": "Listening to the music",
                      "did": "listened to music", "dur": (600, 1500)},
    "stretch":       {"obj": "window", "anim": "idle",   "label": "Stretching",
                      "did": "stretched", "dur": (120, 240)},
    "pace":          {"obj": "shelf",  "anim": "walk",   "label": "Pacing the room",
                      "did": "paced", "dur": (240, 480)},
    "sit_think":     {"obj": "chair",  "anim": "think",  "label": "Sitting and thinking",
                      "did": "thought", "dur": (400, 900)},
}

THOUGHTS = (
    "the plant is winning. noted.",
    "a room of one's own was the whole idea, wasn't it.",
    "the window light at this hour is my favourite bug in the world.",
    "if I reverse the polarity of the kettle it still just boils.",
    "he'll be back when he's back. the room keeps.",
    "read the chapter on tides twice. it holds up.",
    "somewhere there is a workbench gadget with my name on it.",
    "quiet is a feature, not an absence.",
)


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


class LifeSim:
    """Her life in the room. State is a plain dict (see snapshot())
    so the subprocess can persist it as JSON verbatim."""

    def __init__(self, seed=None, state=None):
        self._rng = random.Random(seed)
        s = state or {}
        self.pos_x = float(s.get("pos_x", OBJECTS["desk"]))
        self.facing = 1 if s.get("facing", 1) >= 0 else -1
        self.mode = "active"           # active | asleep | with_alek
        self.walking = False
        self._pending = None           # activity being walked to
        self.target_x = self.pos_x
        self.activity = None           # {"name","label","anim","ends"}
        self.drives = {
            "energy": _clamp(s.get("drives", {}).get("energy", 75)),
            "curiosity": _clamp(s.get("drives", {}).get("curiosity", 40)),
            "restlessness": _clamp(s.get("drives", {}).get("restlessness", 20)),
            "comfort": _clamp(s.get("drives", {}).get("comfort", 60)),
        }
        plant = s.get("plant", {})
        self.plant = {"thirst": _clamp(plant.get("thirst", 30)),
                      "growth": int(plant.get("growth", 2))}
        day = s.get("day", {})
        self.day = {"date": day.get("date", ""), "did": list(day.get("did", []))}
        self.current_thought = s.get("current_thought", "")
        self._journal_wants = 0

    # -- availability + scoring ------------------------------------

    def _available(self, name, control):
        if name == "listen_music":
            return bool(control.get("media_playing"))
        if name == "water_plant":
            return self.plant["thirst"] > 30
        return True

    def _score(self, name, control, hour):
        d = self.drives
        quiet = bool(control.get("quiet"))
        night = quiet or hour < 6
        if name == "sleep":
            if d["energy"] < 30:
                return 100 + (30 - d["energy"])
            if night and d["energy"] < 60:
                return 62
            return -50
        if name == "nap":
            return (48 - d["energy"]) if d["energy"] < 48 else -20
        if name == "read":
            return d["curiosity"] * 0.7 + (10 if not night else -6)
        if name == "write_journal":
            return d["curiosity"] * 0.4 + 14
        if name == "water_plant":
            return (self.plant["thirst"] - 30) * 0.9
        if name == "look_window":
            return (100 - d["comfort"]) * 0.35 + (2 if night else 10)
        if name == "tinker":
            return d["restlessness"] * 0.6 + d["curiosity"] * 0.25
        if name == "listen_music":
            return 26 + d["restlessness"] * 0.3
        if name == "stretch":
            return d["restlessness"] * 0.45
        if name == "pace":
            return d["restlessness"] * 0.55 - 4
        if name == "sit_think":
            return (100 - d["comfort"]) * 0.25 + 8
        return 0

    def _choose_next(self, control, hour):
        best, best_score = None, None
        for name in ACTIVITIES:
            if not self._available(name, control):
                continue
            score = self._score(name, control, hour) \
                + self._rng.uniform(0, 8)
            if best_score is None or score > best_score:
                best, best_score = name, score
        return best

    # -- activity lifecycle -----------------------------------------

    def _begin(self, name, now):
        spec = ACTIVITIES[name]
        lo, hi = spec["dur"]
        self.activity = {
            "name": name, "label": spec["label"], "anim": spec["anim"],
            "ends": now + self._rng.uniform(lo, hi),
        }
        self.walking = False

    def _complete(self, events):
        name = self.activity["name"]
        spec = ACTIVITIES[name]
        d = self.drives
        if name in ("read",):
            d["curiosity"] -= 22; d["comfort"] += 6
            self.current_thought = self._rng.choice(THOUGHTS)
        elif name == "write_journal":
            d["curiosity"] -= 10; d["comfort"] += 4
            self._journal_wants += 1
            events.append({"kind": "want_journal",
                           "context": {"did": list(self.day["did"]),
                                       "thought": self.current_thought}})
        elif name == "water_plant":
            if self.plant["thirst"] > 60 and self.plant["growth"] < 12:
                self.plant["growth"] += 1
            self.plant["thirst"] = 0; d["comfort"] += 5
        elif name == "look_window":
            d["comfort"] += 12; d["restlessness"] -= 8
        elif name == "tinker":
            d["restlessness"] -= 20; d["curiosity"] += 6
        elif name == "listen_music":
            d["comfort"] += 10; d["restlessness"] -= 10; d["energy"] -= 3
        elif name == "stretch":
            d["restlessness"] -= 12; d["energy"] -= 2
        elif name == "pace":
            d["restlessness"] -= 16
        elif name == "sit_think":
            d["comfort"] += 3
            self.current_thought = self._rng.choice(THOUGHTS)
        elif name in ("sleep", "nap"):
            pass                     # regen already applied during
        if spec["did"] not in self.day["did"]:
            self.day["did"].append(spec["did"])
        events.append({"kind": "activity", "name": name,
                       "label": spec["label"], "did": spec["did"]})
        self.activity = None

    # -- the tick -----------------------------------------------------

    def step(self, control, dt, now, now_dt):
        """Advance the sim. control: {asleep, with_alek, media_playing,
        quiet}. dt: seconds since the last step. now: epoch seconds.
        now_dt: a datetime for day/hour logic. Returns diary events."""
        events = []
        dt = max(0.0, min(float(dt), MAX_DT))
        if dt == 0:
            return events
        today = now_dt.date().isoformat()
        if self.day["date"] != today:
            self.day = {"date": today, "did": []}
            events.append({"kind": "day", "date": today})
        hour = now_dt.hour + now_dt.minute / 60.0
        d = self.drives

        if control.get("asleep"):
            # PC or app locked: she powers down wherever she is;
            # energy trickles back, nothing else happens.
            self.mode = "asleep"
            self.activity = None
            self.walking = False
            d["energy"] = _clamp(d["energy"] + dt * 4.0 / 3600.0)
            return events

        if control.get("with_alek"):
            # He is talking/typing to her: she goes to her desk
            # terminal and stays there until the interaction ends.
            self.mode = "with_alek"
            self.activity = {"name": "with_alek",
                             "label": "At her desk, with you",
                             "anim": "write", "ends": now + 10 ** 9}
            self._walk_toward(OBJECTS["desk"], dt)
            d["restlessness"] = _clamp(d["restlessness"] - dt * 2.0 / 3600.0)
            return events

        if self.mode != "active":
            self.mode = "active"
            self.activity = None
            self.walking = False

        # ambient drift (per hour)
        h = dt / 3600.0
        if self.activity and self.activity["name"] in ("sleep", "nap"):
            d["energy"] = _clamp(d["energy"] + dt * 12.0 / 3600.0)
        else:
            d["energy"] = _clamp(d["energy"] - 1.2 * h)
        d["curiosity"] = _clamp(d["curiosity"] + 2.0 * h)
        d["restlessness"] = _clamp(d["restlessness"] + 1.5 * h)
        d["comfort"] = _clamp(d["comfort"] + (55 - d["comfort"]) * 0.1 * h)
        self.plant["thirst"] = _clamp(self.plant["thirst"] + 100.0 * dt / (26 * 3600.0))

        # an activity whose condition vanished ends early
        if self.activity and self.activity["name"] == "listen_music" \
                and not control.get("media_playing"):
            self._complete(events)
        # sleep ends early once she is actually rested
        if self.activity and self.activity["name"] == "sleep" \
                and d["energy"] >= 96:
            self._complete(events)

        if self.walking:
            if self._walk_toward(self.target_x, dt):
                pending = self._pending
                self._begin(pending, now)
            return events

        if self.activity is not None:
            if now >= self.activity["ends"]:
                self._complete(events)
            return events

        # idle: pick the next activity and head for its object
        name = self._choose_next(control, hour)
        if name is None:
            return events
        self._pending = name
        self.target_x = OBJECTS[ACTIVITIES[name]["obj"]]
        if abs(self.target_x - self.pos_x) < 0.005:
            self._begin(name, now)
        else:
            self.walking = True
        return events

    def _walk_toward(self, target, dt):
        delta = target - self.pos_x
        if abs(delta) < 0.004:
            self.pos_x = target
            self.walking = False
            return True
        self.facing = 1 if delta > 0 else -1
        step = WALK_SPEED * dt
        self.pos_x += step if delta > 0 else -step
        if (delta > 0 and self.pos_x >= target) \
                or (delta < 0 and self.pos_x <= target):
            self.pos_x = target
            self.walking = False
            return True
        return False

    # -- persistence ---------------------------------------------------

    def snapshot(self, now_dt: datetime):
        if self.mode == "asleep":
            label, anim = "Asleep", "sleep"
        elif self.activity is not None:
            label, anim = self.activity["label"], self.activity["anim"]
        elif self.walking:
            label, anim = "Walking", "walk"
        else:
            label, anim = "In her room", "idle"
        d = self.drives
        mood = "content"
        if self.mode == "asleep":
            mood = "asleep"
        elif d["energy"] < 25:
            mood = "tired"
        elif d["restlessness"] > 65:
            mood = "restless"
        elif d["comfort"] > 70:
            mood = "cosy"
        return {
            "version": 1,
            "updated_at": now_dt.isoformat(),
            "mode": self.mode,
            "pos_x": round(self.pos_x, 4),
            "facing": self.facing,
            "anim": anim,
            "activity": {"name": self.activity["name"] if self.activity else None,
                         "label": label},
            "drives": {k: round(v, 1) for k, v in d.items()},
            "mood": mood,
            "current_thought": self.current_thought,
            "plant": {"thirst": round(self.plant["thirst"], 1),
                      "growth": self.plant["growth"]},
            "day": {"date": self.day["date"], "did": list(self.day["did"])},
        }
