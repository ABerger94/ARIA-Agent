"""
ARIA Vision & Visual Perception Subsystem.
Webcam capture, screen diffing/capture, screen reading via Gemini vision,
photo/screenshot tools, face tracking with servos, and Phone Bridge MJPEG frame publishing.
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
import time
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, Callable

import cv2
import numpy as np
from PIL import ImageGrab

from aria.config import WORKSPACE_DIR, add_log
from aria.hardware import send_servo_command, SERVO_POS

VISION_SCREEN_SIZE = (800, 450)
VISION_CAM_SIZE = (640, 480)

# Robot body camera: the head-mounted USB webcam, or a network camera URL.
# Set ARIA_BODY_CAMERA=1 (etc.) when the laptop's built-in cam should stay
# index 0 and the body's webcam is the second device — or set it to a stream
# URL (e.g. the IP Webcam app on ARIA's face phone:
# ARIA_BODY_CAMERA=http://192.168.1.42:8080/video). See docs/ROBOT_BODY.md.
BODY_CAMERA_RAW = os.environ.get("ARIA_BODY_CAMERA", "0").strip()


def _body_camera_source():
    """USB camera index (int) or network stream URL (str) for the body camera."""
    raw = BODY_CAMERA_RAW
    if raw.lower().startswith(("http://", "https://")):
        return raw
    try:
        return int(raw)
    except ValueError:
        return 0


def open_body_camera():
    """OpenCV capture for the body camera — USB index or network stream."""
    return cv2.VideoCapture(_body_camera_source())

LATEST_CAMERA_FRAME: Optional[np.ndarray] = None
_LAST_SCREEN_HASH: Optional[str] = None
_VISION_LAST: Tuple[Optional[bool], Optional[float]] = (None, None)
_SCREEN_LAST: Tuple[Optional[bool], Optional[float]] = (None, None)

FACE_TRACKING: bool = False
FACE_CROP = (430, 95, 420, 350)
_FACE_FRAME = {"jpeg": None, "lock": threading.Lock(), "last": 0.0}
_FACE_FPS_MIN_GAP = 0.08  # ~12 fps max encode rate

_VISION_TEXT_CALL: Optional[Callable[[str, Any], str]] = None


def set_vision_text_caller(fn: Callable[[str, Any], str]):
    global _VISION_TEXT_CALL
    _VISION_TEXT_CALL = fn


def get_vision_status() -> Dict[str, Any]:
    return {
        "vision_last": _VISION_LAST,
        "screen_last": _SCREEN_LAST,
        "face_tracking": FACE_TRACKING,
        "has_frame": LATEST_CAMERA_FRAME is not None
    }


def capture_screen() -> bytes:
    """Capture the primary screen buffer resized to VISION_SCREEN_SIZE as JPEG bytes."""
    global _SCREEN_LAST
    add_log("Capturing primary screen buffer...")
    try:
        img = ImageGrab.grab()
        img_np = np.array(img)
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        small = cv2.resize(img_bgr, VISION_SCREEN_SIZE)
        _, buffer = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
        _SCREEN_LAST = (True, time.time())
        return buffer.tobytes()
    except Exception as e:
        _SCREEN_LAST = (False, time.time())
        raise e


def capture_screen_if_changed() -> Optional[bytes]:
    """Returns fresh bytes only if the screen changed since last check; otherwise None."""
    global _LAST_SCREEN_HASH
    data = capture_screen()
    h = hashlib.md5(data).hexdigest()
    if h == _LAST_SCREEN_HASH:
        add_log("Screen unchanged — skipping upload.")
        return None
    _LAST_SCREEN_HASH = h
    return data


def capture_webcam() -> Optional[bytes]:
    """Capture a fresh frame from the local webcam."""
    global LATEST_CAMERA_FRAME, _VISION_LAST
    cap = open_body_camera()
    ret, frame = None, None
    for _ in range(3):
        ret, frame = cap.read()
    cap.release()
    if ret and frame is not None:
        _VISION_LAST = (True, time.time())
        LATEST_CAMERA_FRAME = frame.copy()
        small = cv2.resize(frame, VISION_CAM_SIZE)
        _, buffer = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
        return buffer.tobytes()
    _VISION_LAST = (False, time.time())
    return None


def _safe_name(name: str, default: str) -> str:
    safe = "".join(c for c in str(name) if c.isalnum() or c in ("-", "_", " "))[:40].strip()
    return safe or default


def tool_screenshot(name: str = "") -> str:
    """Save screenshot of user's display."""
    d = os.path.join(WORKSPACE_DIR, "screenshots")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, _safe_name(name, datetime.now().strftime("shot_%Y%m%d_%H%M%S")) + ".png")
    try:
        try:
            import pyautogui
            pyautogui.screenshot(path)
        except Exception:
            ImageGrab.grab().save(path)
        return f"Screenshot saved: {path}"
    except Exception as e:
        return f"[Screenshot failed: {e}]"


def tool_take_photo(name: str = "") -> str:
    """Capture and save a still photograph from webcam."""
    frame = capture_webcam()
    if frame is None and LATEST_CAMERA_FRAME is None:
        return "[Camera not available right now.]"
    d = os.path.join(WORKSPACE_DIR, "photos")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, _safe_name(name, datetime.now().strftime("photo_%Y%m%d_%H%M%S")) + ".jpg")
    try:
        if LATEST_CAMERA_FRAME is not None:
            cv2.imwrite(path, LATEST_CAMERA_FRAME)
            return f"Photo saved: {path}"
        return "[No camera frame captured.]"
    except Exception as e:
        return f"[Photo save failed: {e}]"


def tool_read_screen(question: str = "") -> str:
    """Analyze current user screen using multimodal vision."""
    try:
        data = capture_screen()
    except Exception as e:
        return f"[Screen capture failed: {e}]"
    q = question or "Read all text visible on this screen, top to bottom. Be concise."
    b64 = base64.b64encode(data).decode("utf-8")
    contents = [{"role": "user", "parts": [
        {"text": q},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64}}
    ]}]

    if _VISION_TEXT_CALL:
        return _VISION_TEXT_CALL("You are a precise screen reader. Answer only what is asked, concisely.", contents)
    
    # Fallback to local import if handler not yet attached
    try:
        from aria.agent.brain import gemini_text
        return gemini_text("You are a precise screen reader. Answer only what is asked, concisely.", contents)
    except Exception as e:
        return f"[Screen reading unavailable: {e}]"


def tool_face_tracking(on: Any) -> str:
    """Toggle continuous camera face tracking to control physical neck servos."""
    global FACE_TRACKING
    FACE_TRACKING = bool(on) if isinstance(on, bool) else str(on).lower() in ("1", "true", "on", "yes", "start")
    return ("Face tracking ON — the head will follow you."
            if FACE_TRACKING else "Face tracking OFF.")


def face_track_loop(is_busy_fn: Optional[Callable[[], bool]] = None):
    """Background daemon loop for face detection & servo tracking."""
    try:
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if cascade.empty():
            raise RuntimeError("cascade failed to load")
    except Exception as e:
        add_log(f"Face tracking unavailable: {e}")
        return

    while True:
        time.sleep(2.5)
        try:
            busy = is_busy_fn() if is_busy_fn else False
            if not FACE_TRACKING or busy:
                continue
            cap = open_body_camera()
            ret, frame = cap.read()
            cap.release()
            if not ret or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.2, 5, minSize=(60, 60))
            if len(faces) == 0:
                continue
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            fh, fw = gray.shape
            dx = ((x + w / 2) - fw / 2) / (fw / 2)
            dy = ((y + h / 2) - fh / 2) / (fh / 2)
            new_pan = max(0, min(180, SERVO_POS["pan"] - dx * 20))
            new_tilt = max(0, min(180, SERVO_POS["tilt"] + dy * 14))
            SERVO_POS["pan"], SERVO_POS["tilt"] = new_pan, new_tilt
            send_servo_command(int(new_pan), int(new_tilt))
        except Exception as e:
            add_log(f"Face track err: {e}")


def publish_face_frame(canvas: np.ndarray):
    """Crop the face and stash a JPEG for the Phone Bridge."""
    now = time.time()
    if now - _FACE_FRAME["last"] < _FACE_FPS_MIN_GAP:
        return
    try:
        x, y, w, h = FACE_CROP
        ch, cw = canvas.shape[:2]
        crop = canvas[max(0, y):min(ch, y + h), max(0, x):min(cw, x + w)]
        if crop.size == 0:
            return
        small = cv2.resize(crop, (280, 233), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
        if not ok:
            return
        data = buf.tobytes()
        with _FACE_FRAME["lock"]:
            _FACE_FRAME["jpeg"] = data
            _FACE_FRAME["last"] = now
    except Exception:
        pass


def get_face_frame_jpeg() -> Optional[bytes]:
    """Retrieve the latest MJPEG frame for the Phone Bridge."""
    with _FACE_FRAME["lock"]:
        return _FACE_FRAME["jpeg"]
