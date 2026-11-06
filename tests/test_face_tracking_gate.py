"""Pass 26 B2#1 (config half): face_tracking must be safe-mode-blocked.

Face tracking drives the head servos (vision.py face_track_loop
calls send_servo_command directly), so the tool name belongs in
SAFE_MODE_BLOCKED_TOOLS alongside move_head_servos / drive_wheels.
The dispatch-level and loop servo-write assertions belong to the
companion agent's half; this test pins only the config set.
"""
import importlib
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
_pkg = types.ModuleType('aria')
_pkg.__path__ = [PKG]
sys.modules.setdefault('aria', _pkg)

config = importlib.import_module('aria.config')


def test_face_tracking_in_safe_mode_blocked_tools():
    assert 'face_tracking' in config.SAFE_MODE_BLOCKED_TOOLS


def test_face_tracking_is_safe_mode_blocked():
    assert config.is_safe_mode_blocked('face_tracking') is True


# --- Loop half (companion agent): face_track_loop must re-check safe
# mode every iteration and issue NO servo writes while it is on. The
# config-set half above gates enabling the tool; the loop check covers
# a loop that outlives the toggle (started before safe mode, or
# toggled on while it runs).
import types as _types

import numpy as _np

vision = importlib.import_module("aria.vision")


class _StopLoop(Exception):
    pass


class _FakeCascade:
    def empty(self):
        return False

    def detectMultiScale(self, *a, **k):
        return [(100, 100, 120, 120)]


class _FakeCap:
    def read(self):
        return True, _np.zeros((480, 640, 3), dtype=_np.uint8)

    def release(self):
        pass


def _run_loop_once(*, safe_mode):
    """Run face_track_loop for exactly one iteration; return servo calls."""
    calls = []
    old_tracking = vision.FACE_TRACKING
    old_servo = vision.send_servo_command
    old_cap = vision.open_body_camera
    old_cascade = getattr(vision.cv2, "CascadeClassifier", None)
    had_cascade = hasattr(vision.cv2, "CascadeClassifier")
    old_time = vision.time
    old_pos = dict(vision.SERVO_POS)
    old_env = os.environ.get("ARIA_SAFE_MODE")
    sleeps = {"n": 0}

    def _sleep(_s):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise _StopLoop

    try:
        vision.FACE_TRACKING = True
        vision.send_servo_command = lambda p, t: calls.append((p, t))
        vision.open_body_camera = lambda: _FakeCap()
        vision.cv2.CascadeClassifier = lambda *a, **k: _FakeCascade()
        vision.time = _types.SimpleNamespace(sleep=_sleep, time=old_time.time)
        vision.SERVO_POS.update({"pan": 90, "tilt": 45})
        if safe_mode:
            os.environ["ARIA_SAFE_MODE"] = "1"
        else:
            os.environ.pop("ARIA_SAFE_MODE", None)
        try:
            vision.face_track_loop(lambda: False)
        except _StopLoop:
            pass
        return calls
    finally:
        vision.FACE_TRACKING = old_tracking
        vision.send_servo_command = old_servo
        vision.open_body_camera = old_cap
        if had_cascade:
            vision.cv2.CascadeClassifier = old_cascade
        else:
            del vision.cv2.CascadeClassifier
        vision.time = old_time
        vision.SERVO_POS.update(old_pos)
        if old_env is None:
            os.environ.pop("ARIA_SAFE_MODE", None)
        else:
            os.environ["ARIA_SAFE_MODE"] = old_env


def test_face_track_loop_issues_no_servo_writes_in_safe_mode():
    assert _run_loop_once(safe_mode=True) == []


def test_face_track_loop_drives_servos_when_safe_mode_off():
    calls = _run_loop_once(safe_mode=False)
    assert len(calls) == 1, calls
