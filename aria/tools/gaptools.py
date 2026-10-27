"""Small gap tools — Agent-native versions of the Mark-LVI actions
Alek asked to close on 2026-10-27: flight_finder, game_updater,
browser_control, os_reminder, update_status, dev_agent, code_helper.

Built for THIS codebase, not ported machinery: no Playwright, no
pyautogui screenshot clicking, no direct Gemini SDK. Everything here
is stdlib plus existing Agent seams, imported lazily inside the
handlers so this module loads with the stdlib alone (the repo test
stub pattern depends on that):

  * opening pages / fetching / writing files -> aria.tools.builtins
  * running generated python                 -> aria.tools.sandbox
  * text generation                          -> aria.agent.providers
  * live counts                              -> aria.fleet / taskstore

HONEST SCOPE — what these tools deliberately do NOT do:

  * flight_finder builds a Google Flights URL and opens it. Prices
    are NOT scraped and never invented; they live on the opened page.
  * game_updater covers Steam only. Epic's launcher offers no
    per-game update trigger ARIA can verify, so Epic is reported as
    unsupported rather than faked with screenshot clicking.
  * browser_control get_text is a plain HTTP fetch + tag strip (the
    builtins fetcher), not a rendered page — JavaScript-heavy pages
    may come back thin, and the tool says so by returning exactly
    what it got.
  * os_reminder registers with the OS scheduler (schtasks /
    systemd-run / at) precisely so it fires even if ARIA is closed;
    the in-app set_reminder cannot promise that.
  * dev_agent / code_helper are thin delegations to the provider
    chain. A saved file is reported as saved — never as working.

Every handler takes one args dict and returns a string, typed with
aria.outcome (done / unverified / failed + facts) when that module
is importable, and the same line shape as a plain string when it is
not, so a missing outcome module can never take a tool down.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Outcome plumbing ────────────────────────────────────────────────
# aria.outcome is imported lazily inside every call. If it is absent
# (or its call fails), fall back to rendering the same status line by
# hand: `status=<word> action=<name> key=value ...`.


def _fmt_val(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).replace('"', "'").replace("\n", " ").strip()
    return f'"{text}"' if (" " in text or not text) else text


def _plain(status: str, action: str, reason: str = "", facts=None) -> str:
    parts = [f"status={status}", f"action={action}"]
    parts += [f"{k}={_fmt_val(v)}" for k, v in (facts or {}).items()
              if v is not None]
    if reason:
        parts.append(f"reason={_fmt_val(reason)}")
    return " ".join(parts)


def _outcome():
    try:
        from aria import outcome
        return outcome
    except Exception:
        return None


def _done(action: str, /, **facts) -> str:
    oc = _outcome()
    if oc is not None:
        try:
            return oc.done(action, **facts)
        except Exception:
            pass
    return _plain("done", action, facts=facts)


def _unverified(action: str, reason: str, /, **facts) -> str:
    oc = _outcome()
    if oc is not None:
        try:
            return oc.unverified(action, reason, **facts)
        except Exception:
            pass
    return _plain("unverified", action, reason, facts) + (
        " -- NOT confirmed. Tell the user plainly that you asked and "
        "cannot confirm it worked.")


def _failed(action: str, reason: str, /, **facts) -> str:
    oc = _outcome()
    if oc is not None:
        try:
            return oc.failed(action, reason, **facts)
        except Exception:
            pass
    return _plain("failed", action, reason, facts) + (
        " -- This did NOT happen. Tell the user so, with the reason.")


# ── Agent seams (lazy; each is a monkeypatch point for tests) ───────


def _builtins():
    """The builtins module, resolved fresh on every call."""
    from aria.tools import builtins
    return builtins


def _provider_text(system: str, prompt: str, role: str = "code") -> str:
    from aria.agent.providers import provider_text
    return provider_text(system, prompt, role=role)


def _run_python(code: str) -> str:
    from aria.tools.sandbox import tool_run_python
    return tool_run_python(code)


def _workspace_dir() -> Path:
    try:
        from aria import config
        return Path(config.WORKSPACE_DIR)
    except Exception:
        return Path(__file__).resolve().parents[2] / "workspace"


def _os_name() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _run(cmd) -> tuple:
    """Run a command list; return (returncode, combined output).
    Never raises — a missing binary is (1, message), not a crash."""
    try:
        proc = subprocess.run([str(c) for c in cmd], capture_output=True,
                              text=True, timeout=30)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return 1, str(exc)


def _launch_uri(uri: str) -> None:
    """Hand a steam:// (or similar) URI to the OS handler."""
    if sys.platform.startswith("win"):
        os.startfile(uri)  # noqa: S606 — URI built from a numeric appid
    elif sys.platform == "darwin":
        subprocess.Popen(["open", uri])
    else:
        subprocess.Popen(["xdg-open", uri])


# ── 1) Flight finder ────────────────────────────────────────────────

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}
for _name, _num in list(_MONTHS.items()):
    _MONTHS.setdefault(_name[:3], _num)
_MONTHS["sept"] = 9

_CABIN_CODE = {"economy": "1", "premium": "2", "business": "3",
               "first": "4"}


def parse_date(raw, today=None):
    """Parse a user date into 'YYYY-MM-DD', or None if unparseable.

    Supported: YYYY-MM-DD; MM/DD/YYYY (a DD/MM/YYYY reading is tried
    only when MM/DD cannot parse — ambiguity always resolves MM/DD,
    this user is in the US); 'today' / 'tomorrow'; 'Month D' and
    'D Month' (year = this year when the month is the current month
    or later, else next year). `today` is injectable for tests and
    may be a date or a datetime.
    """
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    if today is None:
        today = date.today()
    elif isinstance(today, datetime):
        today = today.date()
    lower = raw.lower()

    if "today" in lower:
        return today.isoformat()
    if "tomorrow" in lower:
        return (today + timedelta(days=1)).isoformat()

    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue

    mon_s = day_s = year_s = None
    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?"
                 r"(?:,?\s+(\d{4}))?$", raw)
    if m:  # 'Month D'
        mon_s, day_s, year_s = m.group(1), m.group(2), m.group(3)
    else:
        m = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)"
                     r"(?:,?\s+(\d{4}))?$", raw)
        if m:  # 'D Month'
            day_s, mon_s, year_s = m.group(1), m.group(2), m.group(3)
    if mon_s is None:
        return None
    month = _MONTHS.get(mon_s.lower().rstrip("."))
    if month is None:
        return None
    year = int(year_s) if year_s else (
        today.year if month >= today.month else today.year + 1)
    try:
        return date(year, month, int(day_s)).isoformat()
    except ValueError:
        return None


def build_flights_url(origin, destination, date_iso, return_date=None,
                      passengers=1, cabin="economy") -> str:
    """The Google Flights URL, shaped like the Mark-LVI builder:
    q=Flights+from+<origin>+to+<destination>+on+<date>[+returning+<d>]
    with cabin codes economy=1 premium=2 business=3 first=4.

    One deliberate deviation from LVI: its builder appended a
    hardcoded `tfs=` blob encoding an Istanbul->London trip on
    2025-03-15, which contradicts the q= it sits next to. It is
    omitted here; q= carries the whole search.
    """
    def _plus(text):
        return re.sub(r"\s+", "+", str(text).strip())

    trip = (f"Flights+from+{_plus(origin)}+to+{_plus(destination)}"
            f"+on+{date_iso}")
    if return_date:
        trip += f"+returning+{return_date}"
    cabin_code = _CABIN_CODE.get(str(cabin).lower().strip(), "1")
    try:
        pax = max(1, int(passengers))
    except (TypeError, ValueError):
        pax = 1
    return (f"https://www.google.com/travel/flights?q={trip}"
            f"&curr=USD&cabin={cabin_code}&adults={pax}")


def tool_flight_finder(args) -> str:
    args = args or {}
    origin = str(args.get("origin") or "").strip()
    destination = str(args.get("destination") or "").strip()
    if not origin or not destination:
        return _failed(
            "flight_finder",
            "both an origin and a destination are needed to search "
            "for flights")
    date_raw = str(args.get("date") or "").strip()
    if not date_raw:
        return _failed(
            "flight_finder",
            "a departure date is needed (YYYY-MM-DD, MM/DD/YYYY, "
            "'tomorrow', or 'Month D' like 'November 5')")
    date_iso = parse_date(date_raw)
    if date_iso is None:
        return _failed(
            "flight_finder",
            f"could not understand the date '{date_raw}' — use "
            "YYYY-MM-DD, MM/DD/YYYY, 'today'/'tomorrow', or 'Month D'")
    return_iso = None
    return_raw = str(args.get("return_date") or "").strip()
    if return_raw:
        return_iso = parse_date(return_raw)
        if return_iso is None:
            return _failed(
                "flight_finder",
                f"could not understand the return date '{return_raw}'")
    cabin = str(args.get("cabin") or "economy").strip().lower()
    if cabin not in _CABIN_CODE:
        cabin = "economy"
    url = build_flights_url(origin, destination, date_iso, return_iso,
                            args.get("passengers", 1), cabin)
    opened = ""
    try:
        opened = str(_builtins().tool_open_app_or_url(url) or "")
    except Exception as exc:  # the URL is still the deliverable
        opened = f"(opening the page raised: {exc})"
    facts = {"url": url, "origin": origin, "destination": destination,
             "date": date_iso, "cabin": cabin}
    if return_iso:
        facts["return_date"] = return_iso
    return _unverified(
        "flight_finder",
        "the Google Flights page was opened but its contents were "
        "not read from here — prices are NOT scraped and none are "
        "known until the page is looked at",
        **facts) + (
            f"\nGoogle Flights search: {origin} to {destination} on "
            f"{date_iso}"
            + (f", returning {return_iso}" if return_iso else "")
            + f". Results are in the opened page: {url}"
            + (f"\n{opened}" if opened else ""))


# ── 2) Game updater (Steam only) ────────────────────────────────────
#
# Steam keeps one appmanifest_<appid>.acf per installed game in each
# library's steamapps folder; libraryfolders.vdf lists the library
# roots. StateFlags bit 0x4 means fully installed. Whether an update
# is pending is an APPROXIMATION from local state: StateFlags bit
# 0x2 (Steam's "update required" flag) or the manifest's UpdateResult
# being non-zero. Steam can know more than the manifests say (it
# checks its servers), so 'status' reports what Steam has FLAGGED
# locally, never a guarantee that no update exists.

_FULLY_INSTALLED = 0x4
_UPDATE_REQUIRED = 0x2

_ACF_PAIR = re.compile(r'"([^"]+)"\s+"([^"]*)"')


def parse_appmanifest(text) -> dict:
    """Parse one appmanifest .acf into a flat dict. First occurrence
    of each key wins (the top-level block precedes nested sections).
    All-digit values become ints, so appid / StateFlags / UpdateResult
    come back numeric."""
    out = {}
    for key, value in _ACF_PAIR.findall(text or ""):
        if key in out:
            continue
        out[key] = int(value) if re.fullmatch(r"-?\d+", value) else value
    return out


def find_steam_path(root=None, candidates=None):
    """Locate the Steam install root, or None.

    `root` is the injectable seam: when given, it is THE candidate
    (used by tests and by an explicit steam_path argument). Otherwise
    the common locations are tried without touching the registry:
    %ProgramFiles(x86)%/Steam, %ProgramFiles%/Steam, ~/.steam/steam,
    ~/.local/share/Steam, ~/Library/Application Support/Steam.
    A candidate counts when its steamapps folder exists.
    """
    if root is not None:
        p = Path(root)
        return p if (p / "steamapps").is_dir() else None
    if candidates is None:
        candidates = []
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(env)
            if base:
                candidates.append(Path(base) / "Steam")
        home = Path.home()
        candidates += [
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
            home / "Library" / "Application Support" / "Steam",
        ]
    for cand in candidates:
        p = Path(cand)
        if (p / "steamapps").is_dir():
            return p
    return None


def _steam_libraries(steam_path) -> list:
    """steamapps folders: the main one plus every 'path' entry in
    steamapps/libraryfolders.vdf (simple regex parse; the file is a
    flat list of library roots for this purpose)."""
    steam_path = Path(steam_path)
    libs = [steam_path / "steamapps"]
    vdf = steam_path / "steamapps" / "libraryfolders.vdf"
    try:
        content = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        content = ""
    for raw in re.findall(r'"path"\s+"([^"]+)"', content):
        lib = Path(raw.replace("\\\\", "/")) / "steamapps"
        if lib.is_dir() and lib not in libs:
            libs.append(lib)
    return libs


def steam_games(steam_path) -> list:
    """Every game with a manifest across all libraries, deduped by
    appid. Each dict is the parsed manifest plus 'library'."""
    games, seen = [], set()
    for lib in _steam_libraries(steam_path):
        for acf in sorted(lib.glob("appmanifest_*.acf")):
            try:
                data = parse_appmanifest(
                    acf.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            appid = data.get("appid")
            if appid is None or not data.get("name") or appid in seen:
                continue
            seen.add(appid)
            data["library"] = str(lib)
            games.append(data)
    return games


def _needs_update(game) -> bool:
    try:
        flags = int(game.get("StateFlags") or 0)
    except (TypeError, ValueError):
        flags = 0
    try:
        result = int(game.get("UpdateResult") or 0)
    except (TypeError, ValueError):
        result = 0
    return bool(flags & _UPDATE_REQUIRED) or result != 0


def _installed(game) -> bool:
    try:
        return bool(int(game.get("StateFlags") or 0) & _FULLY_INSTALLED)
    except (TypeError, ValueError):
        return False


def tool_game_updater(args) -> str:
    args = args or {}
    action = str(args.get("action") or "list").strip().lower()
    if action not in ("list", "update", "status"):
        return _failed(
            "game_updater",
            f"unknown action '{action}' — use list, update, or status")
    steam = find_steam_path(args.get("steam_path"))
    if steam is None:
        return _failed(
            "game_updater",
            "Steam was not found on this machine (checked the "
            "standard install locations). Epic Games is not "
            "supported: its launcher offers no per-game update "
            "trigger ARIA can verify.")
    games = steam_games(steam)
    name_filter = str(args.get("game") or "").strip().lower()
    if name_filter:
        games = [g for g in games
                 if name_filter in str(g.get("name", "")).lower()]

    if action == "list":
        lines = [f"  {g.get('name')} (appid {g.get('appid')}) — "
                 + ("installed" if _installed(g) else "not fully "
                    "installed")
                 + ("; update flagged" if _needs_update(g) else "")
                 for g in games]
        return _done("game_updater", action="list", games=len(games),
                     steam=str(steam)) + (
                         "\nSteam games"
                         + (f" matching '{name_filter}'"
                            if name_filter else "")
                         + f" ({len(games)}):\n"
                         + ("\n".join(lines) if lines
                            else "  (none found)"))

    if action == "status":
        pending = [g for g in games if _needs_update(g)]
        if not pending:
            return _done(
                "game_updater", action="status", pending=0) + (
                    "\nNo updates pending that Steam has flagged in "
                    "its local manifests.")
        names = ", ".join(str(g.get("name")) for g in pending)
        return _done("game_updater", action="status",
                     pending=len(pending)) + (
                         f"\nUpdates flagged by Steam ({len(pending)}"
                         f"): {names}")

    # action == "update": a name filter targets those games; without
    # one, target the games Steam has flagged for an update.
    targets = games if name_filter else [g for g in games
                                         if _needs_update(g)]
    if not targets:
        if name_filter:
            return _failed(
                "game_updater",
                f"no installed Steam game matches '{name_filter}'")
        return _done("game_updater", action="update", queued=0) + (
            "\nNo updates pending that Steam has flagged, so nothing "
            "was queued.")
    for game in targets:
        _launch_uri(f"steam://validate/{game.get('appid')}")
    names = ", ".join(str(g.get("name")) for g in targets)
    return _unverified(
        "game_updater",
        "Steam was asked to validate/update via steam://validate; "
        "Steam queues it internally and completion is not observable "
        "from here",
        action="update", queued=len(targets)) + (
            f"\nUpdate/validate requested for: {names}")


# ── 3) Browser control ──────────────────────────────────────────────


def tool_browser_control(args) -> str:
    args = args or {}
    action = str(args.get("action") or "").strip().lower()
    url = str(args.get("url") or "").strip()
    if action not in ("go_to", "get_text"):
        return _failed(
            "browser_control",
            f"unknown action '{action}' — use go_to or get_text")
    if not url:
        return _failed("browser_control", "a url is required")
    if action == "go_to":
        try:
            result = _builtins().tool_open_app_or_url(url)
        except Exception as exc:
            return _failed("browser_control",
                           f"opening the browser failed: {exc}",
                           url=url)
        return _unverified(
            "browser_control",
            "a browser was asked to open the URL; what is on screen "
            "is not verified from here",
            url=url) + f"\n{result}"
    # get_text: the builtins fetcher (plain HTTP + tag strip), not a
    # rendered page.
    try:
        text = str(_builtins().tool_fetch_url(url) or "")
    except Exception as exc:
        return _failed("browser_control", f"fetch failed: {exc}",
                       url=url)
    if not text or text.startswith("[Could not fetch"):
        return _failed("browser_control",
                       f"could not fetch {url}", url=url)
    truncated = text[:4000]
    return _done("browser_control", action="get_text", url=url,
                 chars=len(truncated)) + f"\n{truncated}"


# ── 4) OS-level reminder ────────────────────────────────────────────
#
# Distinct from the in-app set_reminder: this registers with the OS
# scheduler so the reminder fires even if ARIA is closed.


def sanitise(text, max_len: int = 200) -> str:
    """Make reminder text safe to embed in a generated script: drop
    quotes, backslashes and control characters, flatten newlines to
    spaces, cap the length."""
    out = []
    for ch in str(text or ""):
        if ch in ('"', "'", "\\"):
            continue
        if ch in ("\n", "\r", "\t"):
            out.append(" ")
        elif ord(ch) < 32 or ord(ch) == 127:
            continue
        else:
            out.append(ch)
    return "".join(out).strip()[:max_len]


def task_name_for(message, when) -> str:
    """'ARIA Reminder <yyyymmddHHMM> <slug>' — unique per minute and
    recognisable in the OS scheduler's list."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(message or "").lower()).strip("-")
    slug = slug[:24].strip("-") or "reminder"
    return f"ARIA Reminder {when:%Y%m%d%H%M} {slug}"


def build_notify_script(message, os_name) -> str:
    """The script the OS scheduler will run at the reminder time.
    `message` must already be sanitised (no quotes/newlines)."""
    name = str(os_name or "").lower()
    if name in ("win32", "windows", "win"):
        ps_msg = message.replace("'", "''")
        return (
            "# ARIA OS reminder — generated by aria.tools.gaptools\r\n"
            "Add-Type -AssemblyName System.Windows.Forms\r\n"
            "try {\r\n"
            f"    [System.Windows.Forms.MessageBox]::Show('{ps_msg}', "
            "'ARIA Reminder') | Out-Null\r\n"
            "} catch {\r\n"
            f"    msg * \"ARIA Reminder: {message}\"\r\n"
            "}\r\n")
    if name in ("darwin", "mac", "macos"):
        return (
            "#!/bin/sh\n"
            "# ARIA OS reminder — generated by aria.tools.gaptools\n"
            f"osascript -e 'display dialog \"{message}\" with title "
            "\"ARIA Reminder\" buttons {\"OK\"} default button \"OK\"'\n")
    return (
        "#!/bin/sh\n"
        "# ARIA OS reminder — generated by aria.tools.gaptools\n"
        f"notify-send \"ARIA Reminder\" \"{message}\" "
        f"|| zenity --info --title=\"ARIA Reminder\" "
        f"--text=\"{message}\"\n")


def _parse_when(args):
    """(target datetime | None, error reason | None)."""
    at_raw = str(args.get("at") or "").strip()
    if at_raw:
        try:
            return datetime.fromisoformat(at_raw), None
        except ValueError:
            return None, (f"could not parse the time '{at_raw}' — use "
                          "ISO format YYYY-MM-DDTHH:MM")
    delay = args.get("delay_minutes")
    if delay is not None:
        try:
            minutes = float(delay)
        except (TypeError, ValueError):
            return None, "delay_minutes must be a number"
        return datetime.now() + timedelta(minutes=minutes), None
    return None, ("give either 'at' (YYYY-MM-DDTHH:MM local time) or "
                  "'delay_minutes'")


def tool_os_reminder(args) -> str:
    args = args or {}
    message = sanitise(args.get("message") or "")
    if not message:
        return _failed("os_reminder", "a reminder message is required")
    when, err = _parse_when(args)
    if err:
        return _failed("os_reminder", err)
    if when <= datetime.now():
        return _failed(
            "os_reminder",
            f"that time has passed ({when:%Y-%m-%d %H:%M}) — pick a "
            "future time")
    os_name = _os_name()
    name = task_name_for(message, when)
    script = build_notify_script(message, os_name)
    folder = _workspace_dir() / "os_reminders"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _failed("os_reminder",
                       f"could not create the reminders folder: {exc}")
    at_iso = f"{when:%Y-%m-%d %H:%M}"

    if os_name == "win32":
        script_path = folder / (re.sub(r"[^A-Za-z0-9]+", "_", name)
                                .strip("_") + ".ps1")
        try:
            script_path.write_text(script, encoding="utf-8")
        except OSError as exc:
            return _failed("os_reminder",
                           f"could not write the reminder script: {exc}")
        tr = (f'powershell -NoProfile -ExecutionPolicy Bypass -File '
              f'"{script_path}"')
        rc, out = _run(["schtasks", "/Create", "/TN", name, "/TR", tr,
                        "/SC", "ONCE", "/SD", f"{when:%m/%d/%Y}",
                        "/ST", f"{when:%H:%M}", "/F"])
        if rc != 0:
            tail = (out or "").strip()[-300:]
            return _failed(
                "os_reminder",
                f"schtasks refused to register the reminder "
                f"(rc={rc})" + (f": {tail}" if tail else ""),
                task=name)
        return _done("os_reminder", task=name, at=at_iso,
                     scheduler="schtasks") + (
                         f"\nOS reminder registered with Task "
                         f"Scheduler: '{name}' fires {at_iso} even "
                         f"if ARIA is closed.")

    # linux / darwin: a shell script + systemd-run, else `at`.
    ext = ".sh"
    script_path = folder / (re.sub(r"[^A-Za-z0-9]+", "_", name)
                            .strip("_") + ext)
    try:
        script_path.write_text(script, encoding="utf-8")
        script_path.chmod(0o700)
    except OSError as exc:
        return _failed("os_reminder",
                       f"could not write the reminder script: {exc}")
    if os_name == "linux" and shutil.which("systemd-run"):
        rc, out = _run(["systemd-run", "--user",
                        f"--on-calendar={when:%Y-%m-%d %H:%M:%S}",
                        str(script_path)])
        if rc == 0:
            return _done("os_reminder", task=name, at=at_iso,
                         scheduler="systemd-run") + (
                             f"\nOS reminder registered with "
                             f"systemd: '{name}' fires {at_iso} even "
                             f"if ARIA is closed.")
    if shutil.which("at"):
        rc, out = _run(["at", "-f", str(script_path),
                        f"{when:%H:%M}", f"{when:%Y-%m-%d}"])
        if rc == 0:
            return _done("os_reminder", task=name, at=at_iso,
                         scheduler="at") + (
                             f"\nOS reminder registered with at: "
                             f"'{name}' fires {at_iso} even if ARIA "
                             f"is closed.")
    return _failed(
        "os_reminder",
        "no OS scheduler available on this machine (tried "
        "systemd-run and at) — the reminder was NOT registered",
        task=name)


# ── 5) Update status ────────────────────────────────────────────────


def git_head(root=None):
    """The repo HEAD sha at `root` (default: this repo), or None."""
    if root is None:
        try:
            from aria import config
            root = config.ROOT_DIR
        except Exception:
            root = Path(__file__).resolve().parents[2]
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def render_status(head, snap, now=None) -> str:
    """Pure render: outcome line + one human line, from a git HEAD
    (or None) and a fleet snapshot dict (or None)."""
    snap = snap or {}
    inflight = len(snap.get("inflight") or [])
    recent_n = len(snap.get("recent") or [])
    delegated = snap.get("delegated") or []
    if isinstance(snap.get("delegated_active"), int):
        delegated_active = snap["delegated_active"]
    else:
        delegated_active = sum(1 for t in delegated
                               if isinstance(t, dict) and t.get("active"))
    if now is None:
        now = datetime.now()
    if isinstance(now, (int, float)):
        now = datetime.fromtimestamp(now)
    stamp = f"{now:%Y-%m-%d %H:%M}"
    facts = {"inflight": inflight, "recent": recent_n,
             "delegated_active": delegated_active}
    if isinstance(snap.get("tasks_active"), int):
        facts["tasks_active"] = snap["tasks_active"]
    if head:
        short = str(head)[:8]
        line = _done("update_status", head=short, **facts)
        human = (f"Code on disk is at {short} (as of {stamp}). "
                 f"{inflight} tool call(s) in flight, {recent_n} "
                 f"recent, {delegated_active} delegated task(s) "
                 f"active.")
    else:
        line = _unverified(
            "update_status",
            "the git HEAD could not be read in this install, so "
            "which code is on disk is unknown from here", **facts)
        human = (f"Could not read the git HEAD (as of {stamp}). "
                 f"{inflight} tool call(s) in flight, {recent_n} "
                 f"recent, {delegated_active} delegated task(s) "
                 f"active.")
    return line + "\n" + human


def tool_update_status(args) -> str:
    args = args or {}
    head = git_head(args.get("root"))
    snap = {}
    try:
        from aria import fleet
        snap = fleet.snapshot() or {}
    except Exception:
        snap = {}
    try:
        from aria.agent import taskstore
        records = taskstore.all_records() or []
        snap["tasks_total"] = len(records)
        snap["tasks_active"] = sum(
            1 for r in records
            if str(r.get("status", "")).lower()
            not in ("done", "complete", "completed", "failed"))
    except Exception:
        pass
    return render_status(head, snap, datetime.now())


# ── 6) Code helper + dev agent ──────────────────────────────────────

_CODE_SYSTEM = ("You are a senior software engineer helping through "
                "an assistant. Be concrete and brief; prefer complete "
                "working code over fragments.")


def _read_code_arg(args):
    """(code text | None, error reason | None) from args' code or
    file_path. File reads are capped at 20000 chars."""
    code = args.get("code")
    if code:
        return str(code)[:20000], None
    file_path = str(args.get("file_path") or "").strip()
    if not file_path:
        return None, "give either 'code' or 'file_path'"
    path = Path(file_path)
    if not path.is_absolute() and not path.exists():
        alt = _workspace_dir() / file_path
        if alt.exists():
            path = alt
    if not path.exists():
        return None, f"file not found: {file_path}"
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:20000], None
    except OSError as exc:
        return None, f"could not read {file_path}: {exc}"


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    m = re.match(r"^```[A-Za-z0-9_+-]*\n(.*)\n```$", text, re.S)
    return m.group(1).strip() if m else text


def tool_code_helper(args) -> str:
    args = args or {}
    action = str(args.get("action") or "explain").strip().lower()
    if action not in ("explain", "fix", "write"):
        return _failed(
            "code_helper",
            f"unknown action '{action}' — use explain, fix, or write")

    if action == "write":
        description = str(args.get("description")
                          or args.get("instruction") or "").strip()
        if not description:
            return _failed("code_helper",
                           "write needs a 'description' of the code")
        output_path = str(args.get("output_path") or "").strip()
        if not output_path:
            return _failed("code_helper",
                           "write needs an 'output_path' to save to")
        prompt = (f"Write complete, working code for this request. "
                  f"Reply with code only, no explanation.\n\n"
                  f"Request: {description}")
        try:
            text = _provider_text(_CODE_SYSTEM, prompt, role="code")
        except Exception as exc:
            return _failed("code_helper",
                           f"the code provider failed: {exc}")
        code = _strip_fences(str(text or ""))
        if not code:
            return _failed("code_helper",
                           "the provider returned no code")
        try:
            result = str(_builtins().tool_write_file(output_path, code))
        except Exception as exc:
            return _failed("code_helper",
                           f"saving the file failed: {exc}")
        if result.startswith(("Error", "[Could not")):
            return _failed("code_helper",
                           f"saving the file failed: {result}")
        return _done("code_helper", action="write", path=output_path,
                     chars=len(code)) + f"\n{result}"

    code, err = _read_code_arg(args)
    if err:
        return _failed("code_helper", err)
    instruction = str(args.get("instruction") or "").strip()
    if action == "explain":
        prompt = "Explain what this code does, briefly and concretely."
        if instruction:
            prompt += f" Focus on: {instruction}"
        prompt += f"\n\n{code}"
    else:  # fix
        prompt = ("Find the bug in this code and return the corrected "
                  "code, with a one-line note on what was wrong.")
        if instruction:
            prompt += f" Reported problem: {instruction}"
        prompt += f"\n\n{code}"
    try:
        text = str(_provider_text(_CODE_SYSTEM, prompt, role="code")
                   or "")
    except Exception as exc:
        return _failed("code_helper", f"the code provider failed: {exc}")
    if not text.strip():
        return _failed("code_helper", "the provider returned nothing")
    return _done("code_helper", action=action,
                 chars=len(text)) + f"\n{text}"


_EXT = {"python": "py", "javascript": "js", "typescript": "ts",
        "html": "html", "css": "css", "bash": "sh", "shell": "sh",
        "rust": "rs", "go": "go", "java": "java", "c": "c",
        "c++": "cpp", "cpp": "cpp"}


def tool_dev_agent(args) -> str:
    args = args or {}
    description = str(args.get("description") or "").strip()
    if not description:
        return _failed("dev_agent",
                       "a 'description' of the program is required")
    language = str(args.get("language") or "python").strip().lower()
    output_path = str(args.get("output_path") or "").strip()
    if not output_path:
        output_path = f"dev_agent_output.{_EXT.get(language, 'txt')}"
    try:
        plan = str(_provider_text(
            _CODE_SYSTEM,
            f"Plan a single-file {language} program for this request. "
            f"Reply with a brief plan only (a few short lines).\n\n"
            f"Request: {description}", role="code") or "")
        code = _strip_fences(str(_provider_text(
            _CODE_SYSTEM,
            f"Write the complete single-file {language} program for "
            f"this request. Reply with code only.\n\n"
            f"Request: {description}\n\nPlan:\n{plan}",
            role="code") or ""))
    except Exception as exc:
        return _failed("dev_agent", f"the code provider failed: {exc}")
    if not code:
        return _failed("dev_agent", "the provider returned no code")
    try:
        result = str(_builtins().tool_write_file(output_path, code))
    except Exception as exc:
        return _failed("dev_agent", f"saving the file failed: {exc}")
    if result.startswith(("Error", "[Could not")):
        return _failed("dev_agent", f"saving the file failed: {result}")

    facts = {"path": output_path, "language": language,
             "chars": len(code)}
    body = (f"\nPlan:\n{plan}\nSaved {len(code)} chars to "
            f"{output_path}. Saved is not the same as working — it "
            f"has not been verified beyond this.")
    if args.get("run"):
        if language == "python":
            try:
                out = str(_run_python(code) or "")
            except Exception as exc:
                out = f"(running it raised: {exc})"
            return _unverified(
                "dev_agent",
                "the program was saved and executed once; whether "
                "its output is CORRECT is not verified from here",
                ran=True, **facts) + body + (
                    f"\nRun output:\n{out[:1000]}")
        return _done("dev_agent", **facts) + body + (
            "\nRun was requested but skipped: only python output "
            "can be run from here.")
    return _done("dev_agent", **facts) + body
