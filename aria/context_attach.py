"""
ARIA clipboard context attachment (ChatGPT loop pass 6, Alek's pick).

THE GAP
    ARIA could already take screen and camera context into a turn,
    and files through the drop-zone pipeline — but handing her a
    piece of TEXT you just copied (an error message, an email
    paragraph, an article excerpt) meant reading it aloud or
    pasting it into the prompt by hand.

THE FLOW
    The dashboard's "Attach clipboard" button (or Ctrl+Shift+V)
    snapshots the clipboard text HERE, on demand — nothing watches
    the clipboard in the background. The staged text rides the
    user's next handle_action turn as a clearly-delimited context
    block prepended to their words, then clears. A preview chip on
    the dashboard shows what is staged, with a remove button.

    The staged text is capped so a careless copy of a whole log
    file cannot flood a turn; truncation is reported in the block
    itself, never silent.

State is module-level (like media_control's panel registry) with
injectable functions in the tests; this module imports nothing
from the package and no Qt.
"""

from __future__ import annotations

import threading
from typing import Optional

MAX_CHARS = 8000
PREVIEW_CHARS = 80

_LOCK = threading.Lock()
_PENDING: Optional[dict] = None  # {"text", "chars", "truncated"}


def set_pending(text: str) -> Optional[dict]:
    """Stage a clipboard snapshot. Returns the staged summary, or
    None when the text is empty / whitespace-only (nothing staged,
    any previous staging left untouched)."""
    global _PENDING
    t = text or ""
    if not t.strip():
        return None
    truncated = len(t) > MAX_CHARS
    if truncated:
        t = t[:MAX_CHARS]
    with _LOCK:
        _PENDING = {"text": t, "chars": len(t), "truncated": truncated}
        return dict(_PENDING)


def pending_summary() -> Optional[dict]:
    """What is staged, without the full text: {chars, truncated,
    preview} — enough for the dashboard chip. None when empty."""
    with _LOCK:
        if _PENDING is None:
            return None
        p = _PENDING
    flat = " ".join(p["text"].split())
    preview = flat[:PREVIEW_CHARS] + ("…" if len(flat) > PREVIEW_CHARS else "")
    return {"chars": p["chars"], "truncated": p["truncated"],
            "preview": preview}


def clear_pending() -> None:
    global _PENDING
    with _LOCK:
        _PENDING = None


def consume_block() -> Optional[str]:
    """Take the staged text as a formatted context block for the
    next user turn, clearing the staging. None when empty."""
    global _PENDING
    with _LOCK:
        p = _PENDING
        _PENDING = None
    if p is None:
        return None
    note = " (truncated to the first 8000 characters)" if p["truncated"] else ""
    return ("[Attached clipboard text — the user copied this and is "
            f"asking about it{note}]\n{p['text']}\n"
            "[End of attached clipboard text]")
