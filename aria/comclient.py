"""A.R.I.A. COM host client — the main process's only path to COM.

Crash round 5 (2026-10-21): process isolation. Every COM consumer
(pycaw volume, pyttsx3 fallback voice) now lives in the helper
process (aria/comhost.py); this module owns that subprocess and the
JSON-lines protocol to it. The main process imports no COM library
at all — tests/test_isolation.py enforces that physically.

Behaviour contract:
  * ``get_client()`` is the process-wide singleton; it spawns the
    helper on first use and registers an atexit hook that closes the
    helper's stdin so it exits with the parent.
  * ``request(op, payload, timeout)`` returns the response's result,
    raising ComHostDown on transport failure/timeout and ComHostError
    when the helper answers ok:false.
  * ``send(op, payload)`` writes a request and returns its id without
    waiting — for ops whose completion arrives as an async event
    (sapi.say → the "sapi.finished" event carries the same id).
  * Event listeners receive every async event dict; down listeners
    fire when the helper exits unexpectedly.
  * Supervisor: an unexpected exit fails all pending requests,
    surfaces the death through aria.agent.failures, and respawns
    with backoff (2 s, 5 s, 15 s). After the ladder is exhausted the
    client stays down — requests raise ComHostDown immediately —
    until a request arrives more than 30 s after exhaustion, which
    earns exactly one fresh spawn attempt (a host that stayed up
    10 s or more resets the ladder on its next exit).
"""

from __future__ import annotations

import atexit
import itertools
import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple


def _default_command() -> List[str]:
    """Launch line for the helper. Deliberately the file path, NOT
    ``-m aria.comhost``: the -m form executes aria/__init__.py,
    which eagerly imports the whole app (speech, hud, cv2, ...) —
    the helper would inherit every main-process dependency and
    import side-effect, the opposite of isolation. Run by path,
    comhost.py needs nothing but the stdlib and the COM libraries."""
    return [sys.executable,
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "comhost.py")]


class ComHostDown(Exception):
    """The helper is unreachable: exited, unspawned, or timed out."""


class ComHostError(Exception):
    """The helper answered, but the op failed (ok:false)."""


# A respawned host that survives at least this long resets the
# backoff ladder — it was a crash, not a spawn failure loop.
_UPTIME_RESET_S = 10.0
_EXHAUSTED_RETRY_S = 30.0


class ComHostClient:
    def __init__(self, command: Optional[List[str]] = None,
                 backoff: Tuple[float, ...] = (2.0, 5.0, 15.0)):
        self._command = command or _default_command()
        self._backoff = tuple(backoff) or (0.0,)
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: Dict[int, Tuple[threading.Event, Dict[str, Any]]] = {}
        self._next_id = itertools.count(1)
        self._event_listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._down_listeners: List[Callable[[], None]] = []
        self._proc: Optional[subprocess.Popen] = None
        self._spawned_at = 0.0
        self._up = False
        self._stopping = False
        self._attempt = 0
        self._exhausted_at: Optional[float] = None
        self._try_spawn()

    # -- lifecycle ---------------------------------------------------

    def _try_spawn(self) -> bool:
        with self._lock:
            if self._stopping or self._proc is not None:
                return self._proc is not None
            try:
                proc = subprocess.Popen(
                    self._command, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, text=True, bufsize=1)
            except Exception:
                return False
            self._proc = proc
            self._spawned_at = time.monotonic()
            self._up = True
            self._exhausted_at = None
        threading.Thread(target=self._reader_loop, args=(proc,),
                         daemon=True, name="aria-comhost-reader").start()
        return True

    def _reader_loop(self, proc: subprocess.Popen):
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if isinstance(msg, dict):
                    self._dispatch(msg)
        except Exception:
            pass
        self._on_exit(proc)

    def _dispatch(self, msg: Dict[str, Any]):
        if "event" in msg:
            for cb in list(self._event_listeners):
                try:
                    cb(msg)
                except Exception:
                    pass
            return
        rid = msg.get("id")
        if rid is None:
            return
        with self._lock:
            entry = self._pending.pop(rid, None)
        if entry is not None:
            ev, box = entry
            box["resp"] = msg
            ev.set()

    def _on_exit(self, proc: subprocess.Popen):
        with self._lock:
            if self._proc is not proc:
                return
            self._proc = None
            was_up = self._up
            self._up = False
            pending = self._pending
            self._pending = {}
            stopping = self._stopping
            uptime = time.monotonic() - self._spawned_at
        for ev, box in pending.values():
            box["error"] = ComHostDown("comhost exited")
            ev.set()
        if stopping or not was_up:
            return
        for cb in list(self._down_listeners):
            try:
                cb()
            except Exception:
                pass
        try:
            from aria.agent.failures import surface_failure
            surface_failure(
                "comclient.comhost", "comhost process exited",
                decision="retry",
                user_message="A background helper restarted itself.")
        except Exception:
            pass
        with self._lock:
            if uptime >= _UPTIME_RESET_S:
                self._attempt = 0
            self._attempt += 1
            attempt = self._attempt
        if attempt <= len(self._backoff):
            delay = self._backoff[attempt - 1]
            t = threading.Thread(target=self._respawn_after,
                                 args=(delay,), daemon=True,
                                 name="aria-comhost-respawn")
            t.start()
        else:
            with self._lock:
                self._exhausted_at = time.monotonic()

    def _respawn_after(self, delay: float):
        time.sleep(delay)
        self._try_spawn()

    def close(self):
        with self._lock:
            self._stopping = True
            proc = self._proc
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass

    # -- state ---------------------------------------------------------

    def is_up(self) -> bool:
        return self._up and self._proc is not None

    def add_event_listener(self, cb: Callable[[Dict[str, Any]], None]):
        if cb not in self._event_listeners:
            self._event_listeners.append(cb)

    def add_down_listener(self, cb: Callable[[], None]):
        if cb not in self._down_listeners:
            self._down_listeners.append(cb)

    # -- protocol ------------------------------------------------------

    def _ensure_proc(self) -> subprocess.Popen:
        proc = self._proc
        if proc is not None and self._up:
            return proc
        # Down. An exhausted ladder earns one fresh spawn attempt
        # once the retry window has passed; anything else raises.
        with self._lock:
            exhausted_at = self._exhausted_at
        if (exhausted_at is not None
                and time.monotonic() - exhausted_at >= _EXHAUSTED_RETRY_S):
            with self._lock:
                self._attempt = 0
                self._exhausted_at = None
            if self._try_spawn():
                proc = self._proc
                if proc is not None:
                    return proc
        raise ComHostDown("comhost is down")

    def _send_tracked(self, op: str,
                      payload: Optional[Dict[str, Any]] = None):
        """Write a request and return (id, event, box) for its
        response. The entry is captured here, under the same lock
        that registers it: a fast reply can pop the pending entry
        and set the event before a caller that re-looked the id up
        afterwards would ever see it (the reply races the lookup),
        so request() must never re-resolve the id itself."""
        proc = self._ensure_proc()
        rid = next(self._next_id)
        msg: Dict[str, Any] = {"id": rid, "op": op}
        if payload:
            msg.update(payload)
        ev = threading.Event()
        box: Dict[str, Any] = {}
        with self._lock:
            self._pending[rid] = (ev, box)
        try:
            with self._write_lock:
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()
        except Exception as e:
            with self._lock:
                self._pending.pop(rid, None)
            self._on_exit(proc)
            raise ComHostDown(f"comhost write failed: {e}")
        return rid, ev, box

    def send(self, op: str, payload: Optional[Dict[str, Any]] = None) -> int:
        """Write a request; return its id. The response still resolves
        internally (and is discarded if nobody waits on it); async
        events arrive through the event listeners."""
        rid, _ev, _box = self._send_tracked(op, payload)
        return rid

    def request(self, op: str, payload: Optional[Dict[str, Any]] = None,
                timeout: float = 10.0) -> Any:
        rid, ev, box = self._send_tracked(op, payload)
        if not ev.wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise ComHostDown(f"comhost timeout waiting for {op}")
        if "error" in box:
            raise box["error"]
        resp = box.get("resp") or {}
        if not resp.get("ok"):
            raise ComHostError(resp.get("error") or f"{op} failed")
        return resp.get("result")


_CLIENT: Optional[ComHostClient] = None
_CLIENT_LOCK = threading.Lock()


def get_client() -> ComHostClient:
    """Process-wide singleton (spawns the helper on first use)."""
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = ComHostClient()
                atexit.register(_CLIENT.close)
    return _CLIENT
