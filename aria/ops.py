"""ARIA OPS command center — data fetchers + cache for the OPS dashboard.

No cv2 dependency: this module only gathers and shapes data. Drawing lives
in aria/hud.py (draw_ops). Every panel fetches independently on its own TTL;
a failed fetch keeps the last good data and marks it stale instead of
blanking the screen.

Data sources (all already in ARIA):
  schedule  <- aria.ical (Google Calendar secret iCal URL, ICAL_URL key)
  inbox     <- aria.tools.builtins.tool_read_email (IMAP, read-only)
  tasks     <- aria.scheduler.sched_list (ARIA's own scheduled jobs)
  systems   <- psutil (optional; panel degrades if missing)
  providers <- aria.agent.providers chain + key quarantine maps
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    import psutil  # type: ignore
except Exception:
    psutil = None  # systems panel degrades gracefully

_TTLS = {
    "schedule": 300,
    "inbox": 300,
    "tasks": 60,
    "systems": 2,
    "providers": 5,
}

_CACHE: Dict[str, Dict[str, Any]] = {}
_REFRESHING: Dict[str, bool] = {}
_LOCK = threading.Lock()

# Rolling history for sparklines (systems panel).
_CPU_HIST: deque = deque(maxlen=30)
_MEM_HIST: deque = deque(maxlen=30)
_LAST_NET = {"t": 0.0, "up": 0, "down": 0}

# Inbox selection (1-8 keys in OPS mode).
SELECTED_MAIL = 0

AUTOMATED_SENDERS = re.compile(
    r"no-?reply|donotreply|alert|notification|newsletter|promo|deals|"
    r"security|support@|info@|marketing",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- cache

def _fetch(name: str):
    fn = {"schedule": fetch_schedule, "inbox": fetch_inbox,
          "tasks": fetch_tasks, "systems": fetch_systems,
          "providers": fetch_providers}[name]
    try:
        data = fn()
        with _LOCK:
            _CACHE[name] = {"at": time.time(), "data": data, "stale": False}
    except Exception as e:
        with _LOCK:
            entry = _CACHE.get(name)
            if entry is not None:
                entry["stale"] = True
                entry["err"] = str(e)[:80]
            else:
                _CACHE[name] = {"at": 0.0, "data": None, "stale": True,
                                "err": str(e)[:80]}
    finally:
        with _LOCK:
            _REFRESHING[name] = False


def _refresh_bg(name: str) -> None:
    with _LOCK:
        if _REFRESHING.get(name):
            return
        _REFRESHING[name] = True
    threading.Thread(target=_fetch, args=(name,), daemon=True).start()


def refresh_all() -> None:
    """Force every panel to refetch in the background."""
    for name in _TTLS:
        _refresh_bg(name)


def get_dashboard() -> Dict[str, Dict[str, Any]]:
    """Return cached panels immediately; kick background refresh for expired."""
    now = time.time()
    for name, ttl in _TTLS.items():
        entry = _CACHE.get(name)
        if entry is None or now - entry.get("at", 0) > ttl:
            _refresh_bg(name)
    with _LOCK:
        return {n: dict(_CACHE.get(n, {"at": 0.0, "data": None,
                                       "stale": True}))
                for n in _TTLS}


# ------------------------------------------------------------- schedule

def fetch_schedule() -> Dict[str, Any]:
    from aria.config import key_get
    from aria.ical import fetch_ical, parse_ical, upcoming

    url = (key_get("ICAL_URL")[0] or "").strip()
    if not url or url == "INSERT":
        return {"connected": False, "events": []}
    events = []
    for e in upcoming(parse_ical(fetch_ical(url)), days=2):
        events.append({
            "summary": e["summary"][:60],
            "start": e["start"], "end": e["end"],
            "all_day": e["all_day"],
        })
    return {"connected": True, "events": events}


# ---------------------------------------------------------------- inbox

def _parse_mail_lines(text: str) -> List[Dict[str, str]]:
    items = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("[uid="):
            continue
        parts = line.split(" | ")
        if len(parts) < 3:
            continue
        m = re.match(r"\[uid=([^\]]+)\]", parts[0])
        sender = re.match(r"^([^<]+)", parts[1])
        items.append({
            "uid": m.group(1) if m else "",
            "sender": (sender.group(1).strip().strip('"')
                       if sender else parts[1])[:28],
            "subject": parts[2].strip().strip('"')[:52],
        })
    return items


def fetch_inbox() -> Dict[str, Any]:
    from aria.tools import builtins

    raw = builtins.tool_read_email(unread_only=True, limit=10) or ""
    if "No Gmail credentials" in raw or "No messages match" in raw:
        return {"connected": "No Gmail credentials" in raw,
                "items": [], "unread": 0}
    items = _parse_mail_lines(raw)
    # Mark Gmail-important via a second cheap query; intersect on uid.
    important_uids = set()
    try:
        imp_raw = builtins.tool_read_email(query="is:important newer_than:2d",
                                           limit=10) or ""
        important_uids = {it["uid"] for it in _parse_mail_lines(imp_raw)}
    except Exception:
        pass
    for it in items:
        auto = bool(AUTOMATED_SENDERS.search(it["sender"] + it["subject"]))
        it["important"] = (it["uid"] in important_uids) or not auto
    items.sort(key=lambda it: (not it["important"],))
    return {"connected": True, "items": items[:8], "unread": len(items)}


def read_mail_body(uid: str, max_chars: int = 600) -> str:
    """Full body of one message, truncated for speech."""
    from aria.tools import builtins
    try:
        body = builtins.tool_read_email(uid=uid) or ""
    except Exception as e:
        return f"Couldn't open that email: {e}"
    text = re.sub(r"\s+", " ", body).strip()
    return text[:max_chars] or "(empty message)"


# ---------------------------------------------------------------- tasks

def fetch_tasks() -> Dict[str, Any]:
    from aria.scheduler import sched_list

    now = time.time()
    items = []
    for tid, kind, prompt, interval_s, next_run in sched_list():
        try:
            nr = float(next_run)
        except (TypeError, ValueError):
            continue
        delta = nr - now
        items.append({
            "id": tid, "kind": kind or "once",
            "prompt": (prompt or "")[:48],
            "next_run": nr, "delta_s": delta,
            "overdue": delta < 0,
            "recurring": bool(interval_s),
        })
    items.sort(key=lambda it: it["next_run"])
    return {"items": items[:8],
            "overdue": sum(1 for it in items if it["overdue"])}


def fmt_countdown(delta_s: float) -> str:
    if delta_s < 0:
        return "OVERDUE"
    if delta_s < 3600:
        return f"in {int(delta_s // 60)}m"
    if delta_s < 86400:
        h = int(delta_s // 3600)
        return f"in {h}h{int((delta_s % 3600) // 60):02d}"
    return f"in {int(delta_s // 86400)}d"


# -------------------------------------------------------------- systems

def fetch_systems() -> Dict[str, Any]:
    if psutil is None:
        return {"available": False}
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\" if hasattr(psutil, "win_service_get")
                             else "/")
    _CPU_HIST.append(cpu)
    _MEM_HIST.append(mem.percent)

    now = time.time()
    net = psutil.net_io_counters()
    up_kbs = down_kbs = 0.0
    if _LAST_NET["t"]:
        dt = max(0.5, now - _LAST_NET["t"])
        up_kbs = (net.bytes_sent - _LAST_NET["up"]) / dt / 1024
        down_kbs = (net.bytes_recv - _LAST_NET["down"]) / dt / 1024
    _LAST_NET.update(t=now, up=net.bytes_sent, down=net.bytes_recv)

    batt = None
    try:
        b = psutil.sensor_battery() if hasattr(psutil, "sensors_battery") else None
        if b is not None:
            batt = {"pct": int(b.percent), "plugged": bool(b.power_plugged)}
    except Exception:
        pass

    return {
        "available": True,
        "cpu": cpu, "mem": mem.percent, "disk": disk.percent,
        "up_kbs": up_kbs, "down_kbs": down_kbs,
        "uptime_s": now - psutil.boot_time(),
        "batt": batt,
        "cpu_hist": list(_CPU_HIST), "mem_hist": list(_MEM_HIST),
    }


def fmt_uptime(s: float) -> str:
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    return f"{d}d{h}h" if d else (f"{h}h{m:02d}m" if h else f"{m}m")


# ------------------------------------------------------------ providers

def fetch_providers() -> Dict[str, Any]:
    from aria.agent import providers as pv
    from aria import config as cfg

    now = time.time()
    chain_info = []
    for p in pv._build_chain():
        avail = p.is_available()
        q_until = cfg._KEY_QUARANTINE_UNTIL.get(getattr(p, "api_key", ""), 0)
        state = "live" if avail else "standby"
        detail = ""
        if q_until > now:
            code = cfg._KEY_QUARANTINE_CODE.get(getattr(p, "api_key", ""), "")
            mins = max(1, int((q_until - now) // 60))
            state, detail = "quarantined", f"{code} ~{mins}m"
        chain_info.append({"name": p.name, "state": state, "detail": detail,
                           "model": getattr(p, "model", "")})
    stats = dict(getattr(pv, "PROVIDER_STATS",
                         {"failovers": 0, "calls": {}, "last_failover": None}))
    return {"active": pv.get_active_provider(), "chain": chain_info,
            "stats": stats}


# ------------------------------------------------------------ attention

def compute_attention(dash: Dict[str, Dict[str, Any]],
                      now: Optional[datetime] = None) -> List[str]:
    """Top urgent items across panels. Max 3, plain strings for the HUD."""
    now = now or datetime.now()
    out: List[str] = []

    evs = ((dash.get("schedule") or {}).get("data") or {}).get("events", [])
    soon = [e for e in evs
            if not e["all_day"] and 0 < (e["start"] - now).total_seconds() < 3 * 3600]
    if soon:
        e = soon[0]
        mins = int((e["start"] - now).total_seconds() // 60)
        h, m = divmod(mins, 60)
        when = f"{h}h{m:02d}m" if h else f"{m}m"
        out.append(f"{e['summary'][:32]} in {when}")

    inbox = (dash.get("inbox") or {}).get("data") or {}
    imp = [i for i in inbox.get("items", []) if i.get("important")]
    if imp:
        out.append(f"{len(imp)} important unread")

    prov = (dash.get("providers") or {}).get("data") or {}
    if prov.get("active") and prov["active"] != "gemini":
        out.append(f"on {prov['active']} (fallback)")

    tasks = (dash.get("tasks") or {}).get("data") or {}
    if tasks.get("overdue"):
        out.append(f"{tasks['overdue']} overdue task(s)")

    sysd = (dash.get("systems") or {}).get("data") or {}
    if sysd.get("available"):
        if sysd.get("disk", 0) > 90:
            out.append(f"disk {sysd['disk']:.0f}% full")
        b = sysd.get("batt")
        if b and not b["plugged"] and b["pct"] < 20:
            out.append(f"battery {b['pct']}%")

    return out[:3]


# ------------------------------------------------------------- timeline

def compute_timeline(events: List[Dict[str, Any]],
                     now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Blocks for the 'rest of today' bar. x0/x1 are 0..1 across now->midnight."""
    now = now or datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0,
                           microsecond=0) + timedelta(days=1)
    span = max(1.0, (midnight - now).total_seconds())
    blocks = []
    for e in events:
        if e.get("all_day"):
            continue
        s = max(e["start"], now)
        en = min(e["end"], midnight)
        if en <= s:
            continue
        blocks.append({
            "label": e["summary"][:24],
            "x0": (s - now).total_seconds() / span,
            "x1": (en - now).total_seconds() / span,
            "active": e["start"] <= now <= e["end"],
        })
    return blocks
