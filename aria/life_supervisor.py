"""Main-process supervisor for ARIA's life subprocess (2026-10-29).

Owns the subprocess the way comclient owns its child: spawn it,
watch it, restart it with backoff, stop it cleanly. Also owns the
life_control.json heartbeat: every few seconds it writes what the
main process knows — PC/app lock, whether Alek is interacting,
whether media is playing, quiet hours — and the life process
decides the rest. If this supervisor dies, the heartbeat goes
stale and the life process pauses itself (see life_process).

Policy lives here and in life_sim, never in the child: the child
cannot lock, speak, or act — it only lives in its room.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

from aria.life_process import paths

CONTROL_PERIOD_S = 2.0
MAX_RESTARTS_IN_WINDOW = 3
RESTART_WINDOW_S = 300
BACKOFF_S = (2, 5, 15, 60)


def _write_json_atomic(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


class LifeSupervisor:
    def __init__(self, control_provider, log=None, base_dir=None,
                 popen=None):
        self._provider = control_provider
        self._log = log or (lambda msg: None)
        self.p = paths(base_dir)
        self._popen = popen or subprocess.Popen
        self._proc = None
        self._stop = threading.Event()
        self._thread = None
        self._restarts = []               # epoch stamps of respawns
        self._next_restart_at = 0.0
        self.gave_up = False

    # -- lifecycle ---------------------------------------------------

    def start(self):
        self._spawn()
        self._write_control(running=True)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._log("Life room: subprocess started")
        return True

    def stop(self):
        self._stop.set()
        try:
            self._write_control(running=False)
        except Exception:
            pass
        proc = self._proc
        if proc is not None and proc.poll() is None:
            for _ in range(30):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        self._proc = None

    def _spawn(self):
        args = [sys.executable, "-m", "aria.life_process"]
        if self.p["dir"]:
            args.append(self.p["dir"])
        self._proc = self._popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # -- control heartbeat --------------------------------------------

    def _write_control(self, running=True):
        payload = {"ts": time.time(), "running": bool(running)}
        if running:
            try:
                payload.update(self._provider() or {})
            except Exception:
                pass            # a broken probe never kills the beat
        _write_json_atomic(self.p["control"], payload)

    # -- supervision ----------------------------------------------------

    def _supervise_once(self, now):
        """One supervision pass; returns 'ok', 'respawned',
        'waiting', or 'gave_up'. Split out for tests."""
        if self._proc is not None and self._proc.poll() is None:
            return "ok"
        if self.gave_up:
            return "gave_up"
        if now < self._next_restart_at:
            return "waiting"
        self._restarts = [t for t in self._restarts
                          if now - t < RESTART_WINDOW_S]
        if len(self._restarts) >= MAX_RESTARTS_IN_WINDOW:
            self.gave_up = True
            self._log("Life room: subprocess kept crashing; "
                      "staying down until the next launch")
            return "gave_up"
        delay = BACKOFF_S[min(len(self._restarts), len(BACKOFF_S) - 1)]
        self._restarts.append(now)
        self._next_restart_at = now + delay
        try:
            self._spawn()
        except Exception as e:
            self._log(f"Life room: respawn failed: {e}")
            return "waiting"
        self._log("Life room: subprocess restarted")
        return "respawned"

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._write_control(running=True)
                self._supervise_once(time.time())
            except Exception:
                pass
            self._stop.wait(CONTROL_PERIOD_S)
