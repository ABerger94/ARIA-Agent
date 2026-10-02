"""
ARIA Built-in Tool Implementations.
Standard operational tools for file I/O, web retrieval, system/window interaction,
Git/GitHub, email, notes, price tracking, and MTG data.
"""

import base64
from datetime import datetime, timedelta
import email
import html as html_lib
import imaplib
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
    memory_forget_entries,
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
    """Save content to a file in the workspace directory (supports nested subdirectories)."""
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    clean = filename.strip().replace("\\", "/").lstrip("/")
    if ".." in clean:
        clean = os.path.basename(clean)
    path = os.path.join(WORKSPACE_DIR, clean)
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    add_log(f"File saved: {clean}")
    return f"Successfully wrote {len(content)} characters to {clean}."


def tool_read_file(filename: str) -> str:
    """Read a text file from the workspace directory (supports nested subdirectories)."""
    clean = filename.strip().replace("\\", "/").lstrip("/")
    if ".." in clean:
        clean = os.path.basename(clean)
    path = os.path.join(WORKSPACE_DIR, clean)
    if not os.path.exists(path):
        # Fallback to base name in workspace root
        path = os.path.join(WORKSPACE_DIR, os.path.basename(filename))
    if not os.path.exists(path):
        return f"File '{filename}' does not exist."
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
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
    """Read plain text from Windows clipboard with win32 API and tkinter fallback."""
    try:
        import win32clipboard
        import win32con
        for _ in range(5):
            try:
                win32clipboard.OpenClipboard()
                try:
                    if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                        data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                        return data if data else "[Clipboard is empty or holds non-text data]"
                    return "[Clipboard is empty or holds non-text data]"
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                time.sleep(0.04)
        return "[Clipboard busy or unavailable]"
    except ImportError:
        pass
    except Exception as e:
        return f"[Clipboard unavailable: {e}]"

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
    """Copy text into Windows clipboard with win32 API and tkinter fallback."""
    try:
        import win32clipboard
        import win32con
        for _ in range(5):
            try:
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                    return f"Copied {len(text)} chars to clipboard."
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                time.sleep(0.04)
        return "[Clipboard busy or write failed]"
    except ImportError:
        pass
    except Exception as e:
        return f"[Clipboard write failed: {e}]"

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


def tool_media_key(action: str = "play_pause", key: str = "") -> str:
    """Send Windows media key press using native virtual key codes."""
    target = (key or action or "play_pause").lower().strip()
    try:
        from aria.spotify import tool_media_key as sp_media_key
        return sp_media_key(target)
    except Exception:
        if not GUI_AVAILABLE:
            return "[Media key unavailable]"
        k = _MEDIA_ACTIONS.get(target, "playpause")
        try:
            pyautogui.press(k)
            return f"Sent media key: {target}."
        except Exception as e:
            return f"[Media key failed: {e}]"


def tool_volume(action: str = "status", level: int = 50) -> str:
    """Control master system audio volume via pycaw."""
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
        dev = AudioUtilities.GetSpeakers()
        if hasattr(dev, "EndpointVolume"):
            vol = dev.EndpointVolume
        else:
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
    # key_get returns (value, source); we only need the value.
    return (key_get("GMAIL_USER")[0] or "").strip(), \
           (key_get("GMAIL_APP_PASSWORD")[0] or "").replace(" ", "").strip()


def tool_gmail_setup(gmail_user: str, app_password: str) -> str:
    """Store Gmail credentials in aria_keys.json."""
    _KEYS["GMAIL_USER"] = (gmail_user or "").strip()
    _KEYS["GMAIL_APP_PASSWORD"] = (app_password or "").replace(" ", "").strip()
    save_keys()
    return f"Gmail saved for {_KEYS['GMAIL_USER']}. You can now send email with send_email and read it with read_email."


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


def _gmail_imap() -> Tuple[Optional[imaplib.IMAP4_SSL], Optional[str]]:
    """Open an authenticated IMAP connection to Gmail, or (None, error)."""
    user, pw = _gmail_creds()
    if not user or not pw or pw == "INSERT":
        return None, ("Gmail isn't set up yet. Ask the user for their Gmail address and an "
                      "app password (myaccount.google.com/apppasswords - needs 2-Step "
                      "Verification turned on), then call gmail_setup to save them.")
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
        m.login(user, pw)
        return m, None
    except imaplib.IMAP4.error:
        return None, ("Gmail rejected the login. The app password is wrong or was revoked - "
                      "ask the user to generate a fresh one at myaccount.google.com/apppasswords "
                      "and call gmail_setup again.")
    except Exception as e:
        return None, f"Could not reach Gmail over IMAP: {e}"


def _imap_text_part(msg) -> str:
    """Best-effort plain-text body from a parsed email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and \
               "attachment" not in str(part.get("Content-Disposition", "")):
                try:
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    continue
        return ""
    try:
        payload = msg.get_payload(decode=True) or b""
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return str(msg.get_payload())


def tool_read_email(query: str = "", limit: int = 10, unread_only: bool = False,
                    uid: str = "") -> str:
    """Read the user's Gmail over IMAP (same app password as send_email).

    Without uid: searches INBOX (Gmail-style query via X-GM-RAW, e.g.
    "from:boss newer_than:7d") and returns newest matches as one-line
    summaries with uids. With uid: returns the full body of that message.
    Read-only - never marks messages as read.
    """
    m, err = _gmail_imap()
    if err:
        return err
    try:
        m.select("INBOX", readonly=True)
        if uid:
            typ, data = m.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not data or not data[0]:
                return f"No message found with uid {uid}."
            raw = data[0][1] if isinstance(data[0], tuple) else data[0]
            msg = email.message_from_bytes(raw)
            body = _imap_text_part(msg).strip()
            if len(body) > 4000:
                body = body[:4000] + "\n[...truncated...]"
            return (f"From: {msg.get('From', '?')}\n"
                    f"Date: {msg.get('Date', '?')}\n"
                    f"Subject: {msg.get('Subject', '(no subject)')}\n\n{body or '[no text body]'}")

        criteria = []
        if unread_only:
            criteria.append("UNSEEN")
        if query:
            # Gmail's raw search: full Gmail query syntax over IMAP.
            try:
                typ, data = m.uid("SEARCH", None, "X-GM-RAW", query)
                if typ != "OK":
                    raise imaplib.IMAP4.error("X-GM-RAW failed")
            except Exception:
                typ, data = m.uid("SEARCH", None, "TEXT", query)
        else:
            typ, data = m.uid("SEARCH", None, *(criteria or ["ALL"]))
        if typ != "OK":
            return "Gmail search failed."
        uids = (data[0] or b"").split()
        try:
            limit = max(1, min(50, int(limit)))
        except Exception:
            limit = 10
        uids = uids[-limit:][::-1]  # newest first
        if not uids:
            return "No matching emails."
        lines = []
        for u in uids:
            u = u.decode()
            typ, data = m.uid("FETCH", u, "(BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT)])")
            if typ != "OK" or not data or not data[0]:
                continue
            raw = data[0][1] if isinstance(data[0], tuple) else data[0]
            h = email.message_from_bytes(raw)
            frm = (h.get("From") or "?").replace("\n", " ")[:60]
            subj = (h.get("Subject") or "(no subject)").replace("\n", " ")[:80]
            dt = (h.get("Date") or "?")[:31]
            lines.append(f"[uid={u}] {dt} | {frm} | \"{subj}\"")
        add_log(f"Email read: {len(lines)} message(s) listed")
        return f"{len(lines)} email(s), newest first:\n" + "\n".join(lines)
    except Exception as e:
        return f"Could not read Gmail: {e}"
    finally:
        try:
            m.logout()
        except Exception:
            pass


# ---------------- Calendar (live iCal feed) ----------------
def _ical_url() -> str:
    v = key_get("ICAL_URL")[0] or ""
    v = v.strip()
    return "" if v in ("", "INSERT") else v


def tool_calendar_setup(ical_url: str) -> str:
    """Save the Google Calendar secret iCal URL in aria_keys.json."""
    url = (ical_url or "").strip()
    if not url.startswith(("http://", "https://")) or "ics" not in url.lower():
        return ("That doesn't look like an iCal URL. In Google Calendar go to Settings > "
                "your calendar > 'Secret address in iCal format', copy it, and pass it here.")
    _KEYS["ICAL_URL"] = url
    save_keys()
    return ("Calendar connected. I'll read your live schedule from now on - "
            "check it anytime with check_calendar.")


def tool_check_calendar(days: int = 1) -> str:
    """Read upcoming events from the live iCal calendar feed."""
    from aria.ical import fetch_ical, parse_ical, upcoming
    url = _ical_url()
    if not url:
        return ("No calendar connected yet. In Google Calendar go to Settings > "
                "your calendar > 'Secret address in iCal format', copy the URL, "
                "then call calendar_setup with it.")
    try:
        days = max(1, min(14, int(days)))
    except Exception:
        days = 1
    try:
        events = upcoming(parse_ical(fetch_ical(url)), days=days)
    except Exception as e:
        return f"Could not fetch your calendar: {e}"
    if not events:
        return f"Nothing on your calendar for the next {days} day(s)."

    def _ft(dt):
        h = dt.hour % 12 or 12
        return f"{h}:{dt.minute:02d} {'PM' if dt.hour >= 12 else 'AM'}"

    lines = []
    for e in events:
        s, en = e["start"], e["end"]
        day = f"{s.strftime('%a %b')} {s.day}"
        tm = f"{day} (all day)" if e["all_day"] else f"{day}, {_ft(s)} - {_ft(en)}"
        lines.append(f"- {tm}: {e['summary']}")
    add_log(f"Calendar read: {len(events)} event(s) for next {days}d")
    return "\n".join(lines)


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
    count = memory_forget_entries(q)
    add_log(f"Forgot {count} memories matching '{query}'")
    noun = "memory" if count == 1 else "memories"
    return f"Forgot {count} {noun} matching '{query}'."


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
            quar = _KEY_QUARANTINE_UNTIL.get(k, 0) - now
            if quar > 0:
                qcode = _KEY_QUARANTINE_CODE.get(k)
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


# ---------------- Inbox (phone bridge uploads) ----------------

def tool_inbox_list() -> str:
    """List files in ARIA's inbox (phone bridge uploads + laptop drops)."""
    from aria import inbox as inbox_mod
    files = inbox_mod.list_inbox()
    if not files:
        return "Inbox is empty."
    lines = [f"{i + 1}. {f['name']} ({f['size_h']}, {f['when']})"
             for i, f in enumerate(files)]
    return f"Inbox ({len(files)} file(s), newest first):\n" + "\n".join(lines)


def tool_inbox_describe(name: str = "") -> str:
    """Describe an inbox photo with vision. Blank name = latest image."""
    from aria import inbox as inbox_mod
    return inbox_mod.describe_inbox_image(name or "")


def tool_inbox_read(name: str = "") -> str:
    """Read a text file from the inbox. Blank name = latest text file."""
    from aria import inbox as inbox_mod
    return inbox_mod.read_inbox_text(name or "")


# ---------------- Autonomy & Background Workers ----------------

def tool_manage_autonomous_goal(action: str = "list", title: str = "",
                                description: str = "", goal_id: int = 0,
                                interval_s: int = 0, priority: int = 5) -> str:
    """Manage persistent autonomous background goals."""
    import aria.agent as agent_mod
    act = (action or "list").strip().lower()
    if act == "create":
        if not title:
            return "Cannot create goal without a title."
        gid = agent_mod.goal_create(title=title, description=description or title,
                                    interval_s=int(interval_s or 0),
                                    priority=int(priority or 5))
        return f"Autonomous Goal #{gid} '{title}' created (priority {priority}, interval {interval_s}s)."

    if act == "list":
        goals = agent_mod.goal_list()
        if not goals:
            return "No autonomous goals currently registered."
        lines = [f"Autonomous Goals ({len(goals)}):"]
        for g in goals:
            rec = f" (recur {g['interval_s']}s)" if g.get("interval_s") else ""
            lines.append(f"- #{g['id']} [{g.get('status','pending')}] (prio {g.get('priority',5)}) '{g['title']}': {g.get('description','')}{rec}")
            if g.get("last_result"):
                lines.append(f"  Result: {str(g['last_result'])[:120]}")
        return "\n".join(lines)

    if act in ("cancel", "delete"):
        if not goal_id:
            return "Provide a goal_id to cancel."
        ok = agent_mod.goal_cancel(int(goal_id))
        return f"Goal #{goal_id} cancelled." if ok else f"Goal #{goal_id} not found or could not be cancelled."

    if act in ("complete", "done"):
        if not goal_id:
            return "Provide a goal_id to complete."
        ok = agent_mod.goal_complete(int(goal_id), result="Marked complete manually.")
        return f"Goal #{goal_id} completed." if ok else f"Goal #{goal_id} not found."

    return f"Unknown action '{action}'. Use 'create', 'list', 'cancel', or 'complete'."


def tool_manage_background_job(action: str = "list", command: str = "",
                               name: str = "", job_id: int = 0) -> str:
    """Manage asynchronous background jobs and execution supervisor."""
    import aria.agent as agent_mod
    act = (action or "list").strip().lower()
    if act == "start":
        if not command:
            return "Cannot start background job without a command."
        res = agent_mod.start_background_job(command, name=name)
        if "error" in res:
            return f"Failed to start background job: {res['error']}"
        return f"Background job #{res['job_id']} started (PID {res['pid']}): '{res['name']}'. Tracking in worker supervisor."

    if act == "list":
        jobs = agent_mod.list_background_jobs(15)
        if not jobs:
            return "No background jobs registered."
        lines = [f"Background Jobs ({len(jobs)}):"]
        for j in jobs:
            lines.append(f"- #{j['id']} [{j['status']}] (PID {j.get('pid')}) '{j['name']}': cmd='{j.get('command','')[:50]}'")
            if j.get("exit_code") is not None:
                lines.append(f"  Exit code: {j['exit_code']}")
        return "\n".join(lines)

    if act in ("cancel", "kill", "stop"):
        if not job_id:
            return "Provide a job_id to cancel."
        ok = agent_mod.cancel_background_job(int(job_id))
        return f"Background job #{job_id} terminated." if ok else f"Job #{job_id} not found or already stopped."

    if act in ("log", "logs", "output"):
        if not job_id:
            return "Provide a job_id to retrieve logs."
        return agent_mod.get_background_job_log(int(job_id))

    return f"Unknown action '{action}'. Use 'start', 'list', 'cancel', or 'logs'."


def tool_system_health_audit() -> str:
    """Inspect system resources, memory, disks, worker threads, and active jobs."""
    import aria.agent as agent_mod
    return agent_mod.system_health_audit()


def tool_self_heal_diagnose(error_text: str = "", context: str = "") -> str:
    """Diagnose errors and view self-healing incidents."""
    import aria.agent as agent_mod
    return agent_mod.tool_self_heal_diagnose(error_text=error_text, context=context)
