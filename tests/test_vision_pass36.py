"""Pass 36 vision fixes: #08 pre-pass routes through the shared service,
#09 native vision rejects placeholder keys / gates the probe,
#38 vision failures stay typed FAILED through normalize().

Follows the test_vision_look.py / test_headless.py stub pattern (cv2
absent on this box); imports the REAL aria.vision and aria.agent.brain.

Run:  python3 -m pytest tests/test_vision_pass36.py -q
"""
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
tools_pkg = types.ModuleType("aria.tools")
tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = tools_pkg
agent_pkg = types.ModuleType("aria.agent")
agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = agent_pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules["psutil"] = _ps
sys.modules["cv2"] = types.ModuleType("cv2")
_hw = types.ModuleType("aria.hardware")
_hw.send_servo_command = lambda *a, **k: None
_hw.SERVO_POS = {"pan": 90, "tilt": 45}
sys.modules["aria.hardware"] = _hw

# Metering stub: the native vision path wraps its HTTP call in
# usage.attempt(); keep the tests offline and side-effect free.
_usage = types.ModuleType("aria.usage")


class _Attempt:
    def __init__(self, *a, **k):
        self.measured = {}

    def __enter__(self):
        return self.measured

    def __exit__(self, *a):
        return False


_usage.attempt = lambda *a, **k: _Attempt()
_usage.max_output_tokens = lambda: 512
sys.modules["aria.usage"] = _usage

import importlib  # noqa: E402

config = importlib.import_module("aria.config")
vision = importlib.import_module("aria.vision")
outcome = importlib.import_module("aria.outcome")
normalize = outcome.normalize
_sp = types.ModuleType("aria.speech")
_sp.edge_tts_bytes = lambda text: b""
_sp.speak = lambda *a, **k: None
_sp._SPEECH_QUEUE = types.SimpleNamespace(unfinished_tasks=0)
_sp._AUDIO_PLAY_QUEUE = types.SimpleNamespace(unfinished_tasks=0)
sys.modules["aria.speech"] = _sp
_hud = types.ModuleType("aria.hud")
_hud.set_hud_state = lambda *a, **k: None
_hud.draw_hud = lambda *a, **k: None
sys.modules["aria.hud"] = _hud
brain = importlib.import_module("aria.agent.brain")

FAKE_JPEG = b"\xff\xd8" + b"x" * 200


# ── Pass 36 #08 ──────────────────────────────────────────────────────

def test_describe_image_success_uses_shared_service(monkeypatch):
    """describe_image() routes through _VISION_TEXT_CALL, not native."""
    seen = {}

    def fake_service(sys_prompt, contents):
        seen["called"] = True
        return "A red bicycle leaning on a fence."

    monkeypatch.setattr(vision, "_VISION_TEXT_CALL", fake_service)
    monkeypatch.setattr(
        vision, "_ollama_native_vision_call",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("native must not be called")))
    result = vision.describe_image(FAKE_JPEG, "what is this?")
    assert seen.get("called") is True
    assert str(result) == "A red bicycle leaning on a fence."
    assert getattr(result, "status", None) == "done"
    assert normalize(result).status == "done"


def test_describe_image_failure_strings_are_typed(monkeypatch):
    for failure in (
        "[Vision unavailable: local-only mode is on and the local vision model did not respond.]",
        "[Vision unavailable: ollama native: HTTP 401: bad key]",
        "[Vision returned no description.]",
        "[Vision unavailable: no vision text caller configured.]",
    ):
        monkeypatch.setattr(vision, "_VISION_TEXT_CALL", lambda sp, c, _f=failure: _f)
        result = vision.describe_image(FAKE_JPEG, "what is this?")
        assert getattr(result, "status", None) == "failed", failure
        assert str(result) == failure
        assert normalize(result).status == "failed"


def test_describe_image_no_caller_or_image_is_failed(monkeypatch):
    monkeypatch.setattr(vision, "_VISION_TEXT_CALL", None)
    assert getattr(vision.describe_image(FAKE_JPEG), "status", None) == "failed"
    monkeypatch.setattr(vision, "_VISION_TEXT_CALL", lambda sp, c: "x")
    assert getattr(vision.describe_image(b""), "status", None) == "failed"
    assert getattr(vision.describe_image(None), "status", None) == "failed"


def test_release_screen_hash_lets_retry_recapture(monkeypatch):
    monkeypatch.setattr(vision, "capture_screen", lambda: b"fake-screen-bytes")
    vision._LAST_SCREEN_HASH = None
    assert vision.capture_screen_if_changed() == b"fake-screen-bytes"
    assert vision.capture_screen_if_changed() is None  # unchanged: nothing
    vision.release_screen_hash()
    assert vision._LAST_SCREEN_HASH is None
    assert vision.capture_screen_if_changed() == b"fake-screen-bytes"


def test_vision_prepass_uses_shared_service_not_native(monkeypatch):
    monkeypatch.setattr(
        vision, "_VISION_TEXT_CALL",
        lambda sp, c: "A desk with a coffee mug.")
    monkeypatch.setattr(
        vision, "_ollama_native_vision_call",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("native must not be called")))
    out = brain._vision_prepass("what is on my screen?", FAKE_JPEG, True)
    assert "A desk with a coffee mug." in out
    assert "what is on my screen?" in out


def test_vision_prepass_screen_failure_releases_hash(monkeypatch):
    monkeypatch.setattr(
        vision, "_VISION_TEXT_CALL",
        lambda sp, c: "[Vision unavailable: local-only mode is on and the local vision model did not respond.]")
    vision._LAST_SCREEN_HASH = "sentinel-hash"
    out = brain._vision_prepass("what is on my screen?", FAKE_JPEG, True)
    assert "could not be processed" in out
    assert vision._LAST_SCREEN_HASH is None


def test_vision_prepass_camera_failure_keeps_hash(monkeypatch):
    """Camera has no hash; a failed camera pre-pass must not touch it."""
    monkeypatch.setattr(
        vision, "_VISION_TEXT_CALL",
        lambda sp, c: "[Vision returned no description.]")
    vision._LAST_SCREEN_HASH = "sentinel-hash"
    out = brain._vision_prepass("what do you see?", FAKE_JPEG, False)
    assert "could not be processed" in out
    assert vision._LAST_SCREEN_HASH == "sentinel-hash"
