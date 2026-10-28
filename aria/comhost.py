"""A.R.I.A. COM host — the ONLY process allowed to touch COM.

Crash round 5 (2026-10-21): process isolation. Four faulthandler
dumps proved one mechanism: a comtypes wrapper orphaned in a
reference cycle and finalised by the cyclic GC outside its COM
apartment kills the whole process in Release(). Three in-process
defences failed (site containment, lazy SAPI init, the round-4 GC
regime — gc.collect() sweeps ALL garbage process-wide, so with two
apartments any collect is cross-apartment for the other apartment's
orphans). The invariant those defences needed does not exist inside
a multi-threaded process, so COM leaves the process entirely.

This helper is launched by aria.comclient (by file path — the
``-m aria.comhost`` form would execute aria/__init__.py and drag the
whole app into the helper) and speaks JSON lines over stdio:
requests ``{"id", "op", ...}`` get ``{"id", "ok",
"result"|"error"}``; utterance completion arrives asynchronously as
``{"event": "sapi.finished", "id", "ok"}``. All logging goes to
stderr; stdout carries protocol only.

Threading: a dumb stdin reader thread moves parsed lines into a work
queue (no COM on it). ONE COM thread owns EVERYTHING: it calls
CoInitialize (STA), then runs the round-4 regime where it is finally
sound — one apartment total — ``gc.collect()`` once, ``gc.disable()``,
and a collect every 30 s on that thread. The work loop ticks at
20 ms (the cadence the old in-process _SapiVoice loop used), services
volume ops and the SAPI queue, and pumps pyttsx3's external event
loop between items. When stdin reaches EOF (parent gone), the COM
thread shuts down and the process exits — no orphans.

The pycaw query/apply functions and the pyttsx3 lazy-init engine
below are the code that used to live in aria.hud.VolumeManager._worker
and aria.speech._SapiVoice, moved as verbatim as the move allows.

Health follow-up (2026-10-21 session log): isolation held — the
main process never died — but the helper's own COM objects went
stale under it. The facade polls state every 1 s; the moved code
re-resolved every wrapper per operation (four endpoint activations
plus two full session enumerations per poll), and wrappers whose
sessions had since ended were finalised by the 30 s collect with
Release() faulting on dead pointers — 809 "COM method call without
VTable" ValueErrors and 879 ignored __del__ tracebacks in one
session. Three fixes: (1) wrappers are now CACHED on the COM
thread and a stale-object failure (VTable ValueError / COMError /
OSError) invalidates the cache and re-resolves ONCE per operation
before surfacing honestly; (2) an absent Spotify session backs off
for ~10 s instead of being re-enumerated every poll, serving
last-known state meanwhile; (3) sys.unraisablehook compresses the
remaining __del__ noise into counted one-line stderr logs.
"""

from __future__ import annotations

import gc
import json
import queue
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional


def _log(msg: str) -> None:
    print(f"[comhost] {msg}", file=sys.stderr, flush=True)


# ---------------- unraisable noise compression ----------------
_UNRAISABLE_COUNTS: Dict[str, int] = {}
_UNRAISABLE_LOCK = threading.Lock()


def _unraisable_hook(args) -> None:
    """sys.unraisablehook replacement. A comtypes wrapper finalised
    after its native object died raises inside __del__ (Release() on
    a dead pointer) — unraisable by construction, and Python's
    default hook prints a full traceback for EACH one (879 in the
    2026-10-21 session log, drowning every real signal). Compress
    to a counted one-liner on stderr: the first occurrence of each
    error kind, then every 100th."""
    etype = getattr(args, "exc_type", None)
    exc = getattr(args, "exc_value", None)
    key = f"{getattr(etype, '__name__', 'Error')}: {exc}"[:120]
    with _UNRAISABLE_LOCK:
        n = _UNRAISABLE_COUNTS.get(key, 0) + 1
        _UNRAISABLE_COUNTS[key] = n
    if n == 1 or n % 100 == 0:
        print(f"[comhost] unraisable suppressed (x{n}): {key}",
              file=sys.stderr, flush=True)


def unraisable_count() -> int:
    """Total unraisable exceptions compressed so far (diagnostics)."""
    with _UNRAISABLE_LOCK:
        return sum(_UNRAISABLE_COUNTS.values())


def _is_stale_error(exc: BaseException) -> bool:
    """True when exc means 'this COM wrapper's native object is
    gone' rather than 'the operation failed': comtypes raises
    ValueError('COM method call without VTable') for a call on a
    pointer whose vtable has been torn down, COMError for dead
    interfaces, and OSError when the native call faults (the access
    violations in the session log's __del__ tracebacks). All three
    are cured the same way: drop the cached wrapper, re-resolve,
    retry once."""
    if isinstance(exc, ValueError) and "VTable" in str(exc):
        return True
    if type(exc).__name__ == "COMError":
        return True
    if isinstance(exc, OSError):
        return True
    return False


# ---------------- Fallback TTS (pyttsx3) ----------------
# Moved verbatim from aria/speech.py (round 3 lazy-init semantics live
# in _ComCore._ensure_engine below).

def init_pyttsx3(on_log: Optional[Callable[[str], None]] = None):
    """Initialize pyttsx3 and attempt to select a Windows Natural voice."""
    import pyttsx3
    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", 170)
        voices = engine.getProperty("voices") or []
        for v in voices:
            name = (v.name or "").lower()
            if any(k in name for k in ["aria", "jenny", "guy", "natural"]):
                engine.setProperty("voice", v.id)
                if on_log:
                    on_log(f"Voice fallback: {v.name}")
                return engine
        return engine
    except Exception as e:
        if on_log:
            on_log(f"Voice fallback init failed: {e}")
        return None


# ---------------- Volume (pycaw) ----------------
# The query/apply closures from hud.VolumeManager._worker, turned into
# methods over the module refs captured at construction. Construction
# happens on the COM thread; a failed import degrades every volume op
# to a per-part error instead of killing the helper.

class _VolumeOps:
    def __init__(self):
        self.available = False
        self._au = None
        self._iface_cls = None
        self._clsctx = None
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            from ctypes import cast, POINTER
            self._au = AudioUtilities
            self._iface_cls = IAudioEndpointVolume
            self._clsctx = CLSCTX_ALL
            self._cast = cast
            self._pointer = POINTER
            self.available = True
        except Exception as e:
            _log(f"volume ops unavailable: {type(e).__name__}: {e}")
        # Cached wrappers (health follow-up): resolved once, held on
        # the COM thread, dropped the moment a call proves them
        # stale. _spot_state is the last-known Spotify (vol, muted)
        # served while the session is absent / backing off.
        self._dev_cache = None        # (dev, vol, iface)
        self._spot_cache = None       # SimpleAudioVolume wrapper
        self._spot_state = (1.0, False)
        self._spot_backoff_until = 0.0
        # The PLAYER session generalises the Spotify one (Alek,
        # 2026-10-28: a slider for whatever app is playing, not
        # just Spotify). The target process defaults to spotify.exe
        # — the historical behaviour — and the facade retargets it
        # to the active SMTC app's process as playback moves. State
        # keys keep their spotify_* names as aliases of player_*.
        self._player_process = "spotify.exe"
        self._player_present = False

    # -- device endpoint: cached wrapper, one stale re-resolve ------
    def _resolve_endpoint(self):
        dev = self._au.GetSpeakers()
        if hasattr(dev, "EndpointVolume"):
            return dev, dev.EndpointVolume, None
        iface = dev.Activate(self._iface_cls._iid_, self._clsctx, None)
        vol = self._cast(iface, self._pointer(self._iface_cls))
        return dev, vol, iface

    def _endpoint_vol(self):
        if self._dev_cache is None:
            self._dev_cache = self._resolve_endpoint()
        return self._dev_cache[1]

    def _dev_call(self, fn):
        """fn(endpoint_volume) with the stale-guard: a dead-wrapper
        failure drops the cache, re-resolves, and retries ONCE; a
        second failure (or a non-stale one) propagates to the caller
        and is surfaced honestly by get_state / handle."""
        try:
            return fn(self._endpoint_vol())
        except Exception as e:
            if not _is_stale_error(e):
                raise
            self._dev_cache = None
            return fn(self._endpoint_vol())

    def query_dev(self):
        return float(self._dev_call(
            lambda v: v.GetMasterVolumeLevelScalar()))

    def apply_dev(self, level):
        level = max(0.0, min(1.0, level))
        self._dev_call(
            lambda v: v.SetMasterVolumeLevelScalar(level, None))

    def query_dev_mute(self):
        return bool(self._dev_call(lambda v: v.GetMute()))

    def apply_dev_mute(self, muted):
        self._dev_call(lambda v: v.SetMute(1 if muted else 0, None))

    # -- Player session: cached wrapper + absence backoff ----------
    # (born as the Spotify session; the target process is now
    # retargetable — see _player_process above)
    def set_player_process(self, name):
        """Retarget the player session at another process (e.g.
        vlc.exe when a movie is playing). A change drops the cached
        wrapper and resets last-known state — the old app's levels
        say nothing about the new one."""
        name = str(name or "").strip()
        if not name or name.lower() == self._player_process.lower():
            return
        self._player_process = name
        self._spot_cache = None
        self._spot_state = (1.0, False)
        self._spot_backoff_until = 0.0
        self._player_present = False

    def _resolve_spotify(self):
        """The player process's SimpleAudioVolume wrapper, or None
        when the session is absent (or resolution is backing off).
        A dead session must not be re-enumerated on every 1 s
        facade poll: a successful enumeration that finds no match
        arms a ~10 s backoff during which last-known state is
        served."""
        if self._spot_cache is not None:
            return self._spot_cache
        now = time.monotonic()
        if now < self._spot_backoff_until:
            return None
        sessions = self._au.GetAllSessions()
        try:
            for s in sessions:
                if s.Process and s.Process.name().lower() == \
                        self._player_process.lower():
                    self._spot_cache = s.SimpleAudioVolume
                    self._player_present = True
                    return self._spot_cache
        finally:
            del sessions
        self._player_present = False
        self._spot_backoff_until = now + _SPOTIFY_BACKOFF_S
        return None

    def _spot_call(self, fn):
        """fn(session_volume) -> value, with the stale-guard.
        Returns (True, value) when a live session was used,
        (False, None) when Spotify is absent / backing off — the
        caller then serves last-known state, quietly."""
        vol = self._resolve_spotify()
        if vol is None:
            return False, None
        self._player_present = True
        try:
            return True, fn(vol)
        except Exception as e:
            if not _is_stale_error(e):
                raise
            self._spot_cache = None
            vol = self._resolve_spotify()
            if vol is None:
                return False, None
            return True, fn(vol)

    def query_spot(self):
        found, val = self._spot_call(
            lambda v: float(v.GetMasterVolume()))
        if found:
            self._spot_state = (val, self._spot_state[1])
            return val
        return self._spot_state[0]

    def apply_spot(self, level):
        level = max(0.0, min(1.0, level))
        found, _ = self._spot_call(
            lambda v: v.SetMasterVolume(level, None))
        if found:
            self._spot_state = (level, self._spot_state[1])

    def query_spot_mute(self):
        found, val = self._spot_call(lambda v: bool(v.GetMute()))
        if found:
            self._spot_state = (self._spot_state[0], val)
            return val
        return self._spot_state[1]

    def apply_spot_mute(self, muted):
        found, _ = self._spot_call(
            lambda v: v.SetMute(1 if muted else 0, None))
        if found:
            self._spot_state = (self._spot_state[0], bool(muted))

    # Player-named aliases for the session ops (the session is the
    # playing app's, whatever it is — Spotify by default).
    query_player = query_spot
    apply_player = apply_spot
    query_player_mute = query_spot_mute
    apply_player_mute = apply_spot_mute

    def get_state(self) -> Dict[str, Any]:
        """Assemble the four-part volume state. Each part is queried
        independently (as the old worker did); a failed part comes
        back None and its error is listed under "errors" so the
        client can keep last-known state and surface the failure.
        The player parts also ride under their historical spotify_*
        names, and the state names the player process + whether a
        live session for it was found."""
        if not self.available:
            raise RuntimeError("pycaw unavailable in comhost")
        state: Dict[str, Any] = {}
        errors = []
        for key, fn in (("device_vol", self.query_dev),
                        ("spotify_vol", self.query_spot),
                        ("device_muted", self.query_dev_mute),
                        ("spotify_muted", self.query_spot_mute)):
            try:
                state[key] = fn()
            except Exception as e:
                state[key] = None
                errors.append(f"{key}: {type(e).__name__}: {e}")
        state["player_vol"] = state["spotify_vol"]
        state["player_muted"] = state["spotify_muted"]
        state["player_process"] = self._player_process
        state["player_present"] = bool(self._player_present)
        if errors:
            state["errors"] = errors
        return state


# ---------------- COM core ----------------

_STOP = object()

_GC_INTERVAL_S = 30.0
_SPOTIFY_BACKOFF_S = 10.0


class _StaleEngine(Exception):
    """Internal signal: the cached SAPI engine's COM objects died
    mid-operation — drop it, re-init once, retry the say once."""


class _ComCore:
    """Owns all COM state. Every method runs on the COM thread."""

    def __init__(self, emit: Callable[[Dict[str, Any]], None]):
        self._emit = emit
        self._vol: Optional[_VolumeOps] = None
        self._engine = None
        self._use_loop = False
        self._current = None  # (request id, (volume, rate) restore or None)
        self._last_gc = time.monotonic()

    # -- lifecycle --------------------------------------------------

    def start(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass  # non-Windows / no pywin32: ops degrade individually
        # The round-4 regime, sound in this process: exactly one
        # apartment exists, and this thread is the only collector.
        try:
            gc.collect()
        except Exception:
            pass
        try:
            gc.disable()
        except Exception:
            pass
        self._vol = _VolumeOps()
        self._last_gc = time.monotonic()

    def shutdown(self):
        # Teardown collection ON THIS THREAD: any wrapper cycles
        # still alive are finalised here, in their owning apartment.
        try:
            gc.collect()
        except Exception:
            pass
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass

    # -- SAPI engine (moved from aria.speech._SapiVoice._run) -------

    def _finish_current(self, ok: bool = True):
        if self._current is None:
            return
        rid, restore = self._current
        self._current = None
        if restore and self._engine is not None:
            try:
                self._engine.setProperty("volume", restore[0])
                self._engine.setProperty("rate", restore[1])
            except Exception:
                pass
        self._emit({"event": "sapi.finished", "id": rid, "ok": ok})

    def _ensure_engine(self) -> bool:
        """Create the pyttsx3 engine HERE, lazily, at first actual
        use (round 3 semantics): SAPI is only the fallback for a dead
        Edge, and pyttsx3.init() makes comtypes generate its SpeechLib
        type library in-process — the generation temporaries are
        cyclic comtypes wrappers, so init is confined to sessions
        that actually fall back, and the gc.collect() immediately
        below flushes them on THIS thread, in the owning apartment.
        Init failure logs, the engine stays absent, the pending say
        is skipped, and the next say retries."""
        if self._engine is not None:
            return True
        try:
            self._engine = init_pyttsx3(on_log=_log)
        except Exception as e:
            _log(f"Voice fallback init failed: {e}")
            self._engine = None
        try:
            gc.collect()
        except Exception:
            pass
        if self._engine is None:
            return False
        try:
            self._engine.startLoop(False)
            self._use_loop = True
        except Exception:
            self._use_loop = False
        if self._use_loop:
            try:
                self._engine.connect(
                    "finished-utterance",
                    lambda *_a, **_k: self._finish_current())
            except Exception:
                pass
        return True

    def _sapi_say(self, req: Dict[str, Any]):
        rid = req.get("id")
        if self._engine is None:
            self._ensure_engine()
        if self._engine is None:
            self._emit({"event": "sapi.finished", "id": rid,
                        "ok": False, "error": "no SAPI engine available"})
            return
        try:
            self._sapi_say_with_engine(req)
        except _StaleEngine:
            # Same stale-wrapper disease as the volume path: the
            # engine's SAPI objects died under it. Drop the engine,
            # re-init ONCE, retry the say ONCE; a second stale
            # failure finishes honestly as ok False.
            self._engine = None
            self._use_loop = False
            self._current = None
            if not self._ensure_engine():
                self._emit({"event": "sapi.finished", "id": rid,
                            "ok": False,
                            "error": "no SAPI engine available"})
                return
            try:
                self._sapi_say_with_engine(req)
            except _StaleEngine:
                self._emit({"event": "sapi.finished", "id": rid,
                            "ok": False,
                            "error": "SAPI engine failed after re-init"})

    def _sapi_say_with_engine(self, req: Dict[str, Any]):
        rid = req.get("id")
        text = req.get("text", "")
        whisper = bool(req.get("whisper"))
        engine = self._engine
        if self._use_loop:
            self._finish_current()  # defensive; the client serialises
            restore = None
            try:
                if whisper:
                    v0 = engine.getProperty("volume")
                    r0 = engine.getProperty("rate")
                    restore = (v0, r0)
                    engine.setProperty("volume", min(float(v0), 0.4))
                    engine.setProperty("rate", max(60, int(r0) - 40))
                engine.say(text)
            except Exception as e:
                if _is_stale_error(e):
                    raise _StaleEngine() from e
                _log(f"SAPI say err: {e}")
                self._current = (rid, restore)
                self._finish_current(ok=False)
            else:
                self._current = (rid, restore)
        else:
            ok = True
            try:
                if whisper:
                    v0 = engine.getProperty("volume")
                    r0 = engine.getProperty("rate")
                    try:
                        engine.setProperty("volume", min(float(v0), 0.4))
                        engine.setProperty("rate", max(60, int(r0) - 40))
                        engine.say(text)
                        engine.runAndWait()
                    finally:
                        engine.setProperty("volume", v0)
                        engine.setProperty("rate", r0)
                else:
                    engine.say(text)
                    engine.runAndWait()
            except Exception as e:
                if _is_stale_error(e):
                    raise _StaleEngine() from e
                _log(f"SAPI say err: {e}")
                ok = False
            self._emit({"event": "sapi.finished", "id": rid, "ok": ok})

    def _sapi_stop(self):
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception as e:
                if _is_stale_error(e):
                    # Dead engine: drop it so the next say re-inits
                    # instead of queueing onto a corpse.
                    self._engine = None
                    self._use_loop = False
        self._finish_current()

    # -- dispatch ----------------------------------------------------

    def handle(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        rid = req.get("id")
        op = req.get("op")
        try:
            if op == "ping":
                result: Any = {"pong": True}
            elif op == "volume.get_state":
                # The facade names the playing app's process with
                # each poll; the host retargets its player session.
                if req.get("player_process"):
                    self._vol.set_player_process(req["player_process"])
                result = self._vol.get_state()
            elif op == "volume.set_device":
                level = float(req["level"])
                self._vol.apply_dev(level)
                result = {"applied": max(0.0, min(1.0, level))}
            elif op in ("volume.set_spotify", "volume.set_player"):
                level = float(req["level"])
                self._vol.apply_spot(level)
                result = {"applied": max(0.0, min(1.0, level))}
            elif op == "volume.set_mute":
                muted = bool(req["muted"])
                if req.get("target") in ("spotify", "player"):
                    self._vol.apply_spot_mute(muted)
                else:
                    self._vol.apply_dev_mute(muted)
                result = {"applied": muted}
            elif op == "sapi.say":
                # Ack immediately; completion arrives as the async
                # sapi.finished event with this same id.
                self._sapi_say(req)
                return {"id": rid, "ok": True, "result": {"queued": True}}
            elif op == "sapi.stop":
                self._sapi_stop()
                result = {"stopped": True}
            elif op == "sapi.status":
                result = {"ready": self._engine is not None,
                          "volume_available": bool(
                              self._vol and self._vol.available)}
            else:
                return {"id": rid, "ok": False,
                        "error": f"unknown op: {op}"}
            return {"id": rid, "ok": True, "result": result}
        except Exception as e:
            return {"id": rid, "ok": False,
                    "error": f"{type(e).__name__}: {e}"}

    def tick(self):
        if self._use_loop and self._engine is not None:
            try:
                self._engine.iterate()
            except Exception:
                pass
        now = time.monotonic()
        if now - self._last_gc >= _GC_INTERVAL_S:
            self._last_gc = now
            try:
                gc.collect()
            except Exception:
                pass

    def run(self, work_q: "queue.Queue"):
        self.start()
        try:
            while True:
                try:
                    req = work_q.get(timeout=0.02)
                except queue.Empty:
                    req = None
                if req is _STOP:
                    break
                if req is not None:
                    resp = self.handle(req)
                    if resp is not None:
                        self._emit(resp)
                self.tick()
        finally:
            self.shutdown()


# ---------------- stdio plumbing ----------------

def main() -> int:
    sys.unraisablehook = _unraisable_hook
    work_q: "queue.Queue" = queue.Queue()
    out_q: "queue.Queue" = queue.Queue()
    core = _ComCore(emit=out_q.put)

    def reader():
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except Exception:
                    continue
                if isinstance(req, dict):
                    work_q.put(req)
        except Exception:
            pass
        finally:
            work_q.put(_STOP)

    threading.Thread(target=reader, daemon=True,
                     name="comhost-stdin").start()
    com_thread = threading.Thread(target=core.run, args=(work_q,),
                                  daemon=True, name="comhost-com")
    com_thread.start()
    try:
        while com_thread.is_alive():
            try:
                msg = out_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                sys.stdout.write(json.dumps(msg) + "\n")
                sys.stdout.flush()
            except (BrokenPipeError, OSError):
                break
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
