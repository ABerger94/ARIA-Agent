"""
Async memory extraction (Merger Phase 2 — blueprint SS2.3).

Pattern reimplemented from Mark-XXXIX-OR's memory functions
(memory/memory_manager.py: should_extract_memory + extract_memory,
wired post-turn by his main.py _update_memory_async) — FatihMakes,
CC BY-NC 4.0. The two-stage shape is his: a cheap YES/NO relevance
gate so most turns cost ~5 tokens, then a liberal JSON extraction.
What is NOT his: the store (his capped JSON file is SKIP per the
blueprint) — facts land in ARIA's existing semantic memory via
aria.memory.memory_save, in her category/format conventions.

Flow: brain.run_agent finishes a turn -> maybe_extract_async(user,
assistant) -> dedupe guard -> daemon thread -> gate (background
pool) -> extract (background pool) -> memory_save per fact. The
conversation path keeps Fork A's behaviour exactly (the embedding
that feeds THIS turn's prompt stays synchronous at its 5 s cap in
memory._embed); extraction costs the live turn nothing.

Kill switch: ARIA_MEMORY_EXTRACT_ASYNC=0 (config.MEMORY_EXTRACT_ASYNC).
Pool kill switch applies too: no pool, no extraction (logged once
via the failure record, decision=skip).
"""

from __future__ import annotations

import threading
from typing import Dict

from aria import config as _config
from aria.config import add_log

# His category guide, mapped onto ARIA's memory categories
# (preferences -> her singular "preference"; the rest pass through).
_CATEGORY_MAP = {
    "identity": "identity",
    "preferences": "preference",
    "projects": "projects",
    "relationships": "relationships",
    "wishes": "wishes",
    "notes": "notes",
}

_GATE_SYSTEM = "You are a memory relevance checker. Reply only YES or NO."

_EXTRACT_SYSTEM = ("Return ONLY valid JSON. No markdown, no explanation, "
                   "no extra text.")


def _gate_prompt(user_text: str, assistant_text: str) -> str:
    combined = f"User: {user_text[:300]}\nARIA: {assistant_text[:1000]}"
    return (
        "Does this conversation contain ANY of the following?\n"
        "- Personal facts (name, age, city, job, birthday, nationality)\n"
        "- Preferences or favorites (food, color, music, sport, game, film, book, etc.)\n"
        "- Active projects or goals the user is working on\n"
        "- People in the user's life (friends, family, partner, colleagues)\n"
        "- Things the user wants to do or buy in the future\n"
        "- Any other fact worth remembering long-term\n\n"
        f"Reply only YES or NO.\n\nConversation:\n{combined}")


def _extract_prompt(user_text: str, assistant_text: str) -> str:
    combined = f"User: {user_text[:600]}\nARIA: {assistant_text[:300]}"
    return (
        "Extract ALL memorable personal facts from this conversation. "
        "Any language.\n"
        "Return ONLY valid JSON. Use {} if truly nothing is worth saving.\n\n"
        "Category guide:\n"
        "  identity      -> name, age, birthday, city, country, job, school, nationality, language\n"
        "  preferences   -> ANY favorite or preferred thing: favorite_food, favorite_color,\n"
        "                   favorite_music, favorite_film, favorite_game, favorite_sport,\n"
        "                   favorite_book, favorite_artist, hobbies, interests, dislikes, etc.\n"
        "  projects      -> projects being built, ongoing work, goals, ideas in progress\n"
        "  relationships -> people mentioned: friends, family, partner, colleagues\n"
        "  wishes        -> future plans, things to buy, travel plans, dreams\n"
        "  notes         -> anything else worth remembering (habits, schedule, etc.)\n\n"
        "IMPORTANT:\n"
        "- Be LIBERAL: if something MIGHT be worth remembering, include it.\n"
        "- Extract from BOTH the user and ARIA turns.\n"
        "- Skip: weather, reminders, search results, one-time commands.\n"
        "- Use concise English values regardless of conversation language.\n\n"
        "Format:\n"
        '{"identity":{"name":{"value":"..."}},\n'
        ' "preferences":{"favorite_color":{"value":"blue"}},\n'
        ' "projects":{"mark_xxv":{"value":"JARVIS-like AI assistant"}},\n'
        ' "relationships":{"friend_yusuf":{"value":"close friend"}},\n'
        ' "wishes":{"buy_guitar":{"value":"wants an acoustic guitar"}},\n'
        ' "notes":{"works_at_night":{"value":"usually active late at night"}}}\n\n'
        f"Conversation:\n{combined}\n\nJSON:")


def _resolve_pool(pool):
    if pool is not None:
        return pool
    from aria.agent.orpool import get_pool
    return get_pool()


def extract_now(user_text: str, assistant_text: str, *,
                pool=None) -> Dict[str, Dict[str, str]]:
    """Run gate -> extract -> save synchronously. Returns the facts
    actually saved, {aria_category: {key: value}}. Any pool failure
    (unavailable, all models down, bad JSON) yields {} — extraction
    is best-effort background work, never an error worth a turn.
    Exposed for tests and the harness; production calls go through
    maybe_extract_async."""
    pool = _resolve_pool(pool)
    try:
        gate = pool.chat(_gate_prompt(user_text, assistant_text),
                         system=_GATE_SYSTEM, max_tokens=5, temperature=0.0)
    except Exception:
        return {}
    if not gate or "YES" not in gate.upper():
        return {}
    try:
        data = pool.chat_json(_extract_prompt(user_text, assistant_text),
                              system=_EXTRACT_SYSTEM, max_tokens=1024)
    except Exception:
        return {}
    if not isinstance(data, dict) or not data:
        return {}

    from aria import memory as _memory
    saved: Dict[str, Dict[str, str]] = {}
    for category, entries in data.items():
        aria_category = _CATEGORY_MAP.get(str(category).strip().lower())
        if aria_category is None or not isinstance(entries, dict):
            continue
        for key, entry in entries.items():
            value = entry.get("value") if isinstance(entry, dict) else entry
            if value is None or str(value).strip() == "":
                continue
            value = str(value)
            try:
                _memory.memory_save(aria_category, str(key), value)
                saved.setdefault(aria_category, {})[str(key)] = value
            except Exception as e:
                add_log(f"memextract: save failed for {key}: {e}")
    if saved:
        n_facts = sum(len(v) for v in saved.values())
        add_log(f"memextract: saved {n_facts} fact(s) async: "
                f"{', '.join(saved.keys())}")
    return saved


# --- async wrapper (his _update_memory_async shape: dedupe + guard) ----
_LAST_INPUT = ""
_LAST_LOCK = threading.Lock()


def _worker(user_text: str, assistant_text: str) -> None:
    try:
        extract_now(user_text, assistant_text)
    except Exception as e:
        try:
            from aria.agent.failures import surface_failure
            surface_failure("memextract", e, decision="skip",
                            user_message="Background memory saving skipped this turn.")
        except Exception:
            pass


def maybe_extract_async(user_text: str, assistant_text: str) -> bool:
    """Post-turn hook (called from brain.run_agent's completion path).
    Returns True when a background extraction was actually spawned.
    Dedupe guard is his: the same user input never extracts twice,
    and trivially short inputs never extract at all."""
    global _LAST_INPUT
    if not getattr(_config, "MEMORY_EXTRACT_ASYNC", True):
        return False
    user_text = (user_text or "").strip()
    assistant_text = (assistant_text or "").strip()
    if len(user_text) < 5:
        return False
    with _LAST_LOCK:
        if user_text == _LAST_INPUT:
            return False
        _LAST_INPUT = user_text
    t = threading.Thread(target=_worker, args=(user_text, assistant_text),
                         daemon=True, name="aria-memextract")
    t.start()
    return True
