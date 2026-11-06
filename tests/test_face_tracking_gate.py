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
