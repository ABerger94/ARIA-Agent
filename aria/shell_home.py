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
    }


# ------------------------------------------------------------------
# V6 (2026-10-30, Alek's order): the header weather readout.
# weather_line formats the dict aria/scheduler.py::_weather_full()
# already returns (wttr.in: temp_F / desc / high_F / low_F, all
# strings, any of them possibly blank); weather_symbol picks the
# glyph for the condition text. Both are pure and fail-soft:
# missing data yields a shorter line or no line — never an
# invented value, never a crash.
# ------------------------------------------------------------------

#: (keywords, glyph) pairs for weather_symbol, matched IN ORDER
#: against the lowercased condition text — first hit wins, and
#: the order is load-bearing: thunder outranks the rain inside
#: "Patchy light rain with thunder", snow outranks rain for
#: "rain and snow", and "partly cloudy" is tested before plain
#: "cloud". One emoji glyph set, used consistently.
_WEATHER_SYMBOLS = (
    (("thunder",), "⛈"),
    (("blizzard", "snow"), "❄"),
    (("sleet", "hail", "ice pellet"), "🌨"),
    (("drizzle",), "🌦"),
    (("rain", "shower"), "🌧"),
    (("fog", "mist", "haze"), "🌫"),
    (("partly",), "⛅"),
    (("overcast", "cloud"), "☁"),
    (("clear", "sunny"), "☀"),
)


def weather_symbol(desc):
    """One unicode glyph for a wttr.in weatherDesc string.

    Keyword match, case-insensitive, in _WEATHER_SYMBOLS order.
    Unknown or empty conditions yield "" — no symbol rather
    than a wrong guess.
    """
    text = str(desc or "").strip().lower()
    if not text:
        return ""
    for keywords, glyph in _WEATHER_SYMBOLS:
        if any(k in text for k in keywords):
            return glyph
    return ""


def weather_line(weather):
    """The header weather line from a _weather_full() dict.

    Pinned format (V6): "61°F · Clear · High 68 · Low 49" —
    current temp + condition, then the day's high and low when
    the source provided them. Blank fields drop their segment;
    a dict with nothing usable (or None) yields "".
    """
    if not weather:
        return ""
    temp = str(weather.get("temp_F") or "").strip()
    desc = str(weather.get("desc") or "").strip()
    high = str(weather.get("high_F") or "").strip()
    low = str(weather.get("low_F") or "").strip()
    parts = []
    head = f"{temp}°F" if temp else ""
    if desc:
        head = f"{head} · {desc}" if head else desc
    if head:
        parts.append(head)
    if high:
        parts.append(f"High {high}")
    if low:
        parts.append(f"Low {low}")
    return " · ".join(parts)


# ------------------------------------------------------------------
# V2 presentation models (2026-10-30): tile grid + rail cards.
# Still pure — formatters decide what cards say from real values;
# no data: an honest empty state, never a fabrication.
# V4 (2026-10-30, Alek's order): the rail is Briefing ONLY — the
# panel composes the full Briefing-tab text via briefing_full_text
# and no longer renders Now Playing / Status at all. The
# now_playing_card / status_card formatters below are retained,
# tested, and deliberately UNUSED by the panel (kept to avoid
# churn; briefing_card likewise remains for its section model).
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


def briefing_full_text(report, research_text=""):
    """The Shell Home Briefing card's body (V4, 2026-10-30):
    EXACTLY the Workspace Briefing tab's text — nothing trimmed,
    summarised, or re-wrapped at this layer.

    That text is ``aria.briefing.report_text(report)`` (header
    line, intro, every section as TITLE + text, plus source
    blocks), with the research section appended exactly the way
    the dashboard's Briefing-tab load appends it (a blank-free
    ``"\\n" + section`` join, only when a section exists). The
    panel gathers *research_text* through its guarded seam —
    research can never break this composition. For a None
    *report*, report_text already yields the tab's honest
    placeholder line.
    """
    from aria.briefing import report_text
    text = report_text(report)
    if research_text:
        text += "\n" + research_text
    return text


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


# ------------------------------------------------------------------
# V8 (2026-10-30, Alek's order + approved design): pinned apps.
# Shell Home opens to the pinned grid ONLY; the full list hides
# behind an All Apps toggle, and search always searches all apps.
# Pin identity is the app entry's .lnk path (the stable,
# launchable identity from enumerate_apps), persisted in pin
# order via aria/ui_prefs. These helpers are the pure core; the
# panel wires them to ui_prefs and the right-click menu.
# ------------------------------------------------------------------

def is_pinned(pinned_paths, path):
    """True when *path* is among the pinned .lnk paths."""
    target = str(path or "")
    if not target:
        return False
    return any(str(p or "") == target for p in (pinned_paths or []))


def toggle_pin(pinned_paths, path):
    """A NEW pin list with *path* toggled: appended (in pin
    order) when absent, removed when present. The result is
    deduped and blank-free, so duplicate pins are impossible
    however the caller got its list. A blank path changes
    nothing (beyond the cleanup)."""
    cleaned = []
    for entry in pinned_paths or []:
        p = str(entry or "").strip()
        if p and p not in cleaned:
            cleaned.append(p)
    target = str(path or "").strip()
    if not target:
        return cleaned
    if target in cleaned:
        return [p for p in cleaned if p != target]
    return cleaned + [target]


def pinned_view(apps, pinned_paths):
    """The home grid's entries: the enumerated apps whose path
    is pinned, IN PIN ORDER. A pinned path that no longer
    enumerates (app uninstalled, shortcut moved) is skipped
    silently — the grid never shows a corpse or an error."""
    by_path = {}
    for app in apps or []:
        p = str((app or {}).get("path", "") or "")
        if p and p not in by_path:
            by_path[p] = app
    out = []
    for entry in pinned_paths or []:
        app = by_path.get(str(entry or "").strip())
        if app is not None:
            out.append(app)
    return out


def view_count_line(view, shown, total):
    """The grid's count line, honest per view (V8): the pinned
    view counts PINS ("8 pinned"), All Apps counts the full
    enumeration ("210 apps"), search counts matches against the
    full list ("3 of 210 apps") — never the total dressed up as
    what's on screen."""
    shown = max(0, int(shown or 0))
    total = max(0, int(total or 0))
    if view == "pinned":
        return f"{shown} pinned"
    if view == "search":
        noun = "app" if total == 1 else "apps"
        return f"{shown} of {total} {noun}"
    return f"{total} app" if total == 1 else f"{total} apps"


# ------------------------------------------------------------------
# V11 (2026-10-31, Alek's order): action cards — pending approvals
# and delivered-but-unacknowledged reminders surface IN Shell Home
# instead of waiting behind the Dashboard. These helpers only
# DECIDE what a card says from snapshots the real seams hand over
# (aria.approval.pending_entries — whose own docstring names UI
# surfaces as its consumer — and aria.reminder_gate.pending_cards).
# Resolution never happens here and never happens through a
# second store: the panel's buttons call approval.approve_token /
# deny_token and scheduler.tool_acknowledge_reminders, the exact
# paths the dashboard banner and the voice tool use.
# ------------------------------------------------------------------

def approval_card(entries):
    """The approval action card from a pending_entries()
    snapshot: the OLDEST open request (the dashboard banner
    shows the same one), or None when nothing is pending.
    Entries without a token cannot be resolved from any
    surface and are skipped rather than painted dead."""
    usable = [e for e in (entries or [])
              if isinstance(e, dict) and e.get("token")]
    if not usable:
        return None
    first = usable[0]
    return {"token": first["token"],
            "tool": str(first.get("tool") or ""),
            "desc": str(first.get("desc") or ""),
            "age_s": int(first.get("age_s") or 0),
            "pending": len(usable)}


def reminder_card(cards):
    """The reminder action card from a pending_cards()
    snapshot (delivered reminder cards still awaiting
    acknowledgement): the first message plus the count, or
    None when nothing is waiting. Cards with no message text
    carry nothing to acknowledge and are skipped."""
    messages = []
    for card in cards or []:
        if isinstance(card, dict):
            text = str(card.get("message") or "").strip()
            if text:
                messages.append(text)
    if not messages:
        return None
    return {"count": len(messages), "message": messages[0],
            "messages": messages}


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
