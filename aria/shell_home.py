"""Shell Home model + command-bar routing for ARIA OS (2026-10-30).

Pure logic behind aria/shell_panel.py — no Qt here, so the home
screen's behaviour is testable headless:

- :func:`greeting_for` — the time-of-day greeting.
- :func:`home_model` — everything the panel paints: greeting,
  clock, date line, the app list, and the count line.
- :func:`route_command` — the command bar's one decision: does
  this text unambiguously name an app (launch intent) or is it
  something to say to ARIA (chat intent, which flows into the
  normal handle_action path with every approval gate unchanged)?

Routing is deliberately conservative. A launch fires only on an
exact name match, or on an explicit open/launch verb plus a name
or unambiguous name-prefix. Everything else is chat — guessing an
app launch from a sentence would make the shell feel possessed,
and a missed guess costs nothing because "open chrome" typed as
chat still reaches ARIA, who has the real tool for it.
"""
from __future__ import annotations

from datetime import datetime

from aria import app_launcher

#: The always-visible escape hatch's label — one string, used by
#: the panel and asserted by the tests so it can never drift.
EXIT_LABEL = "Exit to Windows"


def greeting_for(hour, name="Alek"):
    """Time-of-day greeting for the home header."""
    if 5 <= hour < 12:
        part = "Good morning"
    elif 12 <= hour < 18:
        part = "Good afternoon"
    elif 18 <= hour < 23:
        part = "Good evening"
    else:
        part = "Still up"
    return f"{part}, {name}"


def home_model(apps, now=None, name="Alek", limit=24):
    """The dict the Shell Home panel renders.

    *now* is injectable so screenshots and tests can pin the
    clock. Apps are shown in enumeration order (sorted); the
    panel re-filters through search as he types.
    """
    now = now or datetime.now()
    apps = list(apps or [])
    return {
        "greeting": greeting_for(now.hour, name),
        "clock": now.strftime("%H:%M"),
        "date_line": now.strftime("%A, %B %d"),
        "apps": apps[:limit],
        "app_count": len(apps),
        "count_line": (f"{len(apps)} apps" if len(apps) != 1
                       else "1 app"),
        "exit_label": EXIT_LABEL,
    }


def route_command(text, apps):
    """Classify command-bar text -> an intent dict.

    {'kind': 'launch', 'app': entry, 'text': original} on a
    strong match; {'kind': 'chat', 'text': original} for anything
    else; {'kind': 'empty'} for blank input.
    """
    original = (text or "").strip()
    if not original:
        return {"kind": "empty", "text": ""}
    apps = list(apps or [])
    remainder, had_verb = app_launcher.strip_launch_verb(original)
    if not remainder:
        return {"kind": "chat", "text": original}
    # Exact name match (with or without a verb) launches.
    for app in apps:
        if app_launcher._normalize_name(app.get("name", "")) == \
                remainder:
            return {"kind": "launch", "app": app, "text": original}
    # A verb plus an unambiguous prefix ("open chrom" with only
    # Chrome installed) launches; bare prefixes without a verb
    # stay chat — too easy to hit by accident mid-sentence.
    if had_verb and len(remainder) >= 3:
        matches = [a for a in apps
                   if app_launcher._normalize_name(
                       a.get("name", "")).startswith(remainder)]
        if len(matches) == 1:
            return {"kind": "launch", "app": matches[0],
                    "text": original}
    return {"kind": "chat", "text": original}
