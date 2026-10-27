"""PiP publish wiring in aria.main.handle_action (Alek, 2026-10-27).

The primary "what do you see" path is NOT a vision tool: main's
handle_action captures the webcam/screen INLINE for any prompt
containing see/look/holding/camera (or screen/display/desktop)
and hands the bytes to the brain as context. The first PiP cut
published only inside the vision tools, so his actual question
never produced a PiP — his live report caught it. aria.main
cannot be imported in this sandbox (cv2 / speech_recognition /
the full subsystem graph), so the wiring pins by source, the
same style as the LVI F11 pins and the qthost maximized pin;
the publish store and tools are pinned behaviorally in
test_vision_look, the PiP widget in test_holo_face.
"""
from pathlib import Path

SRC = (Path(__file__).parents[1] / "aria" / "main.py").read_text(
    encoding="utf-8")


def _handle_action_body() -> str:
    start = SRC.index("def handle_action")
    end = SRC.index("def _ptt_rising_edge")
    return SRC[start:end]


def test_handle_action_publishes_camera_frame():
    body = _handle_action_body()
    cap = body.index("vision.capture_webcam()")
    pub = body.index('vision.publish_vision_preview(image_bytes, "CAMERA")')
    assert cap < pub          # publish the frame it just captured


def test_handle_action_publishes_screen_frame():
    body = _handle_action_body()
    cap = body.index("vision.capture_screen_if_changed()")
    pub = body.index('vision.publish_vision_preview(image_bytes, "SCREEN")')
    assert cap < pub


def test_vision_module_defines_the_preview_store():
    vsrc = (Path(__file__).parents[1] / "aria" / "vision.py").read_text(
        encoding="utf-8")
    assert "def publish_vision_preview" in vsrc
    assert "def vision_preview" in vsrc
