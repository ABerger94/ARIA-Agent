"""
ARIA task bookmarks (ChatGPT loop pass 6, Alek's pick).

THE GAP
    Conversation history preserves words, but when a spoken work
    session gets interrupted — a shift, a phone call, sleep — there
    was no way to mark the place and pick the task back up with its
    context intact.

THE SLICE (v1)
    One bookmark slot. Saying "save this for later" (or "bookmark
    this") snapshots the CURRENT task from the conversation history:
    the original request, the tools already run (in order), and a
    bounded excerpt of the recent exchange. It persists to
    bookmark.json at the install root, so it survives restarts. Saying "where
    were we" resumes: handle_action hands the brain a resume prompt
    carrying the bookmark, the brain recaps what the task was and
    what was already done, and the context is part of the live
    conversation from that turn on. Saving again replaces the
    bookmark; resuming keeps it (resume twice, no harm).

    Bounds keep the bookmark a bookmark, not a transcript dump:
    request <= 300 chars, excerpt <= 10 lines of <= 400 chars,
    actions <= 12 tool names.

Phrase matching is whole-utterance (via command_lane.normalize),
so "save this file to the desktop" is a normal request, not a
bookmark. This module imports nothing Qt-bound; storage path is
injectable for tests.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from typing import Optional

from aria.command_lane import normalize
from aria import data_dir

SAVE_PHRASES = {"save this for later", "bookmark this"}
RESUME_PHRASES = {"where were we", "pick up where we left off",
                  "resume my bookmark"}

REQUEST_MAX = 300
LINE_MAX = 400
EXCERPT_MAX = 10
ACTIONS_MAX = 12


def default_path() -> str:
    # Install root via aria.data_dir (V20); first resolve
    # migrates any real legacy ~/ARIA/bookmark.json.
    return data_dir.path("bookmark.json")


def is_save(text: str) -> bool:
    return normalize(text) in SAVE_PHRASES


def is_resume(text: str) -> bool:
    return normalize(text) in RESUME_PHRASES


def _clip(s: str, limit: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def build_bookmark(history, *, now: Optional[datetime] = None) -> dict:
    """Snapshot the current task from brain history entries of the
    shape {"role": ..., "parts": [{"text"|"functionCall"|...}]}."""
    request = ""
    lines = []
    actions = []
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        for part in entry.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if part.get("text"):
                if role == "user":
                    request = part["text"]
                    lines.append("User: " + _clip(part["text"], LINE_MAX))
                elif role == "model":
                    lines.append("ARIA: " + _clip(part["text"], LINE_MAX))
            fc = part.get("functionCall")
            if isinstance(fc, dict) and fc.get("name"):
                if fc["name"] not in actions:
                    actions.append(fc["name"])
    return {
        "saved_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "request": _clip(request, REQUEST_MAX),
        "excerpt": lines[-EXCERPT_MAX:],
        "actions": actions[:ACTIONS_MAX],
    }


def save(bookmark: dict, path: Optional[str] = None) -> str:
    """Persist atomically (tmp file + replace, the life_state
    pattern). Returns the path written."""
    p = path or default_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(bookmark, f, indent=1)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def load(path: Optional[str] = None) -> Optional[dict]:
    p = path or default_path()
    try:
        with open(p, encoding="utf-8") as f:
            bm = json.load(f)
    except (OSError, ValueError):
        return None
    return bm if isinstance(bm, dict) and bm.get("request") else None


def try_save(text: str, history, path: Optional[str] = None,
             now: Optional[datetime] = None) -> Optional[str]:
    """If text is a save phrase, build + persist the bookmark and
    return the acknowledgement; else None."""
    if not is_save(text):
        return None
    bm = build_bookmark(history, now=now)
    if not bm["request"]:
        return "There's nothing to save yet — give me a task first."
    save(bm, path)
    return f"Saved for later: {bm['request']}"


def _saved_when(bm: dict) -> str:
    try:
        dt = datetime.fromisoformat(bm.get("saved_at", ""))
        return dt.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return "earlier"


def resume_prompt(bm: dict) -> str:
    """The synthetic turn handle_action hands the brain on resume:
    recap from the bookmark, with the context now in-conversation."""
    actions = ", ".join(bm.get("actions") or []) or "none"
    excerpt = "\n".join(bm.get("excerpt") or [])
    return (
        "[Task bookmark resumed]\n"
        f"The user saved a task bookmark at {_saved_when(bm)} and now "
        "asks where you left off. Recap briefly what the task was and "
        "what was already done, then ask what they would like to do "
        "next. This bookmark is part of the conversation now — use "
        "its context for anything that follows. Do not re-run any "
        "tool that is listed as already done.\n\n"
        f"Original request: {bm.get('request', '')}\n"
        f"Tools already run, in order: {actions}\n\n"
        f"Recent conversation excerpt:\n{excerpt}")


def nothing_saved_ack() -> str:
    return "You don't have anything saved for later."
