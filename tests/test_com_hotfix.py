"""COM-containment hotfix tests (2026-10-21 crash).

Evidence: the faulthandler dump from the user's laptop showed the
MAIN thread dying in comtypes Release() during a garbage collection
(main.py _render_hud_frame -> ... -> memory.job_db_list ->
"Garbage-collecting" -> comtypes __del__ -> Release -> access
violation). A COM wrapper created on a foreign thread and trapped in
a reference cycle was finalised by the cyclic GC on the main thread.

These tests pin the three repairs:
  * tools.builtins.tool_volume never imports pycaw/comtypes and routes
    every action through the VolumeManager's plain state (fake manager
    here), with the original result strings;
  * the real VolumeManager worker applies level+mute using COM only on
    its own thread (fake pycaw), and gc.collect() runs on that thread —
    periodically and once more at teardown;
  * the _SapiVoice owner thread runs gc.collect() on its own thread
    (fake pyttsx3 engine).

Suite conventions: heavy modules are stubbed in sys.modules at import;
each test installs the exact module surface it needs.
"""
import gc
import importlib
import os
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_pkg = types.ModuleType("aria")
_pkg.__path__ = [os.path.join(HERE, "..", "aria")]
sys.modules["aria"] = _pkg
_tools = types.ModuleType("aria.tools")
_tools.__path__ = [os.path.join(HERE, "..", "aria", "tools")]
sys.modules["aria.tools"] = _tools
_agent = types.ModuleType("aria.agent")
_agent.__path__ = [os.path.join(HERE, "..", "aria", "agent")]
sys.modules["aria.agent"] = _agent

import types as _t
_mod("psutil",
     cpu_percent=lambda interval=0: 0.0,
     virtual_memory=lambda: _t.SimpleNamespace(percent=0.0),
     disk_usage=lambda p: _t.SimpleNamespace(percent=0.0),
     sensors_battery=lambda: None,
     net_io_counters=lambda: _t.SimpleNamespace(bytes_sent=0, bytes_recv=0),
     boot_time=lambda: time.time() - 3600)

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")


# ---------------------------------------------------------------- fake hud
class _FakeHudState:
    def __init__(self, started, level):
        self.started = started
        self.level = level
        self.muted = False
        self.sets = []
        self.mutes = []


def _install_fake_hud(started=True, level=0.40):
    state = _FakeHudState(started, level)
    m = types.ModuleType("aria.hud")
    m.get_hud_volume = lambda: state.level

    def _set(v):
        state.level = v
        state.sets.append(v)
    m.set_hud_volume = _set

    def _sm(muted):
        state.muted = muted
        state.mutes.append(muted)
    m.set_hud_mute = _sm
    m.volume_manager_started = lambda: state.started
    sys.modules["aria.hud"] = m
    return state


_install_fake_hud()
builtins = importlib.import_module("aria.tools.builtins")


class _ComImportPoison:
    """Import hook that records (and fails) any pycaw/comtypes import."""

    def __init__(self):
        self.attempts = []

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("pycaw", "comtypes"):
            self.attempts.append(name)
            raise AssertionError(f"COM import attempted: {name}")
        return None


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


def _wait_for(pred, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


# ------------------------------------------------------------------- tests
def test_a_tool_volume_routes_without_com():
    state = _install_fake_hud(started=True, level=0.40)
    poison = _ComImportPoison()
    sys.meta_path.insert(0, poison)
    try:
        assert builtins.tool_volume("status") == "Volume is at 40%."
        assert builtins.tool_volume("set", 65) == "Volume set to 65%."
        assert state.level == 0.65 and state.sets == [0.65]
        assert builtins.tool_volume("status") == "Volume is at 65%."
        out = builtins.tool_volume("up")
        assert abs(state.level - 0.75) < 1e-9 and out.startswith("Volume is at ")
        out = builtins.tool_volume("down")
        assert abs(state.level - 0.65) < 1e-9 and out.startswith("Volume is at ")
        assert builtins.tool_volume("set", 150) == "Volume set to 100%."
        assert state.level == 1.0
        assert builtins.tool_volume("mute") == "Muted."
        assert state.muted is True and state.mutes == [True]
        assert builtins.tool_volume("unmute") == "Unmuted."
        assert state.muted is False and state.mutes == [True, False]
        # Unknown actions fall through to a status report (old contract).
        assert builtins.tool_volume("bogus") == "Volume is at 100%."
    finally:
        sys.meta_path.remove(poison)
    assert poison.attempts == [], poison.attempts


def test_b_tool_volume_unavailable_when_manager_down():
    state = _install_fake_hud(started=False, level=0.40)
    expected = ("[Volume control unavailable: the volume manager "
                "is not running.]")
    assert builtins.tool_volume("status") == expected
    assert builtins.tool_volume("set", 30) == expected
    assert builtins.tool_volume("mute") == expected
    assert builtins.tool_volume("up") == expected
    assert state.sets == [] and state.mutes == []


# ------------------------------------------------------- fake COM surface
class _FakeEndpointVolume:
    def __init__(self):
        self.level = 0.5
        self.mute = False
        self.calls = []  # (method, value, thread ident)
        self._lock = threading.Lock()

    def _rec(self, method, value=None):
        with self._lock:
            self.calls.append((method, value, threading.get_ident()))

    def snapshot(self):
        with self._lock:
            return list(self.calls)

    def GetMasterVolumeLevelScalar(self):
        self._rec("GetMasterVolumeLevelScalar")
        return self.level

    def SetMasterVolumeLevelScalar(self, level, _ctx):
        self._rec("SetMasterVolumeLevelScalar", level)
        self.level = level

    def GetMute(self):
        self._rec("GetMute")
        return 1 if self.mute else 0

    def SetMute(self, mute, _ctx):
        self._rec("SetMute", mute)
        self.mute = bool(mute)


class _FakeSpeakers:
    def __init__(self, vol):
        self.EndpointVolume = vol


def test_c_volume_manager_worker_com_and_gc_on_own_thread():
    fake_vol = _FakeEndpointVolume()

    class _AudioUtilities:
        @staticmethod
        def GetSpeakers():
            return _FakeSpeakers(fake_vol)

        @staticmethod
        def GetAllSessions():
            return []

    class _IAudioEndpointVolume:
        _iid_ = "fake-iid"

    _mod("cv2")
    _mod("aria.pixel_avatar",
         draw_pixel_aria=lambda *a, **k: None,
         load_theme=lambda: {})
    _mod("aria.vision", publish_face_frame=lambda *a, **k: None)
    pycaw_pkg = _mod("pycaw")
    pycaw_mod = _mod("pycaw.pycaw",
                     AudioUtilities=_AudioUtilities,
                     IAudioEndpointVolume=_IAudioEndpointVolume)
    pycaw_pkg.pycaw = pycaw_mod
    _mod("pythoncom", CoInitialize=lambda: None, CoUninitialize=lambda: None)
    _mod("comtypes", CLSCTX_ALL=0)

    sys.modules.pop("aria.hud", None)
    hud = importlib.import_module("aria.hud")

    main_ident = threading.get_ident()
    rec = _GcRecorder()
    old_interval = hud._VOLUME_GC_EVERY_ITERS
    hud._VOLUME_GC_EVERY_ITERS = 2
    rec.install()
    vm = hud.VolumeManager()
    try:
        vm.start()
        assert vm.is_started()
        # is_alive() implies start() ran, so .ident is populated (the
        # monitor assigns _thread a beat before it starts it).
        assert _wait_for(
            lambda: vm._thread is not None and vm._thread.is_alive(), 5)
        worker_ident = vm._thread.ident
        assert worker_ident is not None and worker_ident != main_ident

        vm.set_level(0.3)
        vm.set_mute(True)
        assert _wait_for(lambda: any(
            c[0] == "SetMasterVolumeLevelScalar" and c[1] == 0.3
            for c in fake_vol.snapshot()))
        assert _wait_for(lambda: any(
            c[0] == "SetMute" and c[1] == 1 for c in fake_vol.snapshot()))
        # Every COM call the fake endpoint saw happened on the worker.
        idents = {c[2] for c in fake_vol.snapshot()}
        assert idents == {worker_ident}, fake_vol.snapshot()
        # Cached state is readable without COM.
        assert vm.get_level() == 0.3 and vm.get_muted() is True

        # Periodic owner-thread collection.
        assert _wait_for(lambda: worker_ident in rec.idents())

        # Teardown: stopping the worker runs one more collection on
        # the worker thread itself (its finally block).
        before = rec.idents().count(worker_ident)
        vm._running = False
        vm._event.set()
        vm._thread.join(timeout=5)
        assert not vm._thread.is_alive()
        assert rec.idents().count(worker_ident) > before
    finally:
        vm._running = False
        vm._event.set()
        rec.restore()
        hud._VOLUME_GC_EVERY_ITERS = old_interval


# ------------------------------------------------------------- SAPI thread
class _FakeEngine:
    def __init__(self):
        self.said = []
        self.idents = []
        self._lock = threading.Lock()

    def _rec(self):
        with self._lock:
            self.idents.append(threading.get_ident())

    def startLoop(self, _hooks):
        raise RuntimeError("test double has no external loop")

    def say(self, text):
        self._rec()
        with self._lock:
            self.said.append(text)

    def runAndWait(self):
        self._rec()

    def stop(self):
        pass

    def getProperty(self, name):
        return 1.0 if name == "volume" else 170

    def setProperty(self, name, value):
        pass


def test_d_sapi_owner_thread_gc_on_own_thread():
    _mod("speech_recognition")
    speech = importlib.import_module("aria.speech")

    engine = _FakeEngine()
    real_init = speech.init_pyttsx3
    speech.init_pyttsx3 = lambda on_log=None: engine
    rec = _GcRecorder()
    rec.install()
    voice = speech._SapiVoice()
    voice._gc_every_iters = 3
    try:
        voice.start()
        done = voice.say("hello hotfix")
        assert done.wait(5)
        assert engine.said == ["hello hotfix"]
        engine_ident = voice._thread.ident
        assert engine.idents and all(i == engine_ident for i in engine.idents)
        # Periodic collection happens on the engine (owner) thread.
        assert _wait_for(lambda: engine_ident in rec.idents(), 5)
    finally:
        rec.restore()
        speech.init_pyttsx3 = real_init
