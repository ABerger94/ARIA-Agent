"""A failed camera capture must never save an old photo (pass 26 B5#9).

tool_take_photo used to fall through to the stale module-global
LATEST_CAMERA_FRAME when capture_webcam() returned None, reporting
"Photo saved" for a photo that was never taken — and it never
checked cv2.imwrite's return value, so even a failed write reported
success. The fix: a failed capture reports unavailability and writes
nothing; only the frame just captured is written, and a False from
imwrite reports "[Photo save failed]".

Run per-file:  python3 -m pytest tests/test_camera_fresh_photo.py -v
"""
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_camera_fresh_")
os.environ["HOME"] = _TMPHOME

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
_hw = types.ModuleType("aria.hardware")
_hw.send_servo_command = lambda *a, **k: None
_hw.SERVO_POS = {"pan": 90, "tilt": 45}
sys.modules["aria.hardware"] = _hw
sys.modules["aria.agent.brain"] = types.ModuleType("aria.agent.brain")

import importlib
from pathlib import Path

import numpy as np

config = importlib.import_module("aria.config")
vision = importlib.import_module("aria.vision")

_TMP = Path(tempfile.mkdtemp(prefix="aria_camera_fresh_tree_", dir=_TMPHOME))
vision.WORKSPACE_DIR = str(_TMP)

FRESH = np.zeros((480, 640, 3), dtype=np.uint8)
STALE = np.full((480, 640, 3), 255, dtype=np.uint8)


def _photos():
    d = _TMP / "photos"
    return list(d.iterdir()) if d.exists() else []


def test_failed_capture_reports_unavailable_and_writes_nothing():
    old_cap = vision.capture_webcam
    old_frame = vision.LATEST_CAMERA_FRAME
    try:
        vision.LATEST_CAMERA_FRAME = STALE
        vision.capture_webcam = lambda: None
        out = vision.tool_take_photo("stale_check")
        assert out == "[Camera not available right now.]", out
        assert _photos() == [], "a failed capture must not write any file"
    finally:
        vision.capture_webcam = old_cap
        vision.LATEST_CAMERA_FRAME = old_frame


def test_failed_write_reports_save_failed():
    old_cap = vision.capture_webcam
    old_frame = vision.LATEST_CAMERA_FRAME
    old_imwrite = vision.cv2.imwrite
    try:
        def _capture_ok():
            vision.LATEST_CAMERA_FRAME = FRESH
            return b"fake-jpeg-bytes"

        vision.capture_webcam = _capture_ok
        vision.cv2.imwrite = lambda *a, **k: False
        out = vision.tool_take_photo("write_fail")
        assert out == "[Photo save failed]", out
    finally:
        vision.capture_webcam = old_cap
        vision.LATEST_CAMERA_FRAME = old_frame
        vision.cv2.imwrite = old_imwrite


def test_successful_capture_still_saves_fresh_frame():
    old_cap = vision.capture_webcam
    old_frame = vision.LATEST_CAMERA_FRAME
    try:
        def _capture_ok():
            vision.LATEST_CAMERA_FRAME = FRESH
            return b"fake-jpeg-bytes"

        vision.capture_webcam = _capture_ok
        before = set(_photos())
        out = vision.tool_take_photo("fresh_check")
        assert out.startswith("Photo saved: "), out
        new = [p for p in _photos() if p not in before]
        assert len(new) == 1 and new[0].stat().st_size > 0
    finally:
        vision.capture_webcam = old_cap
        vision.LATEST_CAMERA_FRAME = old_frame
