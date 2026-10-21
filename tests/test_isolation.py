"""COM process-isolation tests (crash round 5).

The invariant these tests guard is PHYSICAL, not conventional:
the main process imports no COM library anywhere except
aria/comhost.py (the helper process's own module). They pin:

  * the static AST guard — no import of comtypes / pycaw / pyttsx3
    / pywinauto / win32com / pythoncom under aria/ or in aria.py,
    outside comhost.py;
  * the structural poison test replacing the old in-process one:
    importing aria.hud and aria.speech leaves every banned module
    out of sys.modules;
  * tools.builtins.tool_volume against a fake hud (moved verbatim
    from the old test_com_hotfix.py tests a/b — the tool still
    routes through the VolumeManager's plain cached state and
    never imports COM itself);
  * the hud.VolumeManager FACADE against a fake comclient: attrs
    and the poll, optimistic setters that forward ops, last-known
    state held while the host is down, is_started() reflecting
    host liveness;
  * the speech._SapiVoice FACADE against a fake comclient: say()
    returns an Event set by the host's sapi.finished event (with
    the failure logged on ok False), set immediately when the host
    is down, and released for all pending says when the host dies.

The old test_com_hotfix.py tests c/d (in-process worker/owner
thread GC discipline) are GONE with the in-process COM they
guarded; their SAPI lazy-init tests e/f moved to
tests/test_comhost.py, where the engine now lives.

Suite conventions: heavy modules are stubbed in sys.modules at
import; each test installs the exact module surface it needs.
"""
import ast
import importlib
import os
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
PKG = os.path.join(ROOT, "aria")

BANNED = {"comtypes", "pycaw", "pyttsx3", "pywinauto", "win32com",
          "pythoncom"}
ALLOWED_FILES = {os.path.join(PKG, "comhost.py")}


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools = types.ModuleType("aria.tools")
_tools.__path__ = [os.path.join(PKG, "tools")]
sys.modules["aria.tools"] = _tools
_agent = types.ModuleType("aria.agent")
_agent.__path__ = [os.path.join(PKG, "agent")]
sys.modules["aria.agent"] = _agent
_surf = types.ModuleType("aria.agent.failures")
_surf.surface_failure = lambda *a, **k: None
sys.modules["aria.agent.failures"] = _surf

import types as _t
_mod("psutil",
     cpu_percent=lambda interval=0: 0.0,
     virtual_memory=lambda: _t.SimpleNamespace(percent=0.0),
     disk_usage=lambda p: _t.SimpleNamespace(percent=0.0),
     sensors_battery=lambda: None,
     net_io_counters=lambda: _t.SimpleNamespace(bytes_sent=0,
                                                bytes_recv=0),
     boot_time=lambda: time.time() - 3600)

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")


def _wait_for(pred, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


# --------------------------------------------------------- static guard
def _imported_roots(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_a_static_no_com_imports_outside_comhost():
    offenders = []
    for dirpath, _dirs, files in os.walk(PKG):
        if "__pycache__" in dirpath:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            if os.path.abspath(path) in {
                    os.path.abspath(p) for p in ALLOWED_FILES}:
                continue
            bad = _imported_roots(path) & BANNED
            if bad:
                offenders.append((path, sorted(bad)))
    launcher = os.path.join(ROOT, "aria.py")
    bad = _imported_roots(launcher) & BANNED
    if bad:
        offenders.append((launcher, sorted(bad)))
    assert offenders == [], offenders
    # And the one allowed file really is the COM consumer.
    assert _imported_roots(os.path.join(PKG, "comhost.py")) or True
    with open(os.path.join(PKG, "comhost.py"), encoding="utf-8") as fh:
        host_src = fh.read()
    assert "import pyttsx3" in host_src and "pycaw" in host_src


def test_b_importing_hud_and_speech_pulls_no_com():
    _mod("cv2")
    _mod("aria.pixel_avatar",
         draw_pixel_aria=lambda *a, **k: None,
         load_theme=lambda: {})
    _mod("aria.vision", publish_face_frame=lambda *a, **k: None)
    _mod("speech_recognition")
    for name in sorted(BANNED):
        sys.modules.pop(name, None)
    sys.modules.pop("aria.hud", None)
    sys.modules.pop("aria.speech", None)
    importlib.import_module("aria.hud")
    importlib.import_module("aria.speech")
    leaked = sorted(m for m in BANNED if m in sys.modules)
    assert leaked == [], leaked


# ------------------------------------------- tool_volume (moved a/b)
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


class _ComImportPoison:
    """Import hook that records (and fails) any pycaw/comtypes import."""

    def __init__(self):
        self.attempts = []

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("pycaw", "comtypes"):
            self.attempts.append(name)
            raise AssertionError(f"COM import attempted: {name}")
        return None


def test_c_tool_volume_routes_without_com():
    _install_fake_hud()
    sys.modules.pop("aria.tools.builtins", None)
    builtins = importlib.import_module("aria.tools.builtins")
    state = _install_fake_hud(started=True, level=0.40)
    poison = _ComImportPoison()
    sys.meta_path.insert(0, poison)
    try:
        assert builtins.tool_volume("status") == "Volume is at 40%."
        assert builtins.tool_volume("set", 65) == "Volume set to 65%."
        assert state.level == 0.65 and state.sets == [0.65]
        assert builtins.tool_volume("status") == "Volume is at 65%."
        out = builtins.tool_volume("up")
        assert abs(state.level - 0.75) < 1e-9
        assert out.startswith("Volume is at ")
        out = builtins.tool_volume("down")
        assert abs(state.level - 0.65) < 1e-9
        assert out.startswith("Volume is at ")
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


def test_d_tool_volume_unavailable_when_manager_down():
    _install_fake_hud()
    sys.modules.pop("aria.tools.builtins", None)
    builtins = importlib.import_module("aria.tools.builtins")
    state = _install_fake_hud(started=False, level=0.40)
    expected = ("[Volume control unavailable: the volume manager "
                "is not running.]")
    assert builtins.tool_volume("status") == expected
    assert builtins.tool_volume("set", 30) == expected
    assert builtins.tool_volume("mute") == expected
    assert builtins.tool_volume("up") == expected
    assert state.sets == [] and state.mutes == []


# ---------------------------------------------------------- fake client
class _FakeClient:
    """In-process comclient double: programmable state, send log,
    up/down flag, listener registries matching the real client."""

    def __init__(self, state=None, up=True):
        self.state = state if state is not None else {
            "device_vol": 0.42, "spotify_vol": 0.9,
            "device_muted": False, "spotify_muted": True}
        self.up = up
        self.sent = []
        self._next = 100
        self._event_cbs = []
        self._down_cbs = []
        self._lock = threading.Lock()

    def is_up(self):
        return self.up

    def add_event_listener(self, cb):
        if cb not in self._event_cbs:
            self._event_cbs.append(cb)

    def add_down_listener(self, cb):
        if cb not in self._down_cbs:
            self._down_cbs.append(cb)

    def send(self, op, payload=None):
        with self._lock:
            if not self.up:
                from aria.comclient import ComHostDown
                raise ComHostDown("fake host is down")
            self._next += 1
            rid = self._next
            self.sent.append((op, dict(payload or {}), rid))
            return rid

    def request(self, op, payload=None, timeout=10.0):
        if not self.up:
            from aria.comclient import ComHostDown
            raise ComHostDown("fake host is down")
        if op == "volume.get_state":
            return dict(self.state)
        raise AssertionError(f"unexpected request op: {op}")

    def emit_finished(self, rid, ok=True, error=None):
        msg = {"event": "sapi.finished", "id": rid, "ok": ok}
        if error:
            msg["error"] = error
        for cb in list(self._event_cbs):
            cb(msg)

    def die(self):
        self.up = False
        for cb in list(self._down_cbs):
            cb()


def _real_hud():
    _mod("cv2")
    _mod("aria.pixel_avatar",
         draw_pixel_aria=lambda *a, **k: None,
         load_theme=lambda: {})
    _mod("aria.vision", publish_face_frame=lambda *a, **k: None)
    sys.modules.pop("aria.hud", None)
    return importlib.import_module("aria.hud")


def _real_speech():
    _mod("speech_recognition")
    sys.modules.pop("aria.speech", None)
    return importlib.import_module("aria.speech")


# --------------------------------------------------- VolumeManager facade
def test_e_volume_manager_facade_poll_and_setters():
    hud = _real_hud()
    fake = _FakeClient()
    real_getter = hud._get_volume_client
    hud._get_volume_client = lambda: fake
    vm = hud.VolumeManager()
    vm._POLL_INTERVAL_S = 0.05
    try:
        assert vm.is_started() is False  # not started yet
        vm.start()
        assert _wait_for(lambda: vm.device_vol == 0.42)
        assert vm.spotify_vol == 0.9
        assert vm.device_muted is False and vm.spotify_muted is True
        assert vm.is_started() is True   # started AND host up

        vm.set_level(0.3)
        assert vm.get_level() == 0.3     # optimistic cache flip
        assert _wait_for(lambda: any(
            op == "volume.set_device" and p.get("level") == 0.3
            for op, p, _r in fake.sent))
        vm.toggle_target()
        vm.set_level(0.7)
        assert _wait_for(lambda: any(
            op == "volume.set_spotify" and p.get("level") == 0.7
            for op, p, _r in fake.sent))
        vm.set_mute(True)
        assert vm.get_muted() is True
        assert _wait_for(lambda: any(
            op == "volume.set_mute"
            and p == {"target": "spotify", "muted": True}
            for op, p, _r in fake.sent))
        vm.toggle_target()
        # The poll re-reads the (fake) hardware state and overwrites
        # the optimistic flip — eventual consistency, as with the
        # old worker's desired-state queue.
        assert _wait_for(lambda: vm.get_level() == 0.42)
    finally:
        vm._running = False
        hud._get_volume_client = real_getter


def test_f_volume_manager_holds_state_when_host_down():
    hud = _real_hud()
    fake = _FakeClient()
    real_getter = hud._get_volume_client
    hud._get_volume_client = lambda: fake
    vm = hud.VolumeManager()
    vm._POLL_INTERVAL_S = 0.05
    try:
        vm.start()
        assert _wait_for(lambda: vm.device_vol == 0.42)
        assert vm.is_started() is True
        fake.die()
        assert _wait_for(lambda: vm.is_started() is False)
        # Poll failures are swallowed; last-known state is served.
        time.sleep(0.2)
        assert vm.device_vol == 0.42
        # Setters still flip the cache, forward into the void, and
        # never raise.
        vm.set_level(0.6)
        assert vm.get_level() == 0.6
        vm.set_mute(True)
        assert vm.get_muted() is True
    finally:
        vm._running = False
        hud._get_volume_client = real_getter


# ------------------------------------------------------- _SapiVoice facade
def test_g_sapi_voice_facade_event_flow():
    speech = _real_speech()
    fake = _FakeClient()
    real_getter = speech._get_sapi_client
    speech._get_sapi_client = lambda: fake
    logs = []
    voice = speech._SapiVoice(on_log=logs.append)
    try:
        done = voice.say("hello isolation")
        assert not done.is_set()
        assert len(fake.sent) == 1
        op, payload, rid = fake.sent[0]
        assert op == "sapi.say"
        assert payload == {"text": "hello isolation", "whisper": False}
        fake.emit_finished(rid, ok=True)
        assert done.wait(2)

        done2 = voice.say("quiet words", whisper_mode=True)
        _op, payload2, rid2 = fake.sent[-1]
        assert payload2 == {"text": "quiet words", "whisper": True}
        fake.emit_finished(rid2, ok=False, error="engine exploded")
        assert done2.wait(2)
        assert any("engine exploded" in line for line in logs)

        voice.stop()
        assert fake.sent[-1][0] == "sapi.stop"
    finally:
        speech._get_sapi_client = real_getter


def test_h_sapi_voice_down_sets_event_immediately():
    speech = _real_speech()
    fake = _FakeClient(up=False)
    real_getter = speech._get_sapi_client
    speech._get_sapi_client = lambda: fake
    logs = []
    voice = speech._SapiVoice(on_log=logs.append)
    try:
        done = voice.say("nobody home")
        assert done.wait(2)
        assert any("unavailable" in line for line in logs)
    finally:
        speech._get_sapi_client = real_getter


def test_i_sapi_voice_host_death_releases_pending():
    speech = _real_speech()
    fake = _FakeClient()
    real_getter = speech._get_sapi_client
    speech._get_sapi_client = lambda: fake
    logs = []
    voice = speech._SapiVoice(on_log=logs.append)
    try:
        done = voice.say("will be dropped")
        assert not done.is_set()
        fake.die()
        assert done.wait(2)
        assert any("dropped" in line for line in logs)
    finally:
        speech._get_sapi_client = real_getter
