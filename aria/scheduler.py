"""
ARIA Scheduler Subsystem.
Manages one-shot reminders, timers, recurring tasks, morning briefings,
work schedule integration, and break nudges.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Optional, Callable, List, Tuple, Dict, Any

from aria.config import WORKSPACE_DIR, ROOT_DIR, add_log
from aria.memory import DB_LOCK, DB_PATH
import aria.speech as speech

SCHEDULE_FILE = os.path.join(ROOT_DIR, "aria_schedule.json")
BREAK_REMINDERS = True
_SCHED_COUNT_HOOK: Optional[Callable[[int], None]] = None

_SCHEDULE_SEED = [
    ("2026-09-27", "14:00", "18:30", "Dock of the Bay — Bar"),
    ("2026-09-28", "10:30", "11:30", "Smiles R Us dentist"),
    ("2026-09-28", "15:00", "19:30", "Dock of the Bay — Wait"),
    ("2026-09-29", "16:00", "20:30", "Dock of the Bay — Wait"),
    ("2026-10-03", "16:00", "20:30", "Dock of the Bay — Bar"),
    ("2026-10-05", "15:00", "21:00", "Dock of the Bay — Wait"),
    ("2026-10-06", "16:00", "21:00", "Dock of the Bay — Wait"),
    ("2026-10-07", "16:00", "21:00", "Dock of the Bay — Wait"),
    ("2026-10-09", "12:00", "21:00", "Dock of the Bay — Wait (bartender from 4 PM)"),
    ("2026-10-10", "11:30", "17:00", "Dock of the Bay — Wait"),
    ("2026-10-11", "11:30", "17:00", "Dock of the Bay — Wait"),
    ("2026-10-12", "15:00", "21:00", "Dock of the Bay — Wait"),
    ("2026-10-14", "16:00", "21:00", "Dock of the Bay — Wait"),
    ("2026-10-17", "15:00", "21:00", "Dock of the Bay — Wait"),
    ("2026-10-19", "15:00", "21:00", "Dock of the Bay — Wait"),
    ("2026-10-20", "11:30", "17:00", "Dock of the Bay — Wait"),
    ("2026-10-21", "11:30", "17:00", "Dock of the Bay — Wait"),
    ("2026-10-22", "11:30", "17:00", "Dock of the Bay — Wait"),
    ("2026-10-23", "11:30", "17:00", "Dock of the Bay — Wait"),
    ("2026-10-23", "22:00", "23:59", "Poe Speakeasy, Annapolis (2 tickets)"),
    ("2026-10-24", "14:00", "21:00", "Dock of the Bay — Wait"),
    ("2026-10-25", "16:00", "21:00", "Dock of the Bay — Wait"),
    ("2026-10-26", "15:00", "21:00", "Dock of the Bay — Wait"),
    ("2026-10-28", "16:00", "21:00", "Dock of the Bay — Wait"),
    ("2026-11-20", "19:30", "23:00", "Doja Cat — Tour Ma Vie, CFG Bank Arena Baltimore"),
    ("2026-12-02", "10:30", "11:30", "Smiles R Us dentist follow-up"),
]

_BRIEF_PROMPT_RE = re.compile(r"morning\s*brief", re.IGNORECASE)


def set_sched_count_hook(fn: Callable[[int], None]):
    global _SCHED_COUNT_HOOK
    _SCHED_COUNT_HOOK = fn


def _bump_sched_count(delta: int):
    if _SCHED_COUNT_HOOK:
        try:
            _SCHED_COUNT_HOOK(delta)
        except Exception:
            pass


def _load_schedule() -> List[Dict[str, str]]:
    if not os.path.exists(SCHEDULE_FILE):
        try:
            entries = [{"date": d, "start": s, "end": e, "summary": sum_}
                       for d, s, e, sum_ in _SCHEDULE_SEED]
            with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2)
            return entries
        except Exception:
            return []
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _today_entries() -> List[Dict[str, str]]:
    today = datetime.now().strftime("%Y-%m-%d")
    return sorted([e for e in _load_schedule() if e.get("date") == today],
                  key=lambda e: e.get("start", ""))


def _fmt_time(t: str) -> str:
    try:
        h, m = [int(x) for x in t.split(":")]
        suffix = "PM" if h >= 12 else "AM"
        h = h % 12 or 12
        return f"{h}:{m:02d} {suffix}" if m else f"{h} {suffix}"
    except Exception:
        return t


def _entries_line(entries: List[Dict[str, str]]) -> str:
    parts = []
    for e in entries:
        st = _fmt_time(e.get("start", ""))
        en = _fmt_time(e.get("end", ""))
        tm = f"{st} to {en}" if st and en else (st or en)
        sm = e.get("summary", "event")
        parts.append(f"{sm} ({tm})" if tm else sm)
    return ", ".join(parts)


def _live_entries(days: int = 2) -> Tuple[List[Dict[str, str]], bool]:
    """Today's+upcoming entries from the live iCal feed.

    Returns (entries, True) when a calendar is connected and fetched;
    ([], False) otherwise so callers fall back to the saved schedule.
    Entry shape matches the seed: {date, start, end, summary} with
    local HH:MM times (empty start/end for all-day events).
    """
    try:
        from aria.ical import fetch_ical, parse_ical, upcoming
        from aria.config import key_get
        url = (key_get("ICAL_URL")[0] or "").strip()
        if not url or url == "INSERT":
            return [], False
        entries = []
        for e in upcoming(parse_ical(fetch_ical(url)), days=days):
            s, en = e["start"], e["end"]
            entries.append({
                "date": s.strftime("%Y-%m-%d"),
                "start": "" if e["all_day"] else s.strftime("%H:%M"),
                "end": "" if e["all_day"] else en.strftime("%H:%M"),
                "summary": ("(all day) " if e["all_day"] else "") + e["summary"],
            })
        return sorted(entries, key=lambda e: (e["date"], e.get("start", ""))), True
    except Exception as ex:
        add_log(f"Live calendar failed, using saved schedule: {ex}")
        return [], False


def _weather_full() -> Optional[Dict[str, str]]:
    try:
        req = urllib.request.Request(
            "https://wttr.in/Dundalk,Maryland?format=j1",
            headers={"User-Agent": "ARIA-Agent/8.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8", errors="replace"))
        cur = d["current_condition"][0]
        day = d["weather"][0]
        desc = (cur.get("weatherDesc") or [{}])[0].get("value", "").strip()
        return {
            "temp_F": str(cur.get("temp_F", "")).strip(),
            "desc": desc,
            "high_F": str(day.get("maxtempF", "")).strip(),
            "low_F": str(day.get("mintempF", "")).strip()
        }
    except Exception:
        return None


def _weather_now() -> str:
    try:
        req = urllib.request.Request(
            "https://wttr.in/Dundalk,Maryland?format=%C+%t",
            headers={"User-Agent": "ARIA-Agent/8.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _email_brief_section() -> str:
    """Summarizes unread emails and notable items needing attention for the brief."""
    try:
        from aria.tools import builtins
        res = builtins.tool_read_email(unread_only=True, limit=10)
        if not res or "No messages match" in res or "No Gmail credentials" in res or "failed" in res.lower():
            return " Inbox is clear."
        lines = [l.strip() for l in res.split("\n") if l.strip().startswith("[uid=")]
        if not lines:
            return " Inbox is clear."

        actionable = []
        for l in lines:
            parts = l.split(" | ")
            if len(parts) >= 3:
                sender = parts[1]
                name_match = re.match(r"^([^<]+)", sender)
                sender_name = name_match.group(1).strip().strip('"') if name_match else sender
                subj = parts[2].strip('"')
                if "security alert" in subj.lower() and "google" in sender_name.lower():
                    continue
                actionable.append(f"{sender_name} regarding '{subj}'")

        count = len(lines)
        if not actionable:
            return f" You have {count} unread email{'s' if count != 1 else ''}, mostly automated alerts."

        first_few = "; ".join(actionable[:2])
        rest = f" and {count - len(actionable[:2])} other(s)" if count > len(actionable[:2]) else ""
        return f" On email: {count} unread — notable: {first_few}{rest}."
    except Exception:
        return ""


def tool_briefing() -> str:
    now = datetime.now()
    day = now.strftime("%A, %B %d")
    live_entries, is_live = _live_entries(2)
    if is_live:
        today_s = now.strftime("%Y-%m-%d")
        tmrw_s = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        entries = [e for e in live_entries if e.get("date") == today_s]
        t_entries = [e for e in live_entries if e.get("date") == tmrw_s]
    else:
        entries = _today_entries()
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        t_entries = sorted([e for e in _load_schedule() if e.get("date") == tomorrow],
                           key=lambda e: e.get("start", ""))
    sched = f"You've got: {_entries_line(entries)}." if entries else "Nothing on the schedule today."
    tmrw = f" Tomorrow: {_entries_line(t_entries)}." if t_entries else ""
    ebit = _email_brief_section()
    wf = _weather_full()
    if wf and wf.get("temp_F"):
        cond = f" and {wf['desc']}" if wf.get("desc") else ""
        wxbit = f" In Dundalk it's {wf['temp_F']} degrees{cond}, with a high of {wf['high_F']} and a low of {wf['low_F']}."
    else:
        wn = _weather_now()
        wxbit = f" In Dundalk it's {wn}." if wn else ""
    return f"Good morning, Alek. Today is {day}. {sched}{tmrw}{ebit}{wxbit}"


def sched_add(kind: str, prompt: str, delay_s: int = 0, interval_s: int = 0) -> int:
    nxt = datetime.now() + timedelta(seconds=delay_s if kind == "once" else interval_s)
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""INSERT INTO scheduled_tasks (kind, prompt, interval_s, next_run, created)
                       VALUES (?, ?, ?, ?, ?)""",
                    (kind, prompt, interval_s, nxt.strftime("%Y-%m-%d %H:%M:%S"),
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        tid = cur.lastrowid
        conn.commit()
        conn.close()
    _bump_sched_count(1)
    return tid


def sched_list() -> List[Tuple[Any, ...]]:
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, kind, prompt, interval_s, next_run FROM scheduled_tasks ORDER BY next_run")
        rows = cur.fetchall()
        conn.close()
    return rows


def sched_cancel(task_id: int) -> bool:
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        n = conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,)).rowcount
        conn.commit()
        conn.close()
    if n > 0:
        _bump_sched_count(-1)
    return n > 0


def tool_set_reminder(delay_seconds: int, message: str) -> str:
    tid = sched_add("once", message, delay_s=max(1, int(delay_seconds)))
    add_log(f"Reminder #{tid} in {delay_seconds}s")
    return f"Reminder set (#{tid}): I'll say '{message}' in {delay_seconds} seconds."


def tool_set_recurring(interval_seconds: int, prompt: str) -> str:
    kind = "brief" if _BRIEF_PROMPT_RE.search(prompt or "") else "interval"
    tid = sched_add(kind, prompt, interval_s=max(60, int(interval_seconds)))
    add_log(f"Recurring task #{tid} every {interval_seconds}s (kind={kind})")
    if kind == "brief":
        return f"Morning-brief task set (#{tid}): I'll speak your morning briefing every {interval_seconds} seconds."
    return f"Recurring task set (#{tid}): '{prompt}' every {interval_seconds} seconds."


def tool_list_scheduled() -> str:
    rows = sched_list()
    if not rows:
        return "No scheduled tasks."
    lines = [f"#{r[0]} [{r[1]}] next: {r[4]} | {r[2]}" for r in rows]
    return "\n".join(lines)


def tool_cancel_scheduled(task_id: int) -> str:
    return f"Cancelled task #{task_id}." if sched_cancel(int(task_id)) else f"No task #{task_id} found."


def _parse_duration(s: str) -> int:
    s = str(s).lower().strip()
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)(?![a-zA-Z])", s):
        n = float(num)
        u = unit[0]
        total += n * 3600 if u == "h" else n * 60 if u == "m" else n
    if total == 0 and s.replace(".", "", 1).isdigit():
        total = float(s) * 60
    return int(total)


def tool_set_timer(duration_text: str, label: str = "timer") -> str:
    secs = _parse_duration(duration_text)
    if secs <= 0:
        return "[I couldn't understand that duration - try '20 minutes' or '1h30m'.]"

    def _fire():
        add_log(f"Timer fired: {label}")
        speech.speak(f"Timer done: {label}.")

    threading.Timer(secs, _fire).start()
    return f"Timer set: {label}, {duration_text} from now."


def tool_break_reminders(enabled: Any = True) -> str:
    global BREAK_REMINDERS
    BREAK_REMINDERS = bool(enabled) if isinstance(enabled, bool) else str(enabled).lower() in ("1", "true", "on", "yes")
    return f"Break reminders {'enabled' if BREAK_REMINDERS else 'disabled'}."


def _fire_brief(tid: int, interval_s: int, is_busy_fn: Optional[Callable[[], bool]] = None):
    nxt = datetime.now() + timedelta(seconds=interval_s or 3600)
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE scheduled_tasks SET next_run = ?, kind = 'brief' WHERE id = ?",
                     (nxt.strftime("%Y-%m-%d %H:%M:%S"), tid))
        conn.commit()
        conn.close()
    text = tool_briefing()
    add_log(f"Morning brief spoken (#{tid})")
    busy = is_busy_fn() if is_busy_fn else False
    if not busy:
        speech.speak(text)
    else:
        speech._SPEECH_QUEUE.put(text)


# OPS Controls tab: pause/resume the background scheduler without restarting.
PAUSED = False


def set_paused(v: bool) -> bool:
    global PAUSED
    PAUSED = bool(v)
    return PAUSED


def is_paused() -> bool:
    return PAUSED


def scheduler_loop(is_busy_fn: Optional[Callable[[], bool]] = None,
                   run_agent_fn: Optional[Callable[[str], None]] = None):
    """Background scheduler worker thread."""
    time.sleep(5)
    while True:
        if PAUSED:
            time.sleep(5)
            continue
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            due = []
            with DB_LOCK:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT id, kind, prompt, interval_s FROM scheduled_tasks WHERE next_run <= ?", (now,))
                due = cur.fetchall()
                conn.close()

            for tid, kind, prompt, interval_s in due:
                if kind == "once":
                    with DB_LOCK:
                        conn = sqlite3.connect(DB_PATH)
                        conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (tid,))
                        conn.commit()
                        conn.close()
                    _bump_sched_count(-1)
                    add_log(f"Reminder #{tid} firing")
                    try:  # Module 5 event bus: never break scheduling
                        from aria.events import emit_reminder_fired
                        emit_reminder_fired(tid, prompt)
                    except Exception:
                        pass
                    busy = is_busy_fn() if is_busy_fn else False
                    if not busy:
                        speech.speak(prompt)
                    else:
                        speech._SPEECH_QUEUE.put(prompt)
                elif kind == "brief" or _BRIEF_PROMPT_RE.search(prompt or ""):
                    _fire_brief(tid, interval_s, is_busy_fn)
                elif kind == "screenwatch":
                    # ARIA ULTIMATE (Module 7): reschedule, then run the check.
                    nxt = datetime.now() + timedelta(seconds=interval_s or 300)
                    with DB_LOCK:
                        conn = sqlite3.connect(DB_PATH)
                        conn.execute("UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
                                     (nxt.strftime("%Y-%m-%d %H:%M:%S"), tid))
                        conn.commit(); conn.close()
                    try:
                        from aria.screenwatch import check_watch
                        check_watch(prompt or "")
                    except Exception as _sw_err:
                        add_log(f"Screenwatch check failed: {_sw_err}")
                elif kind == "pricecheck":
                    # ARIA ULTIMATE: hourly price-watch check pass.
                    nxt = datetime.now() + timedelta(seconds=interval_s or 3600)
                    with DB_LOCK:
                        conn = sqlite3.connect(DB_PATH)
                        conn.execute("UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
                                     (nxt.strftime("%Y-%m-%d %H:%M:%S"), tid))
                        conn.commit(); conn.close()
                    try:
                        from aria import pricecheck as _pricecheck_mod
                        add_log(_pricecheck_mod.check_price_watches())
                    except Exception as _pc_err:
                        add_log(f"Pricecheck failed: {_pc_err}")
                else:
                    nxt = datetime.now() + timedelta(seconds=interval_s or 3600)
                    with DB_LOCK:
                        conn = sqlite3.connect(DB_PATH)
                        conn.execute("UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
                                     (nxt.strftime("%Y-%m-%d %H:%M:%S"), tid))
                        conn.commit()
                        conn.close()
                    add_log(f"Recurring task #{tid} firing")
                    try:  # Module 5 event bus: never break scheduling
                        from aria.events import emit_task_fired
                        emit_task_fired(tid, "interval", prompt, interval_s)
                    except Exception:
                        pass
                    if run_agent_fn:
                        threading.Thread(target=run_agent_fn,
                                         args=(f"[Scheduled task] {prompt}",),
                                         daemon=True).start()

            # ARIA ULTIMATE (Module 5): inbox poller — emits inbox_new_file
            # events for new inbox arrivals. Never break scheduling.
            try:
                from aria.events import poll_inbox
                poll_inbox()
            except Exception:
                pass
        except Exception as e:
            add_log(f"Scheduler err: {e}")
        time.sleep(10)
