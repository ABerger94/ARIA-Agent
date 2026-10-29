"""ARIA's life subprocess (Alek, 2026-10-29).

Spawned and supervised by the main process (life_supervisor.py).
Owns exactly three files in ~/ARIA and nothing else:

- life_state.json   — her current life snapshot (LifeSim.snapshot),
                      written atomically, read by the dashboard's
                      room panel.
- life_control.json — written by MAIN: presence / lock / interaction
                      / media state plus a heartbeat timestamp. This
                      process only reads it.
- life_diary.jsonl  — append-only diary: activity lines, day
                      markers, and journal entries the main process
                      mediated for her.

Plus the journal handshake pair (life_journal_request.json /
life_journal_response.json): the sim asks, main answers. This
process never calls a model, never touches the network, and has
no tool access — if main dies, the control heartbeat goes stale
and she pauses herself rather than living unsupervised.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

from aria.life_sim import LifeSim

CONTROL_STALE_S = 90
STATE_WRITE_S = 2.0
DIARY_MAX_BYTES = 2 * 1024 * 1024
JOURNAL_PER_HOUR = 4
JOURNAL_PER_DAY = 20


def aria_dir(base_dir=None):
    return base_dir or os.path.join(os.path.expanduser("~"), "ARIA")


def paths(base_dir=None):
    d = aria_dir(base_dir)
    return {
        "dir": d,
        "state": os.path.join(d, "life_state.json"),
        "control": os.path.join(d, "life_control.json"),
        "diary": os.path.join(d, "life_diary.jsonl"),
        "journal_req": os.path.join(d, "life_journal_request.json"),
        "journal_resp": os.path.join(d, "life_journal_response.json"),
        "journal_budget": os.path.join(d, "life_journal_budget.json"),
    }


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json_atomic(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    os.replace(tmp, path)


def load_sim(base_dir=None):
    """Restore her life from the last snapshot; a missing or
    corrupt state file starts a fresh day, never a crash."""
    p = paths(base_dir)
    snap = _read_json(p["state"])
    if isinstance(snap, dict) and snap.get("version") == 1:
        try:
            return LifeSim(state=snap)
        except Exception:
            pass
    return LifeSim()


def append_diary(base_dir, entry, max_bytes=DIARY_MAX_BYTES):
    p = paths(base_dir)
    os.makedirs(p["dir"], exist_ok=True)
    try:
        if os.path.getsize(p["diary"]) > max_bytes:
            os.replace(p["diary"], p["diary"] + ".old")
    except OSError:
        pass
    with open(p["diary"], "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def decide_control(raw, now):
    """Map the raw control file to (sim_control, paused).

    No control file, or a heartbeat older than CONTROL_STALE_S,
    means the supervisor is gone: paused — she does not live
    unwatched. `running: false` is handled by the caller (exit).
    """
    if not isinstance(raw, dict):
        return None, True
    if now - float(raw.get("ts", 0)) > CONTROL_STALE_S:
        return None, True
    return {
        "asleep": bool(raw.get("asleep")),
        "with_alek": bool(raw.get("with_alek")),
        "media_playing": bool(raw.get("media_playing")),
        "quiet": bool(raw.get("quiet")),
    }, False


def can_request_journal(stamps, now):
    """Journal budget (spec hard caps): at most JOURNAL_PER_HOUR
    requests in the trailing hour and JOURNAL_PER_DAY today.
    stamps: list of epoch seconds of past requests."""
    hour = [s for s in stamps if now - s < 3600]
    day_start = now - (now % 86400)
    today = [s for s in stamps if s >= day_start]
    return len(hour) < JOURNAL_PER_HOUR and len(today) < JOURNAL_PER_DAY


class LifeProcess:
    def __init__(self, base_dir=None):
        self.p = paths(base_dir)
        self.sim = load_sim(base_dir)
        self._last_tick = None
        self._last_state_write = 0.0
        self._pending_journal = None      # request id awaiting main

    # -- journal handshake -------------------------------------------

    def _journal_stamps(self):
        data = _read_json(self.p["journal_budget"])
        return list(data.get("stamps", [])) if isinstance(data, dict) else []

    def _maybe_request_journal(self, event, now):
        if self._pending_journal is not None:
            return
        if os.path.exists(self.p["journal_req"]):
            return
        stamps = self._journal_stamps()
        if not can_request_journal(stamps, now):
            return
        req_id = f"{int(now * 1000)}"
        _write_json_atomic(self.p["journal_req"], {
            "id": req_id, "ts": now, "context": event.get("context", {}),
        })
        stamps.append(now)
        _write_json_atomic(self.p["journal_budget"],
                           {"stamps": stamps[-64:]})
        self._pending_journal = req_id

    def _poll_journal_response(self, now_dt):
        if self._pending_journal is None:
            return
        resp = _read_json(self.p["journal_resp"])
        if not isinstance(resp, dict) \
                or resp.get("id") != self._pending_journal:
            return
        text = str(resp.get("text") or "").strip()
        if text:
            append_diary(self.p["dir"], {
                "ts": now_dt.isoformat(), "kind": "journal", "text": text,
            })
        self._pending_journal = None
        for key in ("journal_req", "journal_resp"):
            try:
                os.remove(self.p[key])
            except OSError:
                pass

    # -- one tick -----------------------------------------------------

    def tick(self, now=None):
        """Advance once. Returns False when main asked us to stop."""
        now = time.time() if now is None else now
        now_dt = datetime.now().astimezone()
        if self._last_tick is None:
            self._last_tick = now
            return True
        dt = now - self._last_tick
        self._last_tick = now

        raw = _read_json(self.p["control"])
        if isinstance(raw, dict) and raw.get("running") is False:
            return False
        control, paused = decide_control(raw, now)

        if paused:
            snap = self.sim.snapshot(now_dt)
            snap["mode"] = "paused"
            if now - self._last_state_write >= STATE_WRITE_S:
                _write_json_atomic(self.p["state"], snap)
                self._last_state_write = now
            return True

        events = self.sim.step(control, dt, now, now_dt)
        for e in events:
            if e["kind"] == "want_journal":
                self._maybe_request_journal(e, now)
            elif e["kind"] == "activity":
                append_diary(self.p["dir"], {
                    "ts": now_dt.isoformat(), "kind": "activity",
                    "name": e["name"], "did": e["did"],
                })
            elif e["kind"] == "day":
                append_diary(self.p["dir"], {
                    "ts": now_dt.isoformat(), "kind": "day",
                    "date": e["date"],
                })
        self._poll_journal_response(now_dt)

        if now - self._last_state_write >= STATE_WRITE_S:
            _write_json_atomic(self.p["state"],
                               self.sim.snapshot(now_dt))
            self._last_state_write = now
        return True


def run(base_dir=None, tick_s=0.25, max_ticks=None):
    proc = LifeProcess(base_dir)
    ticks = 0
    while True:
        if not proc.tick():
            return 0
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            return 0
        time.sleep(tick_s)


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else None))
