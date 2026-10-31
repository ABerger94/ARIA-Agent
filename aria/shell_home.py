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


# ------------------------------------------------------------------
# V2 presentation models (2026-10-30): tile grid + rail cards.
# Still pure — the panel gathers real values through the seams
# (aria.briefing.latest_briefing, aria.smtc.now_playing_any,
# aria.config / aria.approval) and these formatters decide what
# the cards say. No seam, no data: an honest empty state, never
# a plausible-looking fabrication.
# ------------------------------------------------------------------

#: Letter-tile palette (mock family) — picked deterministically
#: from the app name so a tile's colour is stable between runs.
TILE_COLORS = ("#57c7d4", "#8fb8c9", "#7fd08a", "#c9a2e0",
               "#e0a2b0", "#d8c58a", "#7aa8e0", "#a8d08a")


def tile_model(app):
    """The tile the grid paints for one app entry.

    A letter tile (initial + stable colour) is the universal
    fallback; on Windows the panel layers the real shortcut icon
    over it when Qt's icon provider has one.
    """
    name = str((app or {}).get("name", "") or "?").strip() or "?"
    letter = name[0].upper()
    color = TILE_COLORS[sum(name.lower().encode()) % len(TILE_COLORS)]
    return {"name": name, "letter": letter, "color": color,
            "path": (app or {}).get("path", "")}


def briefing_card(report):
    """Briefing rail card from briefing.latest_briefing().

    *report* is the last composed briefing dict (or None when
    ARIA hasn't composed one yet this session). Lines are the
    report's own section texts, COMPLETE and untruncated —
    schedule first, then every remaining section in order —
    never invented content. (V3: the V2 card capped this at
    three sections and Alek's witnessed briefing clipped
    mid-sentence; the card now scrolls instead of cutting.)
    """
    if not report:
        return {"title": "Briefing", "empty": True,
                "lines": ["No briefing yet — ARIA composes one "
                          "at startup."]}
    sections = [s for s in (report.get("sections") or [])
                if isinstance(s, dict) and s.get("text")]
    sections.sort(key=lambda s: 0 if "schedule" in
                  str(s.get("label", "")).lower() else 1)
    lines = [str(s["text"]) for s in sections]
    if not lines:
        text = str(report.get("text") or "").strip()
        lines = [text] if text else []
    if not lines:
        return {"title": "Briefing", "empty": True,
                "lines": ["Briefing is empty right now."]}
    return {"title": "Briefing", "empty": False, "lines": lines}


def now_playing_card(info):
    """Now Playing rail card from smtc.now_playing_any().

    *info* is the SMTC session dict (title/track/artist/app/
    playing) or None when no session exists — the card then
    says so, plainly.
    """
    if not info:
        return {"title": "Now Playing", "empty": True,
                "lines": ["Nothing playing"]}
    title = str(info.get("title") or info.get("track") or "").strip()
    if not title:
        artist = str(info.get("artist") or "").strip()
        track = str(info.get("track") or "").strip()
        title = f"{artist} - {track}".strip(" -") if (artist or track) \
            else ""
    if not title:
        return {"title": "Now Playing", "empty": True,
                "lines": ["Nothing playing"]}
    lines = [title]
    app = str(info.get("app") or "").strip()
    state = "Playing" if info.get("playing") else "Paused"
    lines.append(f"{state} · {app}" if app else state)
    return {"title": "Now Playing", "empty": False, "lines": lines}


def status_card(safe_mode=None, approval_mode=None,
                voice_enabled=None, shell_mode=None):
    """Status rail card from real settings/engine values.

    Every argument is a value the panel read from a real seam
    (config settings, approval.get_mode, the shell_mode pref);
    a value that couldn't be read arrives as None and its line
    is simply omitted — the card never guesses a state.
    """
    lines = []
    if approval_mode is not None:
        lines.append(f"Approvals: {approval_mode}")
    if safe_mode is not None:
        lines.append(f"Safe mode: {'on' if safe_mode else 'off'}")
    if voice_enabled is not None:
        lines.append(f"Voice: {'on' if voice_enabled else 'off'}")
    if shell_mode is not None:
        lines.append(f"Shell Mode boot: {'on' if shell_mode else 'off'}")
    if not lines:
        return {"title": "Status", "empty": True,
                "lines": ["Status unavailable"]}
    return {"title": "Status", "empty": False, "lines": lines}


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
