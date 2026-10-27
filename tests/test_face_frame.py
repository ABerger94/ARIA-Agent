"""publish_face_frame: the phone bridge's orb frame.

Alek, 2026-10-27: the orb 'looks like shit' on the mobile
bridge. Two causes on this side of the pipeline: the frame was
force-resized to a 280x233 stamp at JPEG quality 65 (sized for
the old pixel avatar) and upscaled by the phone. Pins: aspect
preserved, width capped at 720, content survives the round trip.
"""
import os
import sys
import types

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest

cv2 = pytest.importorskip('cv2')

pkg = types.ModuleType('aria')
pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import vision


def test_publish_keeps_aspect_and_resolution():
    vision._FACE_FRAME['last'] = 0.0
    # A smooth horizontal gradient: JPEG-friendly, and any
    # flattening/downscale shows up as lost range.
    grad = np.tile(np.linspace(0, 255, 900, dtype=np.uint8), (600, 1))
    canvas = np.dstack([grad, grad[:, ::-1], np.full_like(grad, 128)])
    vision.publish_face_frame(canvas, (0, 0, 900, 600))
    data = vision.get_face_frame_jpeg()
    assert data
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] == 720 and img.shape[0] == 480   # aspect kept
    assert img.std() > 40                                # gradient survived


def test_publish_small_frame_not_upscaled():
    vision._FACE_FRAME['last'] = 0.0
    canvas = np.zeros((200, 300, 3), dtype=np.uint8)
    canvas[:] = (40, 60, 80)
    vision.publish_face_frame(canvas, (0, 0, 300, 200))
    data = vision.get_face_frame_jpeg()
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] == 300 and img.shape[0] == 200
