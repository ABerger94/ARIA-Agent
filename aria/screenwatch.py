"""Screen watcher — Module 7 of ARIA ULTIMATE.

Watches the user's screen for changes to a question (e.g. "what's the order
status?", "is the build done?"). Each watch is persisted to
``~/ARIA/screen_watches.json`` and fires a recurring scheduler task that
runs ``check_watch``.

SCHEDULER WIRING (coordinator): ``tool_watch_screen`` creates the recurring
task with ``sched_add("screenwatch", name, interval_s=...)``. The scheduler
loop's ``else:`` branch currently routes unknown kinds to
``run_agent_fn``. Insert this ``elif`` BEFORE the ``else`` in
``scheduler_loop`` so screen-watch ticks run the check in-process::

    elif kind == "screenwatch":
        nxt = datetime.now() + timedelta(seconds=interval_s or 300)
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
                         (nxt.strftime("%Y-%m-%d %H:%M:%S"), tid))
            conn.commit()
            conn.close()
        try:
            from aria.screenwatch import check_watch
            check_watch(prompt or "")
        except Exception as _sw_err:
            add_log(f"Screenwatch check failed: {_sw_err}")

That keeps recurring state (next_run) in the scheduler DB and the check
logic here. Until the hook lands, watches are stored but never tick — the
check can also be driven manually via ``check_watch(name)``.

Import discipline: module top is stdlib + ``aria.config`` only. ``vision``
and ``scheduler`` are imported lazily inside functions.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import time
from typing import Dict

from aria.config import add_log

WATCHES_PATH = os.path.expanduser(os.path.join("~", "ARIA", "screen_watches.json"))
MIN_INTERVAL_S = 60
DIFF_THRESHOLD = 0.85
_NAME_RE = re.compile(r"[^a-z0-9_-]")


def _sanitize_name(name: str) -> str:
    """Lowercase, alnum + _/- only, max 40 chars (same rule as routines)."""
    clean = _NAME_RE.sub("", str(name or "").lower().strip())[:40]
    return clean


def _load() -> Dict[str, dict]:
    try:
        with open(WATCHES_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(watches: Dict[str, dict]) -> None:
    os.makedirs(os.path.dirname(WATCHES_PATH), exist_ok=True)
    with open(WATCHES_PATH, "w", encoding="utf-8") as fh:
        json.dump(watches, fh, indent=2)


def answers_differ(old: str, new: str, threshold: float = DIFF_THRESHOLD) -> bool:
    """Pure comparator: True if the screen answer changed meaningfully.

    ``difflib.SequenceMatcher`` ratio < 0.85 -> changed. Kept pure for tests.
    """
    old, new = (old or "").strip(), (new or "").strip()
    if old == new:
        return False
    if not old or not new:
        return True
    ratio = difflib.SequenceMatcher(None, old, new).ratio()
    return ratio < threshold


def check_watch(name: str) -> str:
    """Run one check for a watch. Emits ``screen_watch_triggered`` on change."""
    name = _sanitize_name(name)
    if not name:
        return "[Invalid watch name.]"
    watches = _load()
    watch = watches.get(name)
    if not watch:
        return f"[No screen watch named '{name}'.]"
    try:
        from aria.vision import tool_read_screen
    except Exception as e:
        return f"[Screen reading unavailable: {e}]"
    question = watch.get("question", "")
    try:
        answer = tool_read_screen(question)
    except Exception as e:
        return f"[Screen check failed: {e}]"
    answer = str(answer or "")
    if answer.startswith("[Screen capture failed") or \
       answer.startswith("[Screen reading unavailable"):
        return f"[Screen check failed: {answer}]"

    last = watch.get("last_answer")
    watch["last_answer"] = answer
    watch["last_checked"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(watches)

    if last is not None and answers_differ(last, answer):
        try:
            from aria.events import emit
            emit("screen_watch_triggered", {
                "name": name,
                "question": question,
                "before": str(last)[:500],
                "after": answer[:500],
            })
        except Exception:
            pass  # the bus never breaks producers
        add_log(f"Screen watch '{name}' triggered (answer changed)")
        return f"Screen watch '{name}': change detected."
    return f"Screen watch '{name}': no change."


def tool_watch_screen(name: str, question: str, interval_s: int = 300) -> str:
    """Start watching the screen for changes to ``question``."""
    name = _sanitize_name(name)
    if not name:
        return "[Invalid watch name — use lowercase letters, numbers, _ or -.]"
    if not str(question or "").strip():
        return "[A question is required — what should I look for on the screen?]"
    try:
        interval = int(interval_s)
    except (TypeError, ValueError):
        interval = 300
    if interval < MIN_INTERVAL_S:
        return f"[Minimum watch interval is {MIN_INTERVAL_S}s — try a longer interval.]"

    watches = _load()
    if name in watches:
        return f"[Already watching '{name}'. Use unwatch_screen first to replace it.]"

    try:
        from aria import scheduler
        task_id = scheduler.sched_add("screenwatch", name, interval_s=interval)
    except Exception as e:
        return f"[Could not create the recurring check: {e}]"

    watches[name] = {
        "question": str(question).strip(),
        "interval_s": interval,
        "sched_task_id": task_id,
        "last_answer": None,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_checked": None,
    }
    _save(watches)
    add_log(f"Screen watch '{name}' added (every {interval}s, task #{task_id})")
    return (f"Watching for '{question}' every {interval}s (watch '{name}', "
            f"check task #{task_id}). I'll speak up when it changes.")


def tool_unwatch_screen(name: str) -> str:
    """Stop a screen watch and remove its recurring check."""
    name = _sanitize_name(name)
    watches = _load()
    watch = watches.pop(name, None)
    if not watch:
        return f"[No screen watch named '{name}'.]"
    task_id = watch.get("sched_task_id")
    if task_id:
        try:
            from aria import scheduler
            scheduler.sched_cancel(int(task_id))
        except Exception as e:
            add_log(f"Screen watch '{name}': failed to cancel task #{task_id}: {e}")
    _save(watches)
    return f"Stopped watching '{name}'."


def tool_list_screen_watches() -> str:
    """List all active screen watches."""
    watches = _load()
    if not watches:
        return "[No active screen watches.]"
    lines = []
    for name, w in watches.items():
        last = w.get("last_checked") or "never checked"
        lines.append(f"'{name}' — every {w.get('interval_s', '?')}s, last checked {last}\n"
                     f"  Q: {w.get('question', '')}")
    return "\n".join(lines)
