"""Unit tests for the vision first-look path — Merger Phase 6
(blueprint SS2.4 #4 pattern, SS5 Phase 6): aria/vision.py's
vision_first_look + tool_vision_look over an injected fake orpool.
The existing describe_camera / read_screen chain is asserted
untouched (its _VISION_TEXT_CALL hook is never invoked by the
first-look path).

Follows the test_ultimate_e.py stub pattern (cv2 absent on this box).

Run:  python3 tests/_run_suite.py tests/test_vision_look.py
"""
import base64
import os
import sys
import tempfile
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
sys.modules["aria.agent.brain"] = types.ModuleType("aria.agent.brain")

import importlib

config = importlib.import_module("aria.config")
vision = importlib.import_module("aria.vision")

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")


class FakePool:
    def __init__(self, text="A red bicycle.", available=True, raises=None):
        self._text = text
        self._available = available
        self._raises = raises
        self.calls = []

    def available(self):
        return self._available

    def vision(self, prompt, image_b64, mime="image/png", system=None,
               model=None, max_tokens=1024):
        self.calls.append({"prompt": prompt, "b64": image_b64,
                           "mime": mime, "system": system})
        if self._raises is not None:
            raise self._raises
        return self._text


def test_first_look_success():
    pool = FakePool()
    out = vision.vision_first_look(PNG_1X1, "what is this?", pool=pool)
    assert out == "A red bicycle.", out
    call = pool.calls[0]
    assert call["prompt"] == "what is this?"
    assert base64.b64decode(call["b64"]) == PNG_1X1
    assert call["mime"] == "image/jpeg"  # default
    assert "ARIA" in (call["system"] or "")
    print("ok first_look_success")


def test_first_look_default_question():
    pool = FakePool()
    vision.vision_first_look(PNG_1X1, pool=pool)
    assert "Describe what you see" in pool.calls[0]["prompt"]
    print("ok first_look_default_question")


def test_first_look_pool_unavailable():
    out = vision.vision_first_look(PNG_1X1, pool=FakePool(available=False))
    assert out.startswith("[Vision first-look unavailable:"), out
    assert "pool is not available" in out, out
    print("ok first_look_pool_unavailable")


def test_first_look_pool_raises():
    pool = FakePool(raises=RuntimeError("pool boom"))
    out = vision.vision_first_look(PNG_1X1, pool=pool)
    assert out == "[Vision first-look unavailable: pool boom]", out
    print("ok first_look_pool_raises")


def test_first_look_empty_answer():
    out = vision.vision_first_look(PNG_1X1, pool=FakePool(text="  "))
    assert out == "[Vision first-look returned no description.]", out
    print("ok first_look_empty_answer")


def test_first_look_no_image():
    out = vision.vision_first_look(b"", pool=FakePool())
    assert out == "[Vision first-look unavailable: no image was provided.]", out
    print("ok first_look_no_image")


def test_first_look_flag_off():
    old = config.VISION_FASTPATH_ENABLED
    config.VISION_FASTPATH_ENABLED = False
    try:
        out = vision.vision_first_look(PNG_1X1, pool=FakePool())
    finally:
        config.VISION_FASTPATH_ENABLED = old
    assert out.startswith("[Vision first-look is disabled"), out
    print("ok first_look_flag_off")


def test_tool_vision_look_with_file():
    fd, path = tempfile.mkstemp(suffix=".png")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(PNG_1X1)
        pool = FakePool(text="A one-pixel image.")
        out = vision.tool_vision_look("describe it", path, pool=pool)
        assert out == "A one-pixel image.", out
        assert pool.calls[0]["mime"] == "image/png", pool.calls[0]
    finally:
        os.unlink(path)
    print("ok tool_vision_look_with_file")


def test_tool_vision_look_missing_file():
    out = vision.tool_vision_look("q", "/no/such/image.png", pool=FakePool())
    assert out.startswith("[Image not found:"), out
    print("ok tool_vision_look_missing_file")


def test_tool_vision_look_camera_fallback():
    old = vision.capture_webcam
    vision.capture_webcam = lambda: PNG_1X1
    try:
        pool = FakePool(text="The webcam view.")
        out = vision.tool_vision_look("what do you see?", pool=pool)
        assert out == "The webcam view.", out
    finally:
        vision.capture_webcam = old
    print("ok tool_vision_look_camera_fallback")


def test_tool_vision_look_camera_unavailable():
    old = vision.capture_webcam
    vision.capture_webcam = lambda: None
    try:
        out = vision.tool_vision_look("q", pool=FakePool())
        assert out == "[Camera not available right now.]", out
    finally:
        vision.capture_webcam = old
    print("ok tool_vision_look_camera_unavailable")


def test_first_look_leaves_existing_chain_untouched():
    sentinel_calls = []
    old = vision._VISION_TEXT_CALL
    vision._VISION_TEXT_CALL = lambda s, c: sentinel_calls.append(1) or "X"
    try:
        out = vision.vision_first_look(PNG_1X1, pool=FakePool())
        assert out == "A red bicycle.", out
        assert sentinel_calls == [], "first-look must not use _VISION_TEXT_CALL"
        assert vision.tool_describe_camera.__module__ == vision.__name__
    finally:
        vision._VISION_TEXT_CALL = old
    print("ok first_look_leaves_existing_chain_untouched")


def test_vision_preview_publish_and_tools():
    # Alek, 2026-10-27: the dashboard PiP shows the frame a vision
    # tool analyzed when he asks what she sees. The tools publish
    # the frame they captured; nothing publishes on its own (the
    # ambient camera tile he removed stays removed).
    before = vision.vision_preview()        # earlier tests may have
    vision.publish_vision_preview(None, "CAMERA")      # empty = no-op
    assert vision.vision_preview() == before
    old_cam, old_scr = vision.capture_webcam, vision.capture_screen
    old_call = vision._VISION_TEXT_CALL
    vision.capture_webcam = lambda: PNG_1X1
    vision.capture_screen = lambda: PNG_1X1
    vision._VISION_TEXT_CALL = lambda s, c: "described"
    try:
        assert vision.tool_describe_camera("what do you see?") == "described"
        pv = vision.vision_preview()
        assert pv["jpeg"] == PNG_1X1 and pv["label"] == "CAMERA" and pv["ts"] > 0
        assert vision.tool_read_screen("read it") == "described"
        assert vision.vision_preview()["label"] == "SCREEN"
        out = vision.vision_first_look(PNG_1X1, pool=FakePool())
        assert out == "A red bicycle."
        assert vision.vision_preview()["label"] == "VISION"
    finally:
        vision.capture_webcam, vision.capture_screen = old_cam, old_scr
        vision._VISION_TEXT_CALL = old_call
    print("ok vision_preview_publish_and_tools")
