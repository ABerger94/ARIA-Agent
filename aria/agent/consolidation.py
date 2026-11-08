"""
ARIA memory consolidation — reconciliation layer (Gap fill 4).

THE GAP THIS FILLS
    The idle consolidation loop (aria.agent.proactive) distilled
    new facts from chat and saved them blind: aria.memory's
    memory_save upserts ON CONFLICT(key), so a distilled claim
    that contradicted a stored fact silently OVERWROTE it, a
    repeat of the same fact re-saved as churn, and nothing stale
    was ever retired. Mark-LVI's consolidation
    (core/consolidation.py) reconciles instead: changeset-only,
    contradictions queue-and-ask, retirements archived — these
    are those semantics in this codebase's shape.

THE RULES
  * same key + same value (case/whitespace-insensitive) ->
    dupe, skipped quietly;
  * same key + different value -> CONTRADICTION: the stored
    fact is NOT touched; the claim queues in
    WORKSPACE_DIR/memory_contradictions.json for the user to
    settle (memory_review tool), and the superseded value —
    whichever side loses — is archived, never vaporized;
  * new key -> saved under category 'consolidated';
  * retire(key) moves a stored fact into
    WORKSPACE_DIR/memory_retired.json and deletes the live row.

Every store touch is injectable for tests, and every failure is
swallowed to a log line: consolidation runs unattended and must
never break the loop that calls it.
"""

from __future__ import annotations
from aria import workspaces

import json
import os
import sqlite3
import tempfile
import time
import uuid
from typing import Callable, Dict, List, Optional, Tuple

from aria import config as _config
from aria.config import add_log


# ------------------------------------------------------------ storage
def _queue_path() -> str:
    return workspaces.path("memory_contradictions.json")


def _retired_path() -> str:
    return workspaces.path("memory_retired.json")


def _read_json_list(path: str) -> List[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def _write_json_list(path: str, rows: List[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cons-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ------------------------------------------------- live-memory access
def _existing(key: str) -> Optional[Tuple[str, str]]:
    """(category, value) currently stored under key, any category."""
    from aria.memory import DB_LOCK, DB_PATH
    try:
        with DB_LOCK:
            conn = sqlite3.connect(workspaces.path("aria_memory.db", DB_PATH))
            row = conn.execute(
                "SELECT category, value FROM memory WHERE key = ?",
                (key,)).fetchone()
            conn.close()
        return (row[0], row[1]) if row else None
    except Exception as e:
        _config.log_silent("consolidation_existing", e)
        return None


def _delete(key: str) -> None:
    from aria.memory import DB_LOCK, DB_PATH
    with DB_LOCK:
        conn = sqlite3.connect(workspaces.path("aria_memory.db", DB_PATH))
        conn.execute("DELETE FROM memory WHERE key = ?", (key,))
        conn.commit()
        conn.close()


def _save(category: str, key: str, value: str) -> None:
    from aria.memory import memory_save
    memory_save(category, key, value)


def _cas_save(category: str, key: str, value: str,
              expected_value) -> bool:
    """Atomic compare-and-swap save via aria.memory (pass 29
    #50): the compare and the upsert run in one transaction, so
    a correction saved between the resolution's comparison read
    and the upsert cannot be silently overwritten. Returns False
    when a different live value blocked the save."""
    from aria.memory import memory_save_if_value
    return memory_save_if_value(category, key, value, expected_value)


# ------------------------------------------------------------ reconcile
def _norm(value: str) -> str:
    return " ".join(str(value).split()).casefold()


def queue_contradiction(key: str, old: Tuple[str, str],
                        new_value: str) -> dict:
    entry = {
        "id": str(uuid.uuid4())[:8],
        "key": key,
        "old_category": old[0], "old_value": old[1],
        "new_value": new_value,
        "queued_at": time.time(),
    }
    rows = _read_json_list(_queue_path())
    rows.append(entry)
    _write_json_list(_queue_path(), rows)
    return entry


def reconcile(facts: List[Tuple[str, str]], *,
              existing: Callable = _existing,
              save: Callable = _save,
              queue: Callable = queue_contradiction) -> Dict[str, int]:
    """Reconcile distilled (key, value) facts against live
    memory. Returns counts: {saved, dupes, contradictions}."""
    counts = {"saved": 0, "dupes": 0, "contradictions": 0}
    for key, value in facts:
        key = (key or "").strip()
        value = (value or "").strip()
        if not key or not value:
            continue
        try:
            cur = existing(key)
            if cur is not None and _norm(cur[1]) == _norm(value):
                counts["dupes"] += 1
            elif cur is not None:
                queue(key, cur, value)
                counts["contradictions"] += 1
                add_log(f"Consolidation: contradiction queued for "
                        f"'{key}' (stored value kept)")
            else:
                save("consolidated", key, value)
                counts["saved"] += 1
        except Exception as e:
            _config.log_silent("consolidation_reconcile", e)
    return counts


# ------------------------------------------------------- review/resolve
def pending_contradictions() -> List[dict]:
    return _read_json_list(_queue_path())


def retire(key: str, value: str, category: str, reason: str, *,
           write: Callable = _write_json_list,
           read: Callable = _read_json_list) -> None:
    """Archive a fact into memory_retired.json (audit trail —
    replaced facts are kept, not vaporized)."""
    rows = read(_retired_path())
    rows.append({"key": key, "category": category, "value": value,
                 "reason": reason, "retired_at": time.time()})
    write(_retired_path(), rows)


def resolve_contradiction(cid: str, keep: str, *,
                          save: Callable = _save,
                          delete: Callable = _delete,
                          existing: Callable = _existing,
                          cas_save: Callable = None) -> Optional[dict]:
    """Settle a queued contradiction. keep='new': archive the
    stored value and save the distilled claim. keep='old':
    archive the rejected claim, stored fact untouched. Returns
    the settled entry, or None when the id is unknown.

    keep='new' is compare-and-swap (pass 26 B3#6, hardened pass
    29 #50): the live value is read first and the resolution
    REFUSES (raising ValueError, entry stays queued) when a live
    value exists that no longer matches the queued old_value —
    resolving against a stale old_value would delete the CURRENT
    value without archiving it. The replacement is then saved by
    an ATOMIC conditional update (memory_save_if_value: UPDATE
    ... WHERE key=? AND value=?) — the old two-step
    compare-then-upsert let a correction saved between the
    comparison and the upsert be silently overwritten, with the
    archive retaining the original old value instead of the lost
    correction. A failed comparison leaves the entry queued and
    overwrites nothing. The replacement is saved FIRST and
    verified to read back before the old value is retired, so a
    failing save leaves the live key intact. The live row is
    deleted only if it still holds the old value after the save
    (the upsert save already replaced it, so deleting
    unconditionally would destroy the replacement just saved).
    A missing live row means there is nothing to protect: the
    resolution proceeds and saves the claim. `cas_save` (category,
    key, value, expected_value) -> bool overrides the atomic
    save; when it is None and the caller injected a non-default
    `save`, the CAS is emulated from the injected `existing` +
    `save` (re-read, save only while the live value still
    matches) so the injectable test seam keeps working."""
    rows = _read_json_list(_queue_path())
    entry = next((r for r in rows if r.get("id") == cid), None)
    if entry is None:
        return None
    if keep == "new":
        cur = existing(entry["key"])
        if cur is not None and _norm(cur[1]) != _norm(entry["old_value"]):
            raise ValueError(
                f"live value of '{entry['key']}' changed since this "
                f"contradiction was queued — re-review it instead; "
                f"nothing was changed and the entry stays queued")
        expected = cur[1] if cur is not None else None
        if cas_save is not None:
            landed = cas_save("consolidated", entry["key"],
                              entry["new_value"], expected)
        elif save is _save:
            landed = _cas_save("consolidated", entry["key"],
                               entry["new_value"], expected)
        else:
            # Injected save/existing seam (tests): no SQL to be
            # atomic over, so emulate the CAS — re-read and save
            # only while the live value still matches exactly
            # (or is still absent, when nothing was there).
            live = existing(entry["key"])
            if live is None and expected is None:
                save("consolidated", entry["key"], entry["new_value"])
                landed = True
            elif live is not None and live[1] == expected:
                save("consolidated", entry["key"], entry["new_value"])
                landed = True
            else:
                landed = False
        if not landed:
            raise ValueError(
                f"live value of '{entry['key']}' changed while "
                f"resolving — re-review it instead; nothing was "
                f"changed and the entry stays queued")
        after = existing(entry["key"])
        if after is not None and _norm(after[1]) != _norm(entry["new_value"]):
            # The save reported success but did not land: try to
            # put the old value back, then refuse.
            try:
                save(cur[0] if cur else entry.get("old_category", ""),
                     entry["key"], entry["old_value"])
            except Exception:
                pass
            raise ValueError(
                f"replacement for '{entry['key']}' did not read back "
                f"after saving — nothing was retired and the entry "
                f"stays queued")
        retire(entry["key"], entry["old_value"],
               entry.get("old_category", ""), "contradiction: replaced")
        live = existing(entry["key"])
        if live is not None and _norm(live[1]) == _norm(entry["old_value"]):
            delete(entry["key"])
    else:
        retire(entry["key"], entry["new_value"], "consolidated",
               "contradiction: claim rejected")
    _write_json_list(_queue_path(),
                     [r for r in rows if r.get("id") != cid])
    return entry


def retire_fact(key: str, *,
                existing: Callable = _existing,
                delete: Callable = _delete) -> bool:
    """Retire a live fact outright (archived, then deleted)."""
    cur = existing(key)
    if cur is None:
        return False
    retire(key, cur[1], cur[0], "retired")
    delete(key)
    return True
