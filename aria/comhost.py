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

    def _endpoint(self):
        dev = self._au.GetSpeakers()
        if hasattr(dev, "EndpointVolume"):
            return dev, dev.EndpointVolume, None
        iface = dev.Activate(self._iface_cls._iid_, self._clsctx, None)
        vol = self._cast(iface, self._pointer(self._iface_cls))
        return dev, vol, iface

    def query_dev(self):
        dev = None
        vol = None
        iface = None
        try:
            dev, vol, iface = self._endpoint()
            return float(vol.GetMasterVolumeLevelScalar())
        finally:
            del vol, iface, dev

    def apply_dev(self, level):
        dev = None
        vol = None
        iface = None
        try:
            dev, vol, iface = self._endpoint()
            vol.SetMasterVolumeLevelScalar(
                max(0.0, min(1.0, level)), None)
        finally:
            del vol, iface, dev

    def query_spot(self):
        sessions = self._au.GetAllSessions()
        try:
            for s in sessions:
                if s.Process and s.Process.name().lower() == "spotify.exe":
                    return float(s.SimpleAudioVolume.GetMasterVolume())
            return 1.0
        finally:
            del sessions

    def apply_spot(self, level):
        sessions = self._au.GetAllSessions()
        try:
            for s in sessions:
                if s.Process and s.Process.name().lower() == "spotify.exe":
                    s.SimpleAudioVolume.SetMasterVolume(
                        max(0.0, min(1.0, level)), None)
        finally:
            del sessions

    def query_dev_mute(self):
        dev = None
        vol = None
        iface = None
        try:
            dev, vol, iface = self._endpoint()
            return bool(vol.GetMute())
        finally:
            del vol, iface, dev

    def apply_dev_mute(self, muted):
        dev = None
        vol = None
        iface = None
        try:
            dev, vol, iface = self._endpoint()
            vol.SetMute(1 if muted else 0, None)
        finally:
            del vol, iface, dev

    def query_spot_mute(self):
        sessions = self._au.GetAllSessions()
        try:
            for s in sessions:
                if s.Process and s.Process.name().lower() == "spotify.exe":
                    return bool(s.SimpleAudioVolume.GetMute())
            return False
        finally:
            del sessions

    def apply_spot_mute(self, muted):
        sessions = self._au.GetAllSessions()
        try:
            for s in sessions:
                if s.Process and s.Process.name().lower() == "spotify.exe":
                    s.SimpleAudioVolume.SetMute(1 if muted else 0, None)
        finally:
            del sessions

    def get_state(self) -> Dict[str, Any]:
        """Assemble the four-part volume state. Each part is queried
        independently (as the old worker did); a failed part comes
        back None and its error is listed under "errors" so the
        client can keep last-known state and surface the failure."""
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
        if errors:
            state["errors"] = errors
        return state


# ---------------- COM core ----------------

_STOP = object()

_GC_INTERVAL_S = 30.0


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
        text = req.get("text", "")
        whisper = bool(req.get("whisper"))
        engine = self._engine
        if engine is None:
            self._ensure_engine()
            engine = self._engine
        if engine is None:
            self._emit({"event": "sapi.finished", "id": rid,
                        "ok": False, "error": "no SAPI engine available"})
            return
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
                _log(f"SAPI say err: {e}")
                ok = False
            self._emit({"event": "sapi.finished", "id": rid, "ok": ok})

    def _sapi_stop(self):
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                pass
        self._finish_current()

    # -- dispatch ----------------------------------------------------

    def handle(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        rid = req.get("id")
        op = req.get("op")
        try:
            if op == "ping":
                result: Any = {"pong": True}
            elif op == "volume.get_state":
                result = self._vol.get_state()
            elif op == "volume.set_device":
                level = float(req["level"])
                self._vol.apply_dev(level)
                result = {"applied": max(0.0, min(1.0, level))}
            elif op == "volume.set_spotify":
                level = float(req["level"])
                self._vol.apply_spot(level)
                result = {"applied": max(0.0, min(1.0, level))}
            elif op == "volume.set_mute":
                muted = bool(req["muted"])
                if req.get("target") == "spotify":
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
