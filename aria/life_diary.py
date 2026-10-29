"""Reading ARIA's life diary from the main process (2026-10-29).

The diary itself (life_diary.jsonl in ~/ARIA) is written by the
life subprocess. This module is the main side's reader: it turns
activity entries into the small human clause the presence
greeting can carry — "While you were out, I napped, read, and
watered the plant." Pure functions over parsed entries, plus one
fail-soft file reader; a missing diary is a quiet day, not an
error.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

MAX_CLAUSE_ITEMS = 3


def diary_path(base_dir=None):
    d = base_dir or os.path.join(os.path.expanduser("~"), "ARIA")
    return os.path.join(d, "life_diary.jsonl")


def read_entries(base_dir=None, since_ts=None):
    """Diary entries (oldest first). since_ts: epoch seconds —
    only entries at or after it are returned. Fail-soft: any
    read/parse problem yields what could be read, or []."""
    out = []
    try:
        with open(diary_path(base_dir), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return out
    for line in lines:
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue
        if since_ts is not None:
            try:
                ts = datetime.fromisoformat(entry["ts"]).timestamp()
            except Exception:
                continue
            if ts < since_ts:
                continue
        out.append(entry)
    return out


def summarize_activities(entries):
    """The greeting clause for a set of diary entries, or ""
    when she did nothing worth reporting. Dedupes the day's
    verb phrases, keeps the first MAX_CLAUSE_ITEMS, and speaks
    in her first person (the 'did' phrases are verb-first, so
    they read correctly after 'I')."""
    dids = []
    for e in entries:
        if e.get("kind") == "activity":
            did = e.get("did")
            if did and did not in dids:
                dids.append(did)
    if not dids:
        return ""
    picked = dids[:MAX_CLAUSE_ITEMS]
    if len(picked) == 1:
        joined = picked[0]
    elif len(picked) == 2:
        joined = f"{picked[0]} and {picked[1]}"
    else:
        joined = ", ".join(picked[:-1]) + f", and {picked[-1]}"
    return f"While you were out, I {joined}."


def summary_for_window(base_dir, away_start_ts):
    """The clause for everything she did since away_start_ts."""
    return summarize_activities(read_entries(base_dir, away_start_ts))
