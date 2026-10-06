"""
ARIA Inbox Subsystem.
Receives photos and files from the phone bridge upload page (or dropped
straight into the inbox folder on the laptop), watches for new arrivals,
and gives the agent tools to list, read, and describe them.
"""

from __future__ import annotations

import base64
import os
import re
import threading
import time
from datetime import datetime
from email import policy
from email.parser import BytesParser
from typing import Callable, Dict, List, Optional, Tuple

try:
    from aria.config import ROOT_DIR, add_log, get_setting
except Exception:  # test harness without the full dependency tree
    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def add_log(msg: str) -> None:  # type: ignore[misc]
        print(f"[inbox] {msg}")

    def get_setting(key: str, default=None):  # type: ignore[misc]
        return default

INBOX_DIR = os.path.join(ROOT_DIR, "inbox")
os.makedirs(INBOX_DIR, exist_ok=True)

# Upload guardrails: 100 MB per request, 50 MB per single file.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".html", ".xml", ".yaml", ".yml"}

_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    "webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}


def safe_filename(name: Optional[str]) -> str:
    """Strip path components and hostile characters from an upload name."""
    base = os.path.basename((name or "").strip()) or "upload"
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._") or "upload"
    return base[:120]


def save_upload(filename: Optional[str], data: bytes) -> str:
    """Persist uploaded bytes into the inbox; dedupe with a counter suffix."""
    name = safe_filename(filename)
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    else:
        ext = "." + ext
    candidate = name
    n = 1
    while os.path.exists(os.path.join(INBOX_DIR, candidate)):
        n += 1
        candidate = f"{stem}_{n}{ext}"
    with open(os.path.join(INBOX_DIR, candidate), "wb") as fh:
        fh.write(data)
    add_log(f"Inbox: saved upload {candidate} ({len(data)} bytes)")
    return candidate


def _fmt_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


def list_inbox() -> List[Dict[str, object]]:
    """Newest-first listing of inbox files with size and timestamp."""
    out: List[Dict[str, object]] = []
    try:
        names = os.listdir(INBOX_DIR)
    except OSError:
        return out
    for name in names:
        path = os.path.join(INBOX_DIR, name)
        if not os.path.isfile(path):
            continue
        st = os.stat(path)
        out.append({
            "name": name,
            "size": st.st_size,
            "size_h": _fmt_size(st.st_size),
            "mtime": st.st_mtime,
            "when": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
        })
    out.sort(key=lambda f: f["mtime"], reverse=True)  # type: ignore[arg-type]
    return out


def inbox_count() -> int:
    try:
        return sum(1 for n in os.listdir(INBOX_DIR)
                   if os.path.isfile(os.path.join(INBOX_DIR, n)))
    except OSError:
        return 0


def parse_multipart(body: bytes, content_type: str) -> List[Tuple[Optional[str], bytes]]:
    """Parse multipart/form-data with the stdlib email package.

    Returns [(filename_or_None, payload_bytes)] for parts carrying a file.
    """
    head = b"Content-Type: " + content_type.encode("utf-8", "replace") + b"\r\nMIME-Version: 1.0\r\n\r\n"
    msg = BytesParser(policy=policy.HTTP).parsebytes(head + body)
    parts: List[Tuple[Optional[str], bytes]] = []
    if not msg.is_multipart():
        return parts
    for part in msg.iter_parts():
        filename = part.get_filename()
        if filename is None:
            continue
        payload = part.get_payload(decode=True)
        parts.append((filename, payload if payload is not None else b""))
    return parts


def _pick_image(name: str = "") -> Optional[Dict[str, object]]:
    images = [f for f in list_inbox()
              if os.path.splitext(str(f["name"]))[1].lower() in IMAGE_EXTS]
    if not images:
        return None
    want = (name or "").strip().lower()
    if want:
        if want.isdigit():
            idx = int(want) - 1
            if 0 <= idx < len(images):
                return images[idx]
        for f in images:
            if str(f["name"]).lower() == want or want in str(f["name"]).lower():
                return f
        return None
    return images[0]


def describe_inbox_image(name: str = "") -> str:
    """Describe an inbox photo with ARIA vision. Blank name = latest image."""
    target = _pick_image(name)
    if target is None:
        if not list_inbox():
            return "[Inbox is empty — nothing to describe.]"
        return "[No image found by that name. Say 'what did I send you' to list the inbox.]"
    path = os.path.join(INBOX_DIR, str(target["name"]))
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return f"[Could not read {target['name']}: {e}]"
    ext = os.path.splitext(str(target["name"]))[1].lower()
    mime = _MIME_BY_EXT.get(ext, "image/jpeg")
    b64 = base64.b64encode(raw).decode("utf-8")
    contents = [{"role": "user", "parts": [
        {"text": "Describe this photo in two or three sentences, as ARIA seeing it for the first time. Be concrete about what is actually visible."},
        {"inline_data": {"mime_type": mime, "data": b64}},
    ]}]
    sys_prompt = ("You are ARIA, a robot describing a photo the user just sent you. "
                  "Be concise and concrete.")
    try:
        from aria.vision import _VISION_TEXT_CALL  # type: ignore[attr-defined]
    except Exception:
        _VISION_TEXT_CALL = None  # type: ignore[assignment]
    if _VISION_TEXT_CALL:
        try:
            return _VISION_TEXT_CALL(sys_prompt, contents)
        except Exception as e:
            return f"[Vision unavailable: {e}]"
    return "[Vision unavailable: no vision backend loaded.]"


def read_inbox_text(name: str = "", max_chars: int = 4000) -> str:
    """Read a text-ish inbox file, truncated. Blank name = latest text file."""
    files = list_inbox()
    if not files:
        return "[Inbox is empty.]"
    want = (name or "").strip().lower()
    target: Optional[Dict[str, object]] = None
    if want:
        for f in files:
            if str(f["name"]).lower() == want or want in str(f["name"]).lower():
                target = f
                break
        if target is None and want.isdigit():
            idx = int(want) - 1
            if 0 <= idx < len(files):
                target = files[idx]
    else:
        texts = [f for f in files
                 if os.path.splitext(str(f["name"]))[1].lower() in TEXT_EXTS]
        target = texts[0] if texts else None
    if target is None:
        return "[No matching text file in the inbox.]"
    path = os.path.join(INBOX_DIR, str(target["name"]))
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read(max_chars + 1)
    except OSError as e:
        return f"[Could not read {target['name']}: {e}]"
    clipped = len(data) > max_chars
    return f"--- {target['name']} ---\n" + data[:max_chars] + ("\n[...truncated]" if clipped else "")


def start_inbox_watcher(on_new: Callable[[List[str]], None], poll_s: float = 5.0) -> threading.Thread:
    """Poll the inbox; call on_new(names) once per batch of new arrivals."""
    def _loop() -> None:
        known = set()
        try:
            known = set(os.listdir(INBOX_DIR))
        except OSError:
            pass
        while True:
            try:
                time.sleep(poll_s)
                try:
                    current = set(os.listdir(INBOX_DIR))
                except OSError:
                    continue
                new = sorted(current - known)
                known = current
                if new:
                    try:
                        on_new(new)
                    except Exception as e:
                        add_log(f"Inbox watcher callback failed: {e}")
            except Exception as e:  # never let the watcher thread die
                add_log(f"Inbox watcher error: {e}")
                time.sleep(poll_s)

    t = threading.Thread(target=_loop, daemon=True, name="inbox-watcher")
    t.start()
    add_log("Inbox watcher started.")
    return t


def inbox_auto_describe() -> bool:
    return bool(get_setting("inbox_auto_describe", False))
