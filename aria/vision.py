"""
ARIA Vision & Visual Perception Subsystem.
Webcam capture, screen diffing/capture, screen reading via Ollama vision,
photo/screenshot tools, face tracking with servos, and Phone Bridge MJPEG frame publishing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import urllib.request
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, Callable

import cv2
import numpy as np
from PIL import ImageGrab

from aria.config import WORKSPACE_DIR, add_log
from aria.hardware import send_servo_command, SERVO_POS
from aria import config as _config_silent

VISION_SCREEN_SIZE = (800, 450)
VISION_CAM_SIZE = (640, 480)

# Robot body camera: the head-mounted USB webcam, a network camera URL, or
# frames uploaded live by the Phone Bridge page.
# Set ARIA_BODY_CAMERA=1 (etc.) when the laptop's built-in cam should stay
# index 0 and the body's webcam is the second device — or set it to a stream
# URL (e.g. the IP Webcam app on ARIA's face phone:
# ARIA_BODY_CAMERA=http://192.168.1.42:8080/video) — or set it to "bridge"
# to use the bridge page's own camera (tap "Camera: ON" on the phone).
# See docs/ROBOT_BODY.md.
BODY_CAMERA_RAW = os.environ.get("ARIA_BODY_CAMERA", "0").strip()


def _body_camera_source():
    """USB camera index (int), network stream URL (str), or "bridge" for
    frames uploaded by the Phone Bridge page."""
    raw = BODY_CAMERA_RAW
    if raw.lower() == "bridge":
        return "bridge"
    if raw.lower().startswith(("http://", "https://")):
        return raw
    try:
        return int(raw)
    except ValueError:
        return 0


def body_camera_label() -> str:
    """Short human label for the HUD subsystem box: bridge, cam N, or net."""
    src = _body_camera_source()
    if src == "bridge":
        return "bridge"
    if isinstance(src, str):
        return "net"
    return f"cam {src}"


class BridgeCamera:
    """cv2.VideoCapture-compatible shim reading frames uploaded by the
    Phone Bridge page (ARIA_BODY_CAMERA=bridge)."""

    def __init__(self):
        self._last_ok = 0.0

    def isOpened(self):
        return (time.time() - _PHONE_CAM["last"]) < 10.0

    def read(self):
        jpg = get_phone_frame_jpeg()
        if not jpg:
            return False, None
        try:
            frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            return False, None
        if frame is None:
            return False, None
        self._last_ok = time.time()
        return True, frame

    def release(self):
        pass


def open_body_camera():
    """OpenCV capture for the body camera — USB index, network stream, or
    the Phone Bridge page's uploaded frames."""
    src = _body_camera_source()
    if src == "bridge":
        return BridgeCamera()
    return cv2.VideoCapture(src)

LATEST_CAMERA_FRAME: Optional[np.ndarray] = None
_LAST_SCREEN_HASH: Optional[str] = None
_VISION_LAST: Tuple[Optional[bool], Optional[float]] = (None, None)
_SCREEN_LAST: Tuple[Optional[bool], Optional[float]] = (None, None)

FACE_TRACKING: bool = False
FACE_CROP = (430, 95, 420, 350)
_FACE_FRAME = {"jpeg": None, "lock": threading.Lock(), "last": 0.0}
_FACE_FPS_MIN_GAP = 0.08  # ~12 fps max encode rate

# Latest camera frame uploaded by the Phone Bridge page (ARIA_BODY_CAMERA=bridge).
_PHONE_CAM = {"jpeg": None, "lock": threading.Lock(), "last": 0.0}
_PHONE_CAM_FPS_MIN_GAP = 0.15  # ~6 fps max ingest rate


def publish_phone_frame(jpeg: bytes) -> bool:
    """Store the latest camera frame uploaded by the Phone Bridge page."""
    if not jpeg or len(jpeg) < 100 or jpeg[:2] != b"\xff\xd8":
        return False
    now = time.time()
    if now - _PHONE_CAM["last"] < _PHONE_CAM_FPS_MIN_GAP:
        return True  # throttled, not an error
    with _PHONE_CAM["lock"]:
        _PHONE_CAM["jpeg"] = bytes(jpeg)
        _PHONE_CAM["last"] = now
    return True


def get_phone_frame_jpeg() -> Optional[bytes]:
    """Retrieve the latest Phone Bridge camera frame."""
    with _PHONE_CAM["lock"]:
        return _PHONE_CAM["jpeg"]


def get_phone_frame_status() -> dict:
    """Whether the phone camera is actively streaming (for the bridge UI)."""
    with _PHONE_CAM["lock"]:
        last = _PHONE_CAM["last"]
        has = _PHONE_CAM["jpeg"] is not None
    age = time.time() - last if last else -1.0
    return {"active": bool(has and 0 <= age < 10.0),
            "age_s": round(age, 1) if last else -1.0}


def describe_phone_view(question: str = "") -> str:
    """Describe what ARIA's phone camera currently sees."""
    jpg = get_phone_frame_jpeg()
    if not jpg:
        return "[No camera view — turn on Camera in the bridge page's settings.]"
    q = question or "Describe what you see in one or two sentences, as ARIA seeing through her own eyes."
    b64 = base64.b64encode(jpg).decode("utf-8")
    contents = [{"role": "user", "parts": [
        {"text": q},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64}}
    ]}]
    sys_prompt = ("You are ARIA, a robot describing what your own camera eyes see. "
                  "Be concise and concrete.")
    if _VISION_TEXT_CALL:
        return _VISION_TEXT_CALL(sys_prompt, contents)
    return "[Vision unavailable: no vision text caller configured.]"

_NATIVE_LAST_ERROR: Optional[str] = None


def _probe_model_text(model: str, api_key: str) -> Optional[str]:
    """Text-only probe of the vision model via native /api/chat.

    Returns None if the model answers (model/tag OK), else the error string.
    Lets a failed image call distinguish 'bad model tag' from
    'image format rejected'.
    """
    payload = {"model": model, "stream": False,
               "messages": [{"role": "user", "content": "Reply with the word ok."}]}
    try:
        req = urllib.request.Request(
            "https://ollama.com/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if (data.get("message") or {}).get("content", "").strip():
            return None
        return "empty reply"
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:120]
        except Exception:
            detail = ""
        return f"HTTP {e.code}: {detail}"
    except Exception as e:
        return f"{e}"


def _ollama_native_vision_call(sys_prompt: str, contents: Any) -> Optional[str]:
    """Vision via Ollama's native /api/chat endpoint (images array).

    This is Ollama's documented image format — tried FIRST because the
    OpenAI-compatible /v1 endpoint's image_url support on Ollama Cloud is
    unreliable (HTTP 400s). Returns the description text, or None.
    """
    global _NATIVE_LAST_ERROR
    _NATIVE_LAST_ERROR = None
    try:
        from aria.agent.providers import (
            OLLAMA_CLOUD_API_KEY, OLLAMA_CLOUD_MODEL, resolve_role_model)
    except Exception:
        return None
    if not OLLAMA_CLOUD_API_KEY:
        return None
    texts: list = []
    images: list = []
    for msg in contents or []:
        for p in (msg.get("parts") or []):
            if isinstance(p.get("text"), str):
                texts.append(p["text"])
            idata = p.get("inline_data")
            if isinstance(idata, dict) and idata.get("data"):
                images.append(idata["data"])
    if not images:
        return None
    model = resolve_role_model("vision") or OLLAMA_CLOUD_MODEL
    messages = []
    if sys_prompt:
        messages.append({"role": "system", "content": sys_prompt})
    messages.append({"role": "user",
                     "content": "\n".join(texts) or "Describe what you see.",
                     "images": images})
    payload = {"model": model, "stream": False, "messages": messages}
    try:
        req = urllib.request.Request(
            "https://ollama.com/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {OLLAMA_CLOUD_API_KEY}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (data.get("message") or {}).get("content", "").strip()
        return text or None
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:160]
        except Exception:
            detail = ""
        _NATIVE_LAST_ERROR = f"HTTP {e.code}: {detail}"
        add_log(f"ollama native vision: {_NATIVE_LAST_ERROR}")
        # Self-diagnosis: text-only probe of the same model distinguishes
        # "bad model tag" from "image format rejected".
        probe = _probe_model_text(model, OLLAMA_CLOUD_API_KEY)
        if probe is None:
            _NATIVE_LAST_ERROR += " | text-only probe OK -> image format rejected"
        else:
            _NATIVE_LAST_ERROR += f" | text-only probe also failed ({probe}) -> model/tag issue"
        add_log(f"ollama native vision diagnosis: {_NATIVE_LAST_ERROR}")
        return None
    except Exception as e:
        _NATIVE_LAST_ERROR = f"{e}"
        add_log(f"ollama native vision failed: {e}")
        return None


def _default_vision_call(sys_prompt: str, contents: Any) -> str:
    """Vision via the provider chain on the vision-role model.

    Order: (1) Ollama native /api/chat (documented image format),
    (2) OpenAI-compatible image_url via the provider chain (ollama_cloud
    only). If both paths are down, returns a bracketed unavailable message.
    """
    try:
        text = _ollama_native_vision_call(sys_prompt, contents)
        if text:
            return text
    except Exception as e:
        add_log(f"native vision call failed: {e}")
    data = None
    try:
        from aria.agent.providers import provider_call, resolve_role_model
        # Vision is ollama_cloud-only: the image payload would 400/404/403 on
        # the other providers and quarantine THEIR keys for nothing.
        data = provider_call(sys_prompt, contents, tool_decls=None,
                             model_override=resolve_role_model("vision"),
                             only_provider="ollama_cloud")
    except Exception as e:
        add_log(f"vision role call failed: {e}")
    if data and data.get("candidates"):
        parts = data["candidates"][0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts
                       if isinstance(p.get("text"), str)).strip()
        if text:
            return text
        return "[Vision returned no description.]"
    # Native and provider-chain vision are both down — surface the real cause
    # in the user-facing message: the OPS log panel truncates lines, so the
    # diagnosis must travel in chat text.
    native_err = _NATIVE_LAST_ERROR or "not attempted"
    return f"[Vision unavailable: ollama native: {native_err}]"


_VISION_TEXT_CALL: Optional[Callable[[str, Any], str]] = _default_vision_call


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
    h = hashlib.md5(data, usedforsecurity=False).hexdigest()
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

    return "[Screen reading unavailable: no vision text caller configured.]"


def tool_describe_camera(question: str = "") -> str:
    """Look through the webcam right now and describe what it sees."""
    try:
        data = capture_webcam()
    except Exception as e:
        return f"[Camera capture failed: {e}]"
    if not data:
        return "[Camera not available right now.]"
    q = question or "Describe what you see in detail."
    b64 = base64.b64encode(data).decode("utf-8")
    contents = [{"role": "user", "parts": [
        {"text": q},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64}}
    ]}]
    sys_prompt = ("You are ARIA, a robot describing what your own camera eyes see. "
                  "Be concise and concrete.")
    if _VISION_TEXT_CALL:
        return _VISION_TEXT_CALL(sys_prompt, contents)
    return "[Camera description unavailable: no vision text caller configured.]"


# ---------------------------------------------------------------------------
# Vision first-look (Merger Phase 6 — blueprint SS2.4 #4 pattern, SS5
# Phase 6). Pattern credited to Mark-XXXIX-OR's actions/screen_processor.py
# fast path (capture -> one vision call -> short answer); his persistent
# Gemini Live session itself is SKIP (blueprint SS4): it speaks in a
# prebuilt Gemini voice, which invariant #1 forbids. Here the call is
# stateless, goes through the Phase 2 orpool vision pool, and the answer
# is TEXT that the brain speaks through her own Edge pipeline.
#
# Coexistence rule (per the phase brief): the describe_camera /
# read_screen chain above keeps its own _VISION_TEXT_CALL path,
# untouched. First-look is a separate tool for ad-hoc questions over a
# provided image (a dropped/staged file, an explicit path, or a fresh
# webcam capture). The shared piece is the transport — the orpool —
# not the tools.

_FIRST_LOOK_SYSTEM = (
    "You are ARIA's eyes for a quick first look. Answer the question "
    "about the image directly and concisely, in plain text that ARIA "
    "can speak aloud. Never invent details you cannot see."
)

_FIRST_LOOK_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}


def _first_look_surface(error, user_message: str) -> None:
    """Phase 1 surfacing for vision failures — never raises."""
    try:
        from aria.agent.failures import surface_failure
        surface_failure("vision_first_look", error, user_message=user_message)
    except Exception:
        pass


def _first_look_pool(pool):
    if pool is not None:
        return pool
    try:
        from aria.agent.orpool import get_pool
        return get_pool()
    except Exception:
        return None


def vision_first_look(image_bytes: bytes, question: str = "",
                      mime: str = "image/jpeg", *, pool=None) -> str:
    """One stateless vision-pool call over a single image.

    Returns the model's text answer. Every failure — fastpath off, no
    image, pool missing/unavailable, pool error, empty answer — is
    surfaced through Phase 1 and returned as an honest bracketed line;
    a description is never fabricated.
    """
    if not getattr(_config_silent, "VISION_FASTPATH_ENABLED", True):
        return "[Vision first-look is disabled (ARIA_VISION_FASTPATH=0).]"
    if not image_bytes:
        return "[Vision first-look unavailable: no image was provided.]"
    pool = _first_look_pool(pool)
    ready = False
    try:
        ready = bool(pool is not None and pool.available())
    except Exception:
        ready = False
    if not ready:
        _first_look_surface(
            "background vision pool unavailable (disabled or no "
            "OpenRouter key configured)",
            "My quick-look vision is unavailable right now.")
        return ("[Vision first-look unavailable: the background vision "
                "pool is not available.]")
    q = (question or "").strip() or \
        "Describe what you see clearly and concisely."
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        text = pool.vision(q, b64, mime, system=_FIRST_LOOK_SYSTEM)
    except Exception as e:
        _first_look_surface(e, "My quick look at that image failed.")
        return f"[Vision first-look unavailable: {e}]"
    if not text or not str(text).strip():
        _first_look_surface("vision pool returned an empty answer",
                            "My quick look came back empty.")
        return "[Vision first-look returned no description.]"
    return str(text).strip()


def _first_look_image(image_path: str = ""):
    """Resolve the image for a first look: explicit path, else the
    frame's staged file when it is an image, else a fresh webcam
    capture. Returns (bytes_or_None, mime, error_line_or_None)."""
    path = (image_path or "").strip()
    if not path:
        try:
            from aria import frame as _frame
            staged = _frame.staged_file() or ""
        except Exception:
            staged = ""
        if staged and os.path.splitext(staged)[1].lower() in _FIRST_LOOK_MIME:
            path = staged
    if path:
        if not os.path.isfile(path):
            return None, "image/jpeg", f"[Image not found: {path}]"
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            _first_look_surface(e, "I couldn't read that image file.")
            return None, "image/jpeg", f"[Could not read image: {e}]"
        mime = _FIRST_LOOK_MIME.get(
            os.path.splitext(path)[1].lower(), "image/jpeg")
        return data, mime, None
    try:
        data = capture_webcam()
    except Exception as e:
        _first_look_surface(e, "The camera capture failed.")
        return None, "image/jpeg", f"[Camera capture failed: {e}]"
    if not data:
        return None, "image/jpeg", "[Camera not available right now.]"
    return data, "image/jpeg", None


def tool_vision_look(question: str = "", image_path: str = "", *,
                     pool=None) -> str:
    """Tool: ad-hoc vision question over the webcam, a staged/dropped
    image, or an image file path — answered by the first-look pool."""
    data, mime, err = _first_look_image(image_path)
    if err:
        return err
    return vision_first_look(data, question, mime, pool=pool)


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


def publish_face_frame(canvas: np.ndarray, crop_box=None):
    """Crop the face and stash a JPEG for the Phone Bridge."""
    now = time.time()
    if now - _FACE_FRAME["last"] < _FACE_FPS_MIN_GAP:
        return
    try:
        x, y, w, h = FACE_CROP if crop_box is None else crop_box
        ch, cw = canvas.shape[:2]
        crop = canvas[max(0, y):min(ch, y + h), max(0, x):min(cw, x + w)]
        if crop.size == 0:
            return
        # The old fixed 280x233 stamp was sized for the pixel
        # avatar; the phone shows the frame nearly full-screen, so
        # it arrived as upscaled mush (Alek, 2026-10-27). Keep the
        # frame's own aspect, cap the width at 720, quality 82.
        if crop.shape[1] > 720:
            scale = 720 / crop.shape[1]
            crop = cv2.resize(crop, (720, max(1, round(crop.shape[0] * scale))),
                              interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if not ok:
            return
        data = buf.tobytes()
        with _FACE_FRAME["lock"]:
            _FACE_FRAME["jpeg"] = data
            _FACE_FRAME["last"] = now
    except Exception as _e_silent:
        _config_silent.log_silent("publish_face_frame", _e_silent)


def get_face_frame_jpeg() -> Optional[bytes]:
    """Retrieve the latest MJPEG frame for the Phone Bridge."""
    with _FACE_FRAME["lock"]:
        return _FACE_FRAME["jpeg"]
