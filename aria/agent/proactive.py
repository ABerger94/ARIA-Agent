"""
ARIA Proactive Heartbeat, Decline Learning, Mood System, and Idle Consolidation.
Manages autonomous ambient awareness, habit tracking, and memory distillation.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from typing import Optional, Callable, Dict, Any, Tuple

import psutil

from aria.config import WORKSPACE_DIR, add_log
from aria.memory import DB_LOCK, DB_PATH, spine_append, spine_tail, spine_digest, memory_save
from aria.scheduler import BREAK_REMINDERS

_HEARTBEAT_MEM_FILE = os.path.join(WORKSPACE_DIR, "heartbeat_memory.json")
_MOOD_FILE = os.path.join(WORKSPACE_DIR, "mood.json")
_LAST_PROACTIVE = [None, 0.0]

_DISMISS_RE = re.compile(
    r"\b(shut up|not now|leave me alone|go away|be quiet|cut it out|not interested)\b"
    r"|^(no|nope|nah|stop|quiet|enough|later)\b", re.IGNORECASE
)


def _heartbeat_mem_load() -> Dict[str, Any]:
    try:
        with open(_HEARTBEAT_MEM_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _heartbeat_mem_save(mem: Dict[str, Any]):
    try:
        with open(_HEARTBEAT_MEM_FILE, "w", encoding="utf-8") as f:
            json.dump(mem, f, indent=2)
    except Exception:
        pass


def looks_like_decline(user_text: str) -> bool:
    t = (user_text or "").strip()
    if len(t) > 60:
        return False
    return bool(_DISMISS_RE.search(t))


def record_decline(nudge_type: str):
    mem = _heartbeat_mem_load()
    entry = mem.get(nudge_type, {})
    declines = entry.get("declines", 0) + 1
    quiet_days = min(2 ** (declines - 1), 7)
    entry.update({
        "declines": declines,
        "last_decline": datetime.now().isoformat(timespec="seconds"),
        "suppressed_until": time.time() + quiet_days * 86400
    })
    mem[nudge_type] = entry
    _heartbeat_mem_save(mem)
    add_log(f"Heartbeat: '{nudge_type}' declined ({declines}x) - quiet {quiet_days}d")


def proactive_say(nudge_type: str, text: str, speak_fn: Callable[[str], None],
                  is_busy_fn: Callable[[], bool], is_whisper_fn: Callable[[], bool]) -> bool:
    mem = _heartbeat_mem_load()
    entry = mem.get(nudge_type, {})
    if entry.get("suppressed_until", 0) > time.time():
        add_log(f"Heartbeat: '{nudge_type}' nudge suppressed (declined before)")
        return False
    if is_whisper_fn() and nudge_type != "battery":
        add_log(f"Heartbeat: '{nudge_type}' nudge held (whisper mode)")
        return False
    if not is_busy_fn():
        speak_fn(text)
        _LAST_PROACTIVE[0] = nudge_type
        _LAST_PROACTIVE[1] = time.time()
        return True
    return False


def proactive_heartbeat_loop(speak_fn: Callable[[str], None],
                             is_busy_fn: Callable[[], bool],
                             is_whisper_fn: Callable[[], bool],
                             get_last_activity_fn: Callable[[], float]):
    time.sleep(10)
    last_battery_alert = False
    session_start = time.time()
    while True:
        try:
            battery = psutil.sensors_battery()
            if battery and not battery.power_plugged and battery.percent < 20 and not last_battery_alert:
                last_battery_alert = True
                proactive_say("battery", f"Allen, battery level is at {battery.percent}%. Please connect to AC power.",
                              speak_fn, is_busy_fn, is_whisper_fn)
            elif battery and battery.power_plugged:
                last_battery_alert = False

            if BREAK_REMINDERS and (time.time() - session_start) / 3600 >= 1.5:
                session_start = time.time()
                if (time.time() - get_last_activity_fn()) < 5400:
                    proactive_say("break", "You have been at it a while, stretch and get some water.",
                                  speak_fn, is_busy_fn, is_whisper_fn)
        except Exception as e:
            add_log(f"Heartbeat err: {e}")
        time.sleep(30)


# --- Mood system ---
def _mood_energy_baseline(h: float) -> float:
    if 0 <= h < 6:
        return 15.0 + (h / 6.0) * 15.0
    if 6 <= h < 11:
        return 30.0 + ((h - 6) / 5.0) * 55.0
    if 11 <= h < 14:
        return 85.0
    if 14 <= h < 17:
        return 85.0 - ((h - 14) / 3.0) * 20.0
    if 17 <= h < 22:
        return 65.0
    return 65.0 - ((h - 22) / 2.0) * 50.0


def _mood_load() -> Dict[str, Any]:
    try:
        with open(_MOOD_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _mood_save(d: Dict[str, Any]):
    try:
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        with open(_MOOD_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


def mood_state(now: Optional[float] = None) -> Tuple[float, float]:
    now = now if now is not None else time.time()
    dt = datetime.now()
    energy = _mood_energy_baseline(dt.hour + dt.minute / 60.0)
    d = _mood_load()
    warmth = float(d.get("warmth", 50.0))
    idle_h = max(0.0, (now - float(d.get("updated_at", now))) / 3600.0)
    warmth = max(0.0, warmth - 5.0 * idle_h)
    return energy, warmth


def mood_note_interaction(now: Optional[float] = None) -> Tuple[float, float]:
    now = now if now is not None else time.time()
    energy, warmth = mood_state(now=now)
    warmth = min(100.0, warmth + 8.0)
    _mood_save({"warmth": warmth, "updated_at": now, "last_interaction": now})
    return energy, warmth


def mood_word(energy: Optional[float] = None, warmth: Optional[float] = None,
              last_interaction: Optional[float] = None, now: Optional[float] = None) -> str:
    now = now if now is not None else time.time()
    if energy is None or warmth is None:
        energy, warmth = mood_state(now=now)
    if last_interaction is None:
        last_interaction = float(_mood_load().get("last_interaction", 0.0))
    recent_banter = (now - last_interaction) < 600
    if energy < 35:
        return "sleepy"
    if warmth < 35:
        return "quiet"
    if energy >= 65 and warmth >= 65 and recent_banter:
        return "playful"
    if energy >= 65 and warmth >= 65:
        return "bright"
    return "calm"


# --- Idle memory consolidation ---
def _consolidation_watermark() -> int:
    try:
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            r = cur.execute("SELECT value FROM memory WHERE category='system' AND key='_consolidation_wm'").fetchone()
            conn.close()
        return int(r[0]) if r else 0
    except Exception:
        return 0


def _set_consolidation_watermark(chat_id: int):
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO memory(category,key,value) VALUES('system','_consolidation_wm',?)",
                     (str(chat_id),))
        conn.commit()
        conn.close()


def idle_consolidation_loop(gemini_text_fn: Callable[[str, Any], str],
                            is_busy_fn: Callable[[], bool],
                            get_last_activity_fn: Callable[[], float]):
    time.sleep(300)
    while True:
        time.sleep(1800)
        try:
            if is_busy_fn():
                continue
            if time.time() - get_last_activity_fn() < 1800:
                continue

            wm = _consolidation_watermark()
            with DB_LOCK:
                conn = sqlite3.connect(DB_PATH)
                rows = conn.execute("SELECT id,sender,message FROM chat_history WHERE id>? ORDER BY id LIMIT 40",
                                    (wm,)).fetchall()
                conn.close()

            if not rows:
                continue

            convo = "\n".join(f"{s}: {m[:300]}" for _, s, m in rows)
            spine_lines = spine_digest(spine_tail(100))
            if spine_lines:
                convo = convo + "\n[recent events]\n" + "\n".join(spine_lines)

            system_instruction = (
                "You distill durable memories from a conversation log. Reply with 0-3 lines, "
                "each exactly 'key: value' — a lasting fact, preference, or commitment about the user. "
                "Skip small talk. If nothing durable, reply with the single word NONE."
            )
            contents = [{"role": "user", "parts": [{"text": convo}]}]
            text = gemini_text_fn(system_instruction, contents)

            last_id = rows[-1][0]
            _set_consolidation_watermark(last_id)

            if text.strip().upper() == "NONE":
                add_log("Idle consolidation: no durable memories found.")
                continue

            count = 0
            for line in text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k, v = k.strip().lower().replace(" ", "_"), v.strip()
                    if k and v:
                        memory_save("consolidated", k, v)
                        count += 1
            if count:
                add_log(f"Idle consolidation: distilled {count} durable memories.")
        except Exception as e:
            add_log(f"Consolidation err: {e}")
