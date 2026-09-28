"""
ARIA Instant Voice Command Shortcuts.
Fast-path recognition for fixed commands that execute without LLM round-trips.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple, Dict
from aria.tools.builtins import tool_open_app_or_url

# Normalized phrase -> (action_type, target, spoken_ack)
_SHORTCUTS: Dict[str, Tuple[str, str, str]] = {
    "lets play some magic": ("url", "https://convoke.games/en/lobby", "Opening the Convoke lobby. Have a good game."),
    "let us play some magic": ("url", "https://convoke.games/en/lobby", "Opening the Convoke lobby. Have a good game."),
}


def normalize_shortcut(text: str) -> str:
    """Normalize apostrophes, punctuation, and casing for shortcut matching."""
    t = (text or "").lower().replace("’", "").replace("'", "").strip()
    return re.sub(r"[^a-z0-9\s]", "", t).strip()


def check_voice_shortcut(user_prompt: str) -> Optional[Tuple[str, str]]:
    """Return (result_text, spoken_ack) if prompt matches a fast-path shortcut; else None."""
    norm = normalize_shortcut(user_prompt)
    if norm in _SHORTCUTS:
        kind, target, spoken = _SHORTCUTS[norm]
        if kind == "url":
            tool_open_app_or_url(target)
            return f"Executed fast-path shortcut: {target}", spoken
    return None
