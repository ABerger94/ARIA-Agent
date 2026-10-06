"""ARIA ULTIMATE — price-drop checker (fills the Module 5 producer gap).

`tool_watch_price` advertises "Checks run hourly" but nothing ever checked.
This module provides the missing checker:

- `check_price_watches()` — one pass over the price_watches DB: fetches each
  URL (best-effort text extraction), compares against the target, emits a
  `price_drop` event on the bus when a target is hit, and marks the watch
  alerted so it only fires once per drop.
- `ensure_pricecheck_task()` — idempotent: if any watches exist and no
  `pricecheck` scheduler task exists, creates one (hourly).
- `tool_check_price_watches()` — builtins-style manual trigger.

Top-level imports: stdlib + aria.config only. builtins/events/scheduler are
imported lazily inside functions (no import cycles).
"""

import re
import sqlite3

from aria.config import PRICE_WATCH_DB, add_log, redact
from aria import config as _config_silent

# JSON-LD product markup is the most reliable signal; fall back to $ amounts.
_JSONLD_PRICE_RE = re.compile(r'"price"\s*:\s*"?([0-9][0-9,]*\.[0-9]{2})"?')
_DOLLAR_PRICE_RE = re.compile(r'\$\s*([0-9][0-9,]*\.[0-9]{2})')


def extract_price(text: str):
    """Best-effort price extraction. Returns float or None."""
    if not text:
        return None
    m = _JSONLD_PRICE_RE.search(text)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    prices = []
    for m in _DOLLAR_PRICE_RE.finditer(text):
        try:
            prices.append(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
    return min(prices) if prices else None


def check_price_watches() -> str:
    """Run one check pass over all price watches. Returns a summary string."""
    from aria.tools import builtins as _builtins
    from aria import events as _events

    try:
        conn = sqlite3.connect(PRICE_WATCH_DB)
        rows = conn.execute(
            "SELECT id, label, url, target, last_price, alerted FROM price_watches"
        ).fetchall()
    except Exception as e:
        return f"[price check failed: {redact(str(e))}]"
    if not rows:
        return "[No price watches to check]"

    checked, alerts = 0, []
    for wid, label, url, target, _last, alerted in rows:
        checked += 1
        try:
            text = _builtins.tool_fetch_url(url or "")
            price = extract_price(text or "")
        except Exception as e:
            add_log(f"Price check #{wid} ({label}) fetch failed: {redact(str(e))}")
            continue
        if price is None:
            add_log(f"Price check #{wid} ({label}): no price found on page")
            continue
        try:
            with sqlite3.connect(PRICE_WATCH_DB) as db:
                db.execute("UPDATE price_watches SET last_price=? WHERE id=?", (price, wid))
            if price <= float(target) and not alerted:
                _events.emit_price_drop(label, target, price, url)
                with sqlite3.connect(PRICE_WATCH_DB) as db:
                    db.execute("UPDATE price_watches SET alerted=1 WHERE id=?", (wid,))
                alerts.append(f"DROP: '{label}' now ${price:.2f} (target ${float(target):.2f}) — {url}")
                add_log(f"Price drop alert #{wid}: {label} ${price:.2f}")
        except Exception as e:
            add_log(f"Price check #{wid} ({label}) failed: {redact(str(e))}")
    try:
        conn.close()
    except Exception as _e_silent:
        _config_silent.log_silent("check_price_watches", _e_silent)
    summary = f"Checked {checked} price watch(es)."
    if alerts:
        summary += "\n" + "\n".join(alerts)
    return summary


def ensure_pricecheck_task() -> bool:
    """Create the hourly `pricecheck` scheduler task if watches exist and no
    such task is scheduled. Returns True if a task was created."""
    from aria import scheduler as _scheduler

    try:
        with sqlite3.connect(PRICE_WATCH_DB) as db:
            n_watches = db.execute("SELECT COUNT(*) FROM price_watches").fetchone()[0]
        if not n_watches:
            return False
        kinds = [r[1] for r in _scheduler.sched_list()]
        if "pricecheck" in kinds:
            return False
        _scheduler.sched_add("pricecheck", "pricecheck", interval_s=3600)
        add_log("Pricecheck: hourly price-watch task scheduled")
        return True
    except Exception as e:
        add_log(f"Pricecheck task setup failed: {redact(str(e))}")
        return False


def tool_check_price_watches() -> str:
    """Builtins-style tool: run one price-watch check pass now."""
    return check_price_watches()
