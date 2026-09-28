"""
ARIA Built-in Tool Implementations.
Standard operational tools for file I/O, web retrieval, system/window interaction,
Git/GitHub, email, notes, price tracking, and MTG data.
"""

import base64
from datetime import datetime, timedelta
import html as html_lib
import json
import os
import re
import socket
import smtplib
from email.message import EmailMessage
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple, Any

from aria.config import (
    WORKSPACE_DIR, MAX_TOOL_OUTPUT, GITHUB_TOKEN, GITHUB_USERNAME,
    GITHUB_ARMED, KEYS_FILE, BRIDGE_TOKEN, GEMINI_KEY_POOL,
    PRICE_WATCH_DB, key_get, save_keys, _KEYS, redact,
    get_gemini_key, key_mask, _KEY_QUARANTINE_UNTIL, _KEY_QUARANTINE_CODE
)
from aria.memory import (
    memory_save, memory_search_semantic, memory_get_all,
    spine_append, add_log, DB_PATH, DB_LOCK
)
from aria.tools.sandbox import tool_run_python

try:
    from duckduckgo_search import DDGS
    SEARCH_AVAILABLE = True
except Exception:
    SEARCH_AVAILABLE = False

try:
    import pyautogui
    GUI_AVAILABLE = True
except Exception:
    GUI_AVAILABLE = False

_VIDEO_DL_PORT = 3003
JOURNAL_DIR = os.path.join(WORKSPACE_DIR, "journal")


# ---------------- Web Search & Fetch ----------------
def tool_web_search(query: str) -> str:
    """Live web search via DuckDuckGo."""
    add_log(f"Searching web: '{query[:25]}...'")
    if not SEARCH_AVAILABLE:
        return "Search library not installed."
    try:
        results = DDGS().text(query, max_results=3)
        if not results:
            return "No web results found."
        return "\n".join(f"• {r['title']}: {r['body']}" for r in results)
    except Exception as e:
        return f"Search error: {e}"


def _fetch_html(url: str) -> Optional[str]:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _html_to_text(html: str) -> str:
    html = re.sub(r'<script.*?</script>', ' ', html, flags=re.S | re.I)
    html = re.sub(r'<style.*?</style>', ' ', html, flags=re.S | re.I)
    html = re.sub(r'<noscript.*?</noscript>', ' ', html, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', ' ', html)
    text = html_lib.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def tool_fetch_url(url: str) -> str:
    """Fetch URL and strip scripts/styles into clean readable text."""
    html = _fetch_html(url)
    if not html:
        return f"[Could not fetch {url}]"
    return _html_to_text(html)[:MAX_TOOL_OUTPUT]


# ---------------- File Tools ----------------
def tool_write_file(filename: str, content: str) -> str:
    """Save content to a file in the workspace directory."""
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    path = os.path.join(WORKSPACE_DIR, os.path.basename(filename))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    add_log(f"File saved: {filename}")
    return f"Successfully wrote {len(content)} characters to {filename}."


def tool_read_file(filename: str) -> str:
    """Read a text file from the workspace directory."""
    path = os.path.join(WORKSPACE_DIR, os.path.basename(filename))
    if not os.path.exists(path):
        return f"File '{filename}' does not exist."
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def tool_list_files() -> str:
    """List all files in the workspace directory."""
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    files = os.listdir(WORKSPACE_DIR)
    return "Workspace files:\n" + "\n".join(f"- {f}" for f in files) if files else "Workspace is empty."


def tool_find_file(name: str, ext: str = "") -> str:
    """Search user directories and E: drive for a file by name substring."""
    name = str(name).lower()
    ext = str(ext).lower().lstrip(".")
    roots = []
    for var in ("USERPROFILE", "HOME"):
        home = os.environ.get(var)
        if home:
            for sub in ("Desktop", "Documents", "Downloads"):
                p = os.path.join(home, sub)
                if os.path.isdir(p):
                    roots.append(p)
    if os.path.isdir("E:\\"):
        roots.append("E:\\")
    skip = {"appdata", "node_modules", ".git", "__pycache__", "site-packages"}
    hits = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath[len(root):].count(os.sep) > 4:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in skip and not d.startswith(".")]
            for fn in filenames:
                if name in fn.lower() and (not ext or fn.lower().endswith("." + ext)):
                    hits.append(os.path.join(dirpath, fn))
                    if len(hits) >= 25:
                        return "Found (first 25):\n" + "\n".join(hits)
    if not hits:
        return f"[No files matching '{name}' found.]"
    return "Found:\n" + "\n".join(hits)


# ---------------- GUI & System Actions ----------------
def tool_gui_click(x: int, y: int) -> str:
    """Simulate a mouse click at screen coordinates."""
    if not GUI_AVAILABLE:
        return "PyAutoGUI not installed."
    pyautogui.click(x, y)
    add_log(f"GUI: Clicked ({x}, {y})")
    return f"Clicked coordinates ({x}, {y})."


def tool_gui_type(text: str) -> str:
    """Type string into the currently focused window."""
    if not GUI_AVAILABLE:
        return "PyAutoGUI not installed."
    pyautogui.write(text, interval=0.03)
    add_log(f"GUI: Typed '{text[:20]}...'")
    return "Typed text into active window."


def _open_video_downloader() -> str:
    """Open or launch local video-downloader on port 3003."""
    dl_url = f"http://localhost:{_VIDEO_DL_PORT}"
    try:
        urllib.request.urlopen(dl_url, timeout=2)
        os.system(f'start "" "{dl_url}"')
        return f"Video downloader is already running — opened {dl_url}."
    except Exception:
        pass
    home = os.environ.get("USERPROFILE", "")
    candidates = [
        os.path.join(d, "start-windows.bat") for d in (
            "E:\\video-downloader-main",
            "E:\\video-downloader",
            os.path.join(home, "Downloads", "video-downloader-main"),
            os.path.join(home, "Downloads", "video-downloader"),
            "C:\\apps\\video-downloader",
            "D:\\video-downloader-main",
        )
    ]
    checked = [os.path.dirname(p) for p in candidates]
    bat = next((p for p in candidates if os.path.isfile(p)), None)
    if not bat:
        return ("Couldn't find start-windows.bat — checked: " + ", ".join(checked) +
                ". Tell me which folder the video-downloader-main files are in and I'll remember it.")
    os.system(f'start "" "{bat}"')
    for _ in range(10):
        time.sleep(2)
        try:
            urllib.request.urlopen(dl_url, timeout=2)
            os.system(f'start "" "{dl_url}"')
            return f"Video downloader started — opened {dl_url}."
        except Exception:
            continue
    return (f"Started the video downloader server, but {dl_url} isn't responding yet — "
            "give it a few more seconds and open it yourself.")


def tool_open_app_or_url(target: str) -> str:
    """Open desktop application or web URL."""
    add_log(f"Launching: {target}")
    try:
        t = (target or "").replace('"', '').strip()
        tl = t.lower()
        if tl in ("video-downloader", "video downloader", "the video downloader"):
            return _open_video_downloader()
        if tl.startswith(("http://", "https://")):
            os.system(f'start "" "{t}"')
            return f"Opened URL: {t}"
        os.system(f'start "" "{t}"')
        return f"Launched application: {t}"
    except Exception as e:
        return f"Error opening target: {e}"


# ---------------- Clipboard ----------------
def _tk_root():
    import tkinter
    r = tkinter.Tk()
    r.withdraw()
    return r


def tool_clipboard_read() -> str:
    """Read plain text from Windows clipboard."""
    try:
        r = _tk_root()
        try:
            return r.clipboard_get()
        except Exception:
            return "[Clipboard is empty or holds non-text data]"
        finally:
            r.destroy()
    except Exception as e:
        return f"[Clipboard unavailable: {e}]"


def tool_clipboard_write(text: str) -> str:
    """Copy text into Windows clipboard."""
    try:
        r = _tk_root()
        try:
            r.clipboard_clear()
            r.clipboard_append(text)
            r.update()
            return f"Copied {len(text)} chars to clipboard."
        finally:
            r.destroy()
    except Exception as e:
        return f"[Clipboard write failed: {e}]"


# ---------------- Window Control ----------------
def _gw():
    try:
        import pygetwindow
        return pygetwindow
    except ImportError:
        return None


def _find_window(gw, title: str):
    t_lower = title.lower()
    for w in gw.getAllWindows():
        if t_lower in w.title.lower():
            return w
    return None


def tool_list_windows() -> str:
    gw = _gw()
    if not gw:
        return "[pygetwindow not installed]"
    titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
    return "\n".join(titles[:40]) if titles else "[No windows found]"


def tool_focus_window(title: str) -> str:
    gw = _gw()
    if not gw:
        return "[pygetwindow not installed]"
    w = _find_window(gw, title)
    if not w:
        return f"[No window matching '{title}']"
    try:
        if w.isMinimized:
            w.restore()
        w.activate()
        return f"Focused: {w.title}"
    except Exception as e:
        return f"[Focus failed: {e}]"


def tool_minimize_window(title: str) -> str:
    gw = _gw()
    if not gw:
        return "[pygetwindow not installed]"
    w = _find_window(gw, title)
    if not w:
        return f"[No window matching '{title}']"
    try:
        w.minimize()
        return f"Minimized: {w.title}"
    except Exception as e:
        return f"[Minimize failed: {e}]"


def tool_close_window(title: str) -> str:
    gw = _gw()
    if not gw:
        return "[pygetwindow not installed]"
    w = _find_window(gw, title)
    if not w:
        return f"[No window matching '{title}']"
    try:
        t = w.title
        w.close()
        return f"Closed: {t}"
    except Exception as e:
        return f"[Close failed: {e}]"


_MEDIA_ACTIONS = {
    "mute": "volumemute", "volume_up": "volumeup",
    "volume_down": "volumedown", "play_pause": "playpause",
    "next": "audionext", "prev": "audioprev"
}


def tool_media_key(action: str) -> str:
    if not GUI_AVAILABLE:
        return "[pyautogui not installed]"
    a = (action or "").lower().strip()
    key = _MEDIA_ACTIONS.get(a)
    if not key:
        return f"[Unknown media action '{action}'. Valid: {', '.join(sorted(_MEDIA_ACTIONS))}]"
    try:
        pyautogui.press(key)
        return f"Sent media key: {a}."
    except Exception as e:
        return f"[Media key failed: {e}]"


def tool_volume(action: str = "status", level: int = 50) -> str:
    """Control master system audio volume via pycaw."""
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
        dev = AudioUtilities.GetSpeakers()
        iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(iface, POINTER(IAudioEndpointVolume))
        a = str(action).lower().strip()
        if a == "set":
            pct = max(0, min(100, int(level)))
            vol.SetMasterVolumeLevelScalar(pct / 100.0, None)
            return f"Volume set to {pct}%."
        if a == "mute":
            vol.SetMute(1, None)
            return "Muted."
        if a == "unmute":
            vol.SetMute(0, None)
            return "Unmuted."
        if a == "up":
            vol.SetMasterVolumeLevelScalar(min(1.0, vol.GetMasterVolumeLevelScalar() + 0.1), None)
        elif a == "down":
            vol.SetMasterVolumeLevelScalar(max(0.0, vol.GetMasterVolumeLevelScalar() - 0.1), None)
        cur = int(vol.GetMasterVolumeLevelScalar() * 100)
        return f"Volume is at {cur}%."
    except Exception as e:
        return f"[Volume control failed: {e}]"


# ---------------- Comms (Gmail) ----------------
def _gmail_creds() -> Tuple[str, str]:
    return (key_get("GMAIL_USER") or "").strip(), \
           (key_get("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()


def tool_gmail_setup(gmail_user: str, app_password: str) -> str:
    """Store Gmail credentials in aria_keys.json."""
    _KEYS["GMAIL_USER"] = (gmail_user or "").strip()
    _KEYS["GMAIL_APP_PASSWORD"] = (app_password or "").replace(" ", "").strip()
    save_keys()
    return f"Gmail saved for {_KEYS['GMAIL_USER']}. You can now send email with send_email."


def tool_send_email(to: str, subject: str, body: str) -> str:
    """Send an email through user's Gmail using SMTP."""
    user, pw = _gmail_creds()
    if not user or not pw or pw == "INSERT":
        return ("Gmail isn't set up yet. Ask the user for their Gmail address and an "
                "app password (myaccount.google.com/apppasswords - needs 2-Step "
                "Verification turned on), then call gmail_setup to save them.")
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body or "")
    
    for _sa in range(2):
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
                s.starttls()
                s.login(user, pw)
                s.send_message(msg)
            add_log(f"Email sent to {to}: {subject}")
            return f"Email sent to {to}: '{subject}'."
        except smtplib.SMTPAuthenticationError:
            return ("Gmail rejected the login. The app password is wrong or was revoked - "
                    "ask the user to generate a fresh one at myaccount.google.com/apppasswords "
                    "and call gmail_setup again.")
        except Exception as e:
            add_log(f"Email send attempt {_sa + 1}/2 failed ({type(e).__name__}) - retrying...")
            time.sleep(3)
    return f"Could not send email to {to}."


# ---------------- GitHub ----------------
def _github_request(path: str, method: str = "GET", payload: Optional[dict] = None) -> Tuple[Optional[dict], Optional[str]]:
    if not GITHUB_ARMED or not GITHUB_TOKEN:
        return None, "GitHub token not configured or armed in aria_keys.json."
    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ARIA-Agent",
        "Content-Type": "application/json"
    }
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return None, f"GitHub API error {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}"
    except Exception as e:
        return None, f"GitHub request failed: {e}"


def tool_github_push(repo: str, filepath: str, content: str, message: str = "") -> str:
    """Create or update a file in a GitHub repo."""
    r_name = repo or f"{GITHUB_USERNAME}/ARIA-Agent"
    add_log(f"GitHub: pushing {filepath} -> {r_name}")
    enc_path = urllib.parse.quote(filepath)
    existing, _ = _github_request(f"/repos/{r_name}/contents/{enc_path}")
    payload = {
        "message": message or f"ARIA: update {filepath}",
        "content": base64.b64encode(content.encode("utf-8")).decode()
    }
    if existing and "sha" in existing:
        payload["sha"] = existing["sha"]
    result, err = _github_request(f"/repos/{r_name}/contents/{enc_path}", "PUT", payload)
    if err:
        return f"Push failed: {err}"
    return f"Pushed '{filepath}' to {r_name}."


def tool_github_create_repo(name: str, description: str = "", private: bool = True) -> str:
    """Create a new GitHub repository."""
    add_log(f"GitHub: creating repo '{name}'")
    result, err = _github_request(
        "/user/repos", "POST",
        {"name": name, "description": description, "private": private, "auto_init": True}
    )
    if err:
        return f"Repo creation failed: {err}"
    return f"Repository '{name}' created: {result.get('html_url', '')}"


# ---------------- Memory Plus & Notes ----------------
def memory_forget(query: str) -> str:
    """Delete non-system memories matching keyword."""
    spine_append("memory_forget", {"query": query})
    q = (query or "").strip().lower()
    if not q:
        return "Nothing to forget: empty query."
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id FROM memory WHERE category != 'system' AND (lower(key) LIKE ? OR lower(value) LIKE ?)",
                    (f"%{q}%", f"%{q}%"))
        ids = [r[0] for r in cur.fetchall()]
        for rid in ids:
            cur.execute("DELETE FROM memory WHERE id = ?", (rid,))
        conn.commit()
        conn.close()
    add_log(f"Forgot {len(ids)} memories matching '{query}'")
    noun = "memory" if len(ids) == 1 else "memories"
    return f"Forgot {len(ids)} {noun} matching '{query}'."


def journal_write(entry: str) -> str:
    """Write an entry to the daily dated markdown journal."""
    spine_append("journal", {"entry": entry})
    try:
        os.makedirs(JOURNAL_DIR, exist_ok=True)
        day = datetime.now()
        path = os.path.join(JOURNAL_DIR, day.strftime("%Y-%m-%d") + ".md")
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if new:
                f.write(f"# {day.strftime('%A, %B %d, %Y')}\n\n")
            f.write(f"## {day.strftime('%I:%M %p')}\n{(entry or '').strip()}\n\n")
        add_log("Journal entry written")
        try:
            stamp = day.strftime("%Y-%m-%d %H:%M:%S.%f")
            memory_save("journal", f"journal {stamp}", (entry or "").strip()[:2000])
        except Exception as _je:
            add_log(f"Journal memory save failed: {_je}")
        return f"Journal entry saved to {os.path.basename(path)}"
    except Exception as e:
        return f"Journal write failed: {e}"


def note_take(text: str) -> str:
    """Voice note: saves to today's journal and searchable memory."""
    text = (text or "").strip()
    if not text:
        return "[Nothing to note - empty text.]"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    journal_write(f"Note to self: {text}")
    try:
        memory_save("note", f"note {stamp}", text)
    except Exception as e:
        add_log(f"Note memory save failed: {e}")
    return "Noted."


def _resolve_note_date(s: str) -> str:
    s = (s or "").strip().lower()
    now = datetime.now()
    if s in ("today", ""):
        return now.strftime("%Y-%m-%d")
    if s == "yesterday":
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if s in weekdays:
        target = weekdays.index(s)
        diff = (now.weekday() - target) % 7
        if diff == 0:
            diff = 7
        return (now - timedelta(days=diff)).strftime("%Y-%m-%d")
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except Exception:
        return now.strftime("%Y-%m-%d")


def note_read(date_str: str = "today") -> str:
    """Read voice notes from a given date."""
    day = _resolve_note_date(date_str)
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM memory WHERE category='note' AND key LIKE ? ORDER BY key",
                    (f"note {day}%",))
        rows = cur.fetchall()
        conn.close()
    if not rows:
        return f"No notes from {day}."
    lines = [f"• {k[5:]} — {v}" for k, v in rows]
    return f"Notes from {day}:\n" + "\n".join(lines)


# ---------------- MTG & Price Watches ----------------
def _scryfall_get(url: str) -> Tuple[Optional[dict], Optional[Any]]:
    req = urllib.request.Request(
        url, headers={"User-Agent": "ARIA-Agent/1.0", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8")), None
    except Exception as e:
        return None, e


def tool_mtg_card(card_name: str) -> str:
    """Look up Magic card details and price on Scryfall."""
    try:
        url = "https://api.scryfall.com/cards/named?fuzzy=" + urllib.parse.quote(card_name)
        c, err = _scryfall_get(url)
        if c is None:
            if isinstance(err, urllib.error.HTTPError) and err.code == 404:
                return f"[Card not found: '{card_name}']"
            return f"[Scryfall lookup failed: {err}]"
        if c.get("object") == "error":
            return f"[Card not found: '{card_name}']"
        usd = (c.get("prices") or {}).get("usd")
        lines = [
            f"{c.get('name', '?')} {c.get('mana_cost', '')}".strip(),
            c.get("type_line", ""),
            (c.get("oracle_text") or "[no oracle text]")[:600]
        ]
        if usd:
            lines.append(f"Market: ~${usd}")
        return "\n".join(lines)
    except Exception as e:
        return f"[Scryfall lookup failed: {e}]"


def tool_mtg_advice(deck: str, card_name: str) -> str:
    """Card advice for Commander."""
    data, err = _scryfall_get("https://api.scryfall.com/cards/named?fuzzy=" + urllib.parse.quote(str(card_name)))
    if err or not data or "name" not in data:
        return f"[Couldn't fetch '{card_name}': {err or 'not found'}]"
    card_txt = (
        f"{data.get('name')} - {data.get('mana_cost', '')} {data.get('type_line', '')}\n"
        f"{data.get('oracle_text', '')}\nEDHREC rank: {data.get('edhrec_rank', 'n/a')}"
    )
    return (
        f"Card: {data.get('name')}\n"
        f"Type: {data.get('type_line', '')}\n"
        f"Mana: {data.get('mana_cost', '')}\n"
        f"Oracle: {data.get('oracle_text', '')[:300]}\n"
        f"EDHREC: #{data.get('edhrec_rank', 'n/a')}\n"
        f"Deck context: {deck}"
    )


def _price_db():
    conn = sqlite3.connect(PRICE_WATCH_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS price_watches(
        id INTEGER PRIMARY KEY, url TEXT, target REAL, label TEXT,
        last_price REAL, alerted INTEGER DEFAULT 0, created TEXT)""")
    return conn


def tool_watch_price(url: str, target_price: str, label: str = "item") -> str:
    """Add a target price watch."""
    try:
        target = float(str(target_price).replace("$", "").strip())
    except ValueError:
        return "[target_price must be a number, e.g. 40]"
    with _price_db() as db:
        cur = db.execute(
            "INSERT INTO price_watches(url,target,label,last_price,created) VALUES(?,?,?,?,?)",
            (url, target, label, None, datetime.now().isoformat())
        )
        wid = cur.lastrowid
    add_log(f"Price watch #{wid}: {label} <= ${target:.2f}")
    return f"Watching '{label}' (#{wid}): I'll alert you when it's at or below ${target:.2f}. Checks run hourly."


def tool_list_price_watches() -> str:
    """List all active price watches."""
    with _price_db() as db:
        rows = db.execute("SELECT id,label,url,target,last_price,alerted FROM price_watches ORDER BY id").fetchall()
    if not rows:
        return "[No active price watches]"
    return "\n".join(
        f"#{r[0]} {r[1]} — target ${r[3]:.2f}"
        f"{f', last seen ${r[4]:.2f}' if r[4] else ', not checked yet'}"
        f"{' [alerted]' if r[5] else ''}\n  {r[2]}" for r in rows
    )


def tool_unwatch_price(watch_id: int) -> str:
    """Remove a price watch by ID."""
    with _price_db() as db:
        cur = db.execute("DELETE FROM price_watches WHERE id=?", (watch_id,))
    return f"Removed price watch #{watch_id}." if cur.rowcount else f"[No watch #{watch_id}]"


# ---------------- Admin Tools ----------------
def tool_gemini_keys(action: str = "status", key: str = "") -> str:
    """Check status or add/remove Gemini API keys in pool."""
    a = str(action).lower().strip()
    now = time.time()
    if a == "status":
        if not GEMINI_KEY_POOL:
            return "No Gemini API keys configured. Add one with the gemini_keys tool (action: add)."
        lines = []
        for i, k in enumerate(GEMINI_KEY_POOL):
            quar = _KEY_QUARANTINE_UNTIL.get(i, 0) - now
            if quar > 0:
                qcode = _KEY_QUARANTINE_CODE.get(i)
                why = f"HTTP {qcode}" if qcode else "key rejected by Google"
                state = f"QUARANTINED ({int(quar)}s left - {why})"
            else:
                state = "ready"
            lines.append(f"#{i + 1} {key_mask(k)} - {state}")
        return f"{len(GEMINI_KEY_POOL)} key(s) in pool:\n" + "\n".join(lines)
    if a == "add":
        k = str(key).strip()
        if not ((k.startswith("AIza") or k.startswith("AQ.")) and len(k) >= 20):
            return "That doesn't look like a Gemini API key (expected AIza... or AQ... with 20+ chars). Not added."
        if k in GEMINI_KEY_POOL:
            return "That key is already in the pool."
        GEMINI_KEY_POOL.append(k)
        _KEYS["GEMINI_API_KEYS"] = list(GEMINI_KEY_POOL)
        _KEYS.pop("GEMINI_API_KEY", None)
        save_keys()
        return f"Added key {key_mask(k)} to pool ({len(GEMINI_KEY_POOL)} keys total)."
    return f"Unknown action '{action}'. Use 'status' or 'add'."
