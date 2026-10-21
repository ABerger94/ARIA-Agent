"""COM host unit tests (crash round 5: process isolation).

aria/comhost.py is the ONLY process that touches COM. These tests
drive its _ComCore in-process with stubbed pycaw / pyttsx3 /
pythoncom / comtypes modules and pin:

  * the volume state assembly (four parts, per-part failure comes
    back as None + an errors entry, as the old hud worker's poll
    produced) and the set/mute op dispatch;
  * the round-3 lazy SAPI contract, moved here from the old
    test_com_hotfix.py speech tests: thread/core start and stop
    must never init the engine; the first say inits exactly once,
    immediately followed by a gc.collect() flushing the SpeechLib
    generation temporaries in-apartment; an init failure leaves the
    engine absent, completes the say as finished/ok False, and the
    next say retries;
  * the say -> sapi.finished event flow, both through an external
    loop engine (finished-utterance callback) and through the
    runAndWait fallback.

Suite conventions: heavy modules are stubbed in sys.modules at
import; each test installs the exact module surface it needs.
"""
import gc
import importlib
import os
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_pkg = types.ModuleType("aria")
_pkg.__path__ = [os.path.join(HERE, "..", "aria")]
sys.modules["aria"] = _pkg

comhost = importlib.import_module("aria.comhost")


class _GcRecorder:
    """gc.collect replacement recording the calling thread's ident."""

    def __init__(self):
        self._real = gc.collect
        self._calls = []
        self._lock = threading.Lock()

    def __call__(self, *a, **k):
        with self._lock:
            self._calls.append(threading.get_ident())
        return self._real(*a, **k)

    def install(self):
        gc.collect = self

    def restore(self):
        gc.collect = self._real

    def idents(self):
        with self._lock:
            return list(self._calls)


# ------------------------------------------------------------ fake pycaw
class _FakeEndpointVolume:
    def __init__(self):
        self.level = 0.5
        self.mute = False
        self.calls = []

    def GetMasterVolumeLevelScalar(self):
        self.calls.append(("GetMasterVolumeLevelScalar", None))
        return self.level

    def SetMasterVolumeLevelScalar(self, level, _ctx):
        self.calls.append(("SetMasterVolumeLevelScalar", level))
        self.level = level

    def GetMute(self):
        self.calls.append(("GetMute", None))
        return 1 if self.mute else 0

    def SetMute(self, mute, _ctx):
        self.calls.append(("SetMute", mute))
        self.mute = bool(mute)


class _FakeSpeakers:
    def __init__(self, vol):
        self.EndpointVolume = vol


class _FakeSessionVolume:
    def __init__(self):
        self.level = 0.8
        self.mute = False

    def GetMasterVolume(self):
        return self.level

    def SetMasterVolume(self, level, _ctx):
        self.level = level

    def GetMute(self):
        return 1 if self.mute else 0

    def SetMute(self, mute, _ctx):
        self.mute = bool(mute)


class _FakeProcess:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class _FakeSession:
    def __init__(self, proc_name, vol):
        self.Process = _FakeProcess(proc_name) if proc_name else None
        self.SimpleAudioVolume = vol


def _install_fake_com(volume=None, sessions=None):
    volume = volume if volume is not None else _FakeEndpointVolume()
    sessions = sessions if sessions is not None else []

    class _AudioUtilities:
        @staticmethod
        def GetSpeakers():
            return _FakeSpeakers(volume)

        @staticmethod
        def GetAllSessions():
            return list(sessions)

    class _IAudioEndpointVolume:
        _iid_ = "fake-iid"

    pycaw_pkg = _mod("pycaw")
    pycaw_mod = _mod("pycaw.pycaw",
                     AudioUtilities=_AudioUtilities,
                     IAudioEndpointVolume=_IAudioEndpointVolume)
    pycaw_pkg.pycaw = pycaw_mod
    _mod("comtypes", CLSCTX_ALL=0)
    _mod("pythoncom", CoInitialize=lambda: None,
         CoUninitialize=lambda: None)
    return volume


def _core():
    emitted = []
    core = comhost._ComCore(emit=emitted.append)
    core.start()
    return core, emitted


def test_a_volume_state_assembly():
    spot = _FakeSessionVolume()
    vol = _install_fake_com(
        sessions=[_FakeSession("spotify.exe", spot),
                  _FakeSession("other.exe", _FakeSessionVolume())])
    core, _emitted = _core()
    resp = core.handle({"id": 1, "op": "volume.get_state"})
    assert resp["ok"] is True
    state = resp["result"]
    assert state["device_vol"] == 0.5
    assert state["spotify_vol"] == 0.8
    assert state["device_muted"] is False
    assert state["spotify_muted"] is False
    assert "errors" not in state
    assert vol.calls  # the endpoint really was queried


def test_b_volume_set_and_mute_ops():
    spot = _FakeSessionVolume()
    vol = _install_fake_com(sessions=[_FakeSession("spotify.exe", spot)])
    core, _emitted = _core()
    resp = core.handle({"id": 1, "op": "volume.set_device", "level": 0.3})
    assert resp == {"id": 1, "ok": True, "result": {"applied": 0.3}}
    assert vol.level == 0.3
    resp = core.handle({"id": 2, "op": "volume.set_spotify", "level": 0.25})
    assert resp["ok"] is True and spot.level == 0.25
    resp = core.handle({"id": 3, "op": "volume.set_mute",
                        "target": "device", "muted": True})
    assert resp["ok"] is True and vol.mute is True
    resp = core.handle({"id": 4, "op": "volume.set_mute",
                        "target": "spotify", "muted": True})
    assert resp["ok"] is True and spot.mute is True
    # Levels clamp to [0, 1] as they did in the old worker.
    resp = core.handle({"id": 5, "op": "volume.set_device", "level": 1.7})
    assert resp["result"] == {"applied": 1.0} and vol.level == 1.0


def test_c_volume_partial_failure_is_per_part():
    vol = _install_fake_com()

    class _Boom:
        @staticmethod
        def GetAllSessions():
            raise RuntimeError("session enum died")

    sys.modules["pycaw.pycaw"].AudioUtilities = _Boom
    # Device queries go through GetSpeakers on the same class in the
    # old code path too — split the double: keep a working speakers
    # path by restoring GetSpeakers from a fresh fake.
    good = _FakeEndpointVolume()

    class _Mixed:
        @staticmethod
        def GetSpeakers():
            return _FakeSpeakers(good)

        @staticmethod
        def GetAllSessions():
            raise RuntimeError("session enum died")

    sys.modules["pycaw.pycaw"].AudioUtilities = _Mixed
    core, _emitted = _core()
    resp = core.handle({"id": 1, "op": "volume.get_state"})
    assert resp["ok"] is True
    state = resp["result"]
    assert state["device_vol"] == 0.5
    assert state["device_muted"] is False
    assert state["spotify_vol"] is None
    assert state["spotify_muted"] is None
    assert len(state["errors"]) == 2
    del vol


def test_d_ping_and_unknown_op_and_status():
    _install_fake_com()
    core, _emitted = _core()
    assert core.handle({"id": 7, "op": "ping"}) == {
        "id": 7, "ok": True, "result": {"pong": True}}
    bad = core.handle({"id": 8, "op": "nope"})
    assert bad["ok"] is False and "unknown op" in bad["error"]
    st = core.handle({"id": 9, "op": "sapi.status"})
    assert st["ok"] is True
    assert st["result"]["ready"] is False  # no say yet: engine absent
    assert st["result"]["volume_available"] is True


# ------------------------------------------------------------- SAPI side
class _LoopEngine:
    """pyttsx3 double WITH an external loop: say() queues, the test
    fires the finished-utterance callback by hand."""

    def __init__(self):
        self.said = []
        self.stopped = 0
        self._cb = None
        self.props = {"volume": 1.0, "rate": 170}

    def startLoop(self, _hooks):
        pass

    def connect(self, event, cb):
        assert event == "finished-utterance"
        self._cb = cb

    def finish(self):
        self._cb()

    def say(self, text):
        self.said.append(text)

    def stop(self):
        self.stopped += 1

    def iterate(self):
        pass

    def getProperty(self, name):
        return self.props[name]

    def setProperty(self, name, value):
        self.props[name] = value


class _BlockingEngine:
    """pyttsx3 double WITHOUT an external loop (runAndWait path)."""

    def __init__(self):
        self.said = []

    def startLoop(self, _hooks):
        raise RuntimeError("no external loop")

    def say(self, text):
        self.said.append(text)

    def runAndWait(self):
        pass

    def stop(self):
        pass

    def getProperty(self, name):
        return 1.0 if name == "volume" else 170

    def setProperty(self, name, value):
        pass


class _InitTracker:
    """init_pyttsx3 replacement counting calls and snapshotting how
    many collections the GC recorder had already seen when init ran
    (the post-init flush must not have happened yet)."""

    def __init__(self, engine, rec=None):
        self.engine = engine
        self.rec = rec
        self.calls = 0
        self.collects_before = []

    def __call__(self, on_log=None):
        self.calls += 1
        if self.rec is not None:
            self.collects_before.append(
                self.rec.idents().count(threading.get_ident()))
        return self.engine


def _install_engine(engine, rec=None):
    tracker = _InitTracker(engine, rec)
    _mod("pyttsx3", init=lambda: None)  # importable; init unused here
    real = comhost.init_pyttsx3
    comhost.init_pyttsx3 = tracker
    return tracker, real


def test_e_sapi_engine_inits_lazily_on_first_say():
    """Moved from test_com_hotfix.py test_e (speech._SapiVoice era):
    core start and stop must never init; the first say inits exactly
    once, immediately followed by an in-apartment gc.collect(); the
    second say reuses the engine. Completion arrives as the async
    sapi.finished event for the say's id."""
    _install_fake_com()
    engine = _LoopEngine()
    rec = _GcRecorder()
    tracker, real = _install_engine(engine, rec)
    core, emitted = _core()  # start()'s own sweep predates the recorder
    rec.install()
    try:
        assert tracker.calls == 0  # start() did not init
        ack = core.handle({"id": 1, "op": "sapi.stop"})
        assert ack["ok"] is True
        assert tracker.calls == 0  # stop with no engine: still no init
        assert emitted == []       # and nothing was "finished"

        ack = core.handle({"id": 2, "op": "sapi.say",
                           "text": "first words", "whisper": False})
        assert ack == {"id": 2, "ok": True, "result": {"queued": True}}
        assert tracker.calls == 1
        # No owner-thread collection had run when init was called;
        # the post-init flush has run by the time say returns.
        assert tracker.collects_before == [0]
        assert rec.idents().count(threading.get_ident()) >= 1
        assert engine.said == ["first words"]
        assert emitted == []  # not finished until the engine says so
        engine.finish()
        assert emitted == [{"event": "sapi.finished", "id": 2, "ok": True}]

        ack = core.handle({"id": 3, "op": "sapi.say",
                           "text": "second words", "whisper": False})
        assert ack["ok"] is True
        assert tracker.calls == 1  # reused, no re-init
        engine.finish()
        assert emitted[-1] == {"event": "sapi.finished", "id": 3,
                               "ok": True}
    finally:
        rec.restore()
        comhost.init_pyttsx3 = real


def test_f_sapi_lazy_init_failure_leaves_engine_absent():
    """Moved from test_com_hotfix.py test_f: init returning None ->
    the say completes immediately as finished/ok False, and a later
    stop is a harmless no-op; the next say retries init."""
    _install_fake_com()
    tracker, real = _install_engine(None)
    core, emitted = _core()
    try:
        ack = core.handle({"id": 1, "op": "sapi.say",
                           "text": "nobody home", "whisper": False})
        assert ack["ok"] is True
        assert tracker.calls == 1
        assert len(emitted) == 1
        fin = emitted[0]
        assert fin["event"] == "sapi.finished" and fin["id"] == 1
        assert fin["ok"] is False
        ack = core.handle({"id": 2, "op": "sapi.stop"})
        assert ack["ok"] is True
        core.handle({"id": 3, "op": "sapi.say",
                     "text": "retry", "whisper": False})
        assert tracker.calls == 2  # retried, per the round-3 contract
    finally:
        comhost.init_pyttsx3 = real


def test_g_sapi_runandwait_path_and_whisper_restore():
    _install_fake_com()
    engine = _BlockingEngine()
    tracker, real = _install_engine(engine)
    core, emitted = _core()
    try:
        ack = core.handle({"id": 1, "op": "sapi.say",
                           "text": "loud", "whisper": False})
        assert ack["ok"] is True
        assert engine.said == ["loud"]
        assert emitted == [{"event": "sapi.finished", "id": 1, "ok": True}]
        ack = core.handle({"id": 2, "op": "sapi.say",
                           "text": "quiet", "whisper": True})
        assert ack["ok"] is True
        assert engine.said == ["loud", "quiet"]
        assert emitted[-1] == {"event": "sapi.finished", "id": 2,
                               "ok": True}
        assert tracker.calls == 1
    finally:
        comhost.init_pyttsx3 = real


def test_h_core_regime_collects_then_disables_gc():
    """Inside the helper the round-4 regime is sound (one apartment):
    start() sweeps once and disables automatic collection; shutdown
    collects once more before CoUninitialize. The test restores the
    process-wide GC state it disturbs."""
    _install_fake_com()
    was_enabled = gc.isenabled()
    rec = _GcRecorder()
    rec.install()
    core, _emitted = _core()
    try:
        assert rec.idents(), "start() must collect once"
        assert not gc.isenabled(), "start() must disable automatic GC"
    finally:
        core.shutdown()
        rec.restore()
        if was_enabled:
            gc.enable()
    assert rec.idents(), "shutdown() must collect on the COM thread"
