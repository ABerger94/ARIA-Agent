from aria import workspaces
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
import smtplib
from email.message import EmailMessage
import sqlite3
import time
import urllib.parse
import urllib.request
import urllib.error
from aria.outcome import legacy_error_results
from typing import Optional, Tuple, Any

from aria.config import (
    WORKSPACE_DIR, MAX_TOOL_OUTPUT, GITHUB_TOKEN, GITHUB_USERNAME,
    GITHUB_ARMED, PRICE_WATCH_DB, key_get, save_keys, _KEYS
)
from aria.memory import (
    memory_forget_entries,
    memory_save, spine_append, add_log, DB_PATH, DB_LOCK
)
from aria import config as _config_silent
from aria.mailview import decode_mail_header

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


def _is_http_url(url: str) -> bool:
    """True only for absolute http(s) URLs. Everything else — file://,
    ftp://, data:, javascript:, schemeless strings — is not a web fetch,
    and must never reach a URL opener (urllib speaks file:// too)."""
    try:
        return urllib.parse.urlparse(url).scheme.lower() in ("http", "https")
    except Exception:
        return False


def _http_only_opener() -> urllib.request.OpenerDirector:
    """An opener that can speak http(s) only: no FileHandler, no
    FTPHandler, so even an http(s) response redirecting to file://
    cannot escape the scheme gate — the redirect re-enters this opener
    and hits UnknownHandler. ProxyHandler is kept so proxied hosts
    behave exactly as they did under urlopen."""
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


_HTTP_ONLY_OPENER = _http_only_opener()


# Pass 26 B4#8: bounds for unbounded collection. A fetch body or a
# file is never buffered whole — the cap is applied at the read,
# not after the bytes are already in memory.
_MAX_FETCH_BYTES = 2 * 1024 * 1024
_MAX_READ_FILE_BYTES = 2 * 1024 * 1024


def _fetch_html(url: str) -> Optional[str]:
    if not _is_http_url(url):
        return None
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with _HTTP_ONLY_OPENER.open(req, timeout=10) as resp:
            # Read at most cap+1 bytes: a huge or chunked body is
            # bounded here, never buffered whole (the +1 lets the
            # caller tell "exactly at the cap" from "over it").
            # A Content-Length over the cap is honoured the same
            # way — the read stops at the cap regardless.
            try:
                raw = resp.read(_MAX_FETCH_BYTES + 1)
            except TypeError:
                raw = resp.read()
            if isinstance(raw, str):
                return raw[:_MAX_FETCH_BYTES + 1]
            return raw.decode("utf-8", errors="replace")
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
    """Save content to a file in the workspace directory (supports nested subdirectories).

    Pass 26 B1#8: content is validated BEFORE the destination is
    opened (a non-string used to raise TypeError only after "w"
    had already truncated the existing file), and the write goes
    through a temp file in the same directory + os.replace, so a
    failure at any point leaves the original byte-identical.
    """
    from aria.outcome import ResultText, FAILED
    if not isinstance(content, str):
        return ResultText(
            f"[Write failed: content must be text, got "
            f"{type(content).__name__}. Nothing was written.]", FAILED)
    path = workspaces.resolve_workspace_file(filename, WORKSPACE_DIR)
    clean = os.path.relpath(path, workspaces.path('', WORKSPACE_DIR))
    if workspaces.is_reserved_control_file(path, WORKSPACE_DIR):
        return ResultText(
            f"[Write failed: '{clean}' is one of ARIA's own control "
            f"files (it carries settings like safe mode) — file "
            f"tools cannot write it. Nothing was written.]", FAILED)
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    try:
        existing_mode = os.stat(path).st_mode if os.path.exists(path) else None
    except OSError:
        existing_mode = None
    tmp_path = None
    try:
        import tempfile
        fd, tmp_path = tempfile.mkstemp(
            dir=parent_dir or ".", prefix=".aria-write-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            if existing_mode is not None:
                try:
                    os.chmod(tmp_path, existing_mode)
                except OSError:
                    pass
            os.replace(tmp_path, path)
            tmp_path = None
        except Exception:
            raise
    except Exception as e:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return ResultText(f"[Write failed: {e}. The original file was left unchanged.]", FAILED)
    add_log(f"File saved: {clean}")
    return f"Successfully wrote {len(content)} characters to {clean}."


def tool_read_file(filename: str) -> str:
    """Read a text file from the workspace directory (supports nested subdirectories).

    Pass 26 B4#8: the file is stat'ed first and stream-capped at
    _MAX_READ_FILE_BYTES — a multi-MB file is never read whole.
    Oversized reads return the head plus a truncation marker
    naming the total size.
    """
    path = workspaces.resolve_workspace_file(filename, WORKSPACE_DIR)
    if not os.path.exists(path):
        from aria.outcome import ResultText, FAILED
        return ResultText(f"File '{filename}' does not exist.", FAILED)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    with open(path, "rb") as f:
        raw = f.read(_MAX_READ_FILE_BYTES + 1)
    text = raw.decode("utf-8", errors="ignore")
    if size is not None and size > _MAX_READ_FILE_BYTES:
        text = text[:_MAX_READ_FILE_BYTES] + (
            f"\n...[truncated, {size} bytes total]")
    return text


def tool_list_files() -> str:
    """List all files in the workspace directory."""
    root = workspaces.path('', WORKSPACE_DIR)
    if not workspaces.file_allowed(root):
        from aria.outcome import failed
        return failed('list_workspace', 'File is outside this project’s configured roots')
    os.makedirs(root, exist_ok=True)
    files = sorted(os.listdir(root))
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
@legacy_error_results('PyAutoGUI not installed.')
def tool_gui_click(x: int, y: int) -> str:
    """Simulate a mouse click at screen coordinates."""
    if not GUI_AVAILABLE:
        return "PyAutoGUI not installed."
    pyautogui.click(x, y)
    add_log(f"GUI: Clicked ({x}, {y})")
    return f"Clicked coordinates ({x}, {y})."


@legacy_error_results('PyAutoGUI not installed.')
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
    except Exception as _e_silent:
        _config_silent.log_silent("_open_video_downloader", _e_silent)
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
        # os.startfile: no shell, no cmd.exe — model-controlled text cannot
        # inject shell metacharacters the way os.system('start ...') could.
        if tl.startswith(("http://", "https://")):
            os.startfile(t)
            return f"Opened URL: {t}"
        os.startfile(t)
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


@legacy_error_results('[Clipboard busy or write failed]', '[Clipboard write failed:')
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


@legacy_error_results('[pygetwindow not installed]', '[No window matching', '[Focus failed:')
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


@legacy_error_results('[pygetwindow not installed]', '[No window matching', '[Minimize failed:')
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


@legacy_error_results('[pygetwindow not installed]', '[No window matching', '[Close failed:')
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


def tool_media_control(action: str = "status", target: str = "auto",
                       value: str = "") -> str:
    """The unified media surface (Gap fill 6): one entry over the
    built-in video player, system media keys, and master volume.
    Routing rules live in aria.media_control."""
    from aria import media_control
    return media_control.control(action, target=target, value=value)


def tool_volume(action: str = "status", level: int = 50) -> str:
    """Control master system audio volume via the HUD volume manager.

    COM containment (2026-10-21 crash hotfix): this tool used to call
    pycaw INLINE on whatever thread the dispatcher ran it on — no
    CoInitialize, and the endpoint wrappers were dropped into reference
    cycles (the error path's traceback holds the frame, the frame holds
    the locals) for the cyclic GC to finalise on an arbitrary thread.
    The 2026-10-21 faulthandler dump shows exactly that finalisation —
    comtypes Release() on the main thread — killing the process. All
    COM now stays on the VolumeManager's worker thread in aria.hud;
    this function only reads/writes the manager's plain cached state.
    """
    try:
        from aria.hud import (
            get_hud_volume, set_hud_volume, set_hud_mute,
            volume_manager_started,
        )
    except Exception as e:
        return f"[Volume control failed: {e}]"
    if not volume_manager_started():
        return ("[Volume control unavailable: the volume manager "
                "is not running.]")
    a = str(action).lower().strip()
    try:
        if a == "set":
            pct = max(0, min(100, int(level)))
            set_hud_volume(pct / 100.0)
            return f"Volume set to {pct}%."
        if a == "mute":
            set_hud_mute(True)
            return "Muted."
        if a == "unmute":
            set_hud_mute(False)
            return "Unmuted."
        if a == "up":
            set_hud_volume(min(1.0, get_hud_volume() + 0.1))
        elif a == "down":
            set_hud_volume(max(0.0, get_hud_volume() - 0.1))
        cur = int(get_hud_volume() * 100)
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
    from aria.outcome import ResultText, FAILED, UNVERIFIED
    user, pw = _gmail_creds()
    if not user or not pw or pw == "INSERT":
        return ResultText("Gmail isn't set up yet. Ask the user for their Gmail address and an "
                "app password (myaccount.google.com/apppasswords - needs 2-Step "
                "Verification turned on), then call gmail_setup to save them.", FAILED)
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body or "")

    # Two-phase submission (ChatGPT x ARIA loop pass 13): only a
    # failure BEFORE send_message is known not to have sent, so only
    # that phase retries. Once send_message returns, the mail is
    # sent — a later failure (e.g. the audit-log write) must not
    # trigger a resend. And if send_message itself raises, delivery
    # is AMBIGUOUS (the server may have accepted it before the error
    # surfaced), so the outcome is reported as unknown WITHOUT
    # resending: a duplicate is worse than an honest unknown.
    for _sa in range(2):
        attempted, submitted = False, False
        refused = None
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
                s.starttls()
                s.login(user, pw)
                attempted = True
                refused = s.send_message(msg)
                submitted = True
        except Exception as e:
            if not submitted:
                if isinstance(e, smtplib.SMTPAuthenticationError):
                    return ResultText("Gmail rejected the login. The app password is wrong or was revoked - "
                            "ask the user to generate a fresh one at myaccount.google.com/apppasswords "
                            "and call gmail_setup again.", FAILED)
                if attempted:
                    return ResultText(
                        f"[Email send outcome unknown for {to}: the send "
                        f"attempt raised {type(e).__name__} — it may "
                        f"have been delivered. It was NOT resent; check "
                        f"Sent Mail before retrying.]", UNVERIFIED)
                # Only connect / TLS / login failures can safely retry.
                add_log(f"Email send attempt {_sa + 1}/2 failed ({type(e).__name__})")
                if _sa == 0:
                    time.sleep(3)
                continue
            # SMTP.__exit__ sends QUIT and may raise after acceptance. The
            # server already accepted this message; never submit it again.
        # Submitted: the audit log is best-effort from here — its
        # failure must never look like a send failure.
        try:
            add_log(f"Email sent to {to}: {subject}")
        except Exception:
            pass
        if refused:
            return ResultText(
                f"Email accepted for some recipients; refused recipients: "
                f"{', '.join(refused)}. It was NOT resent; check Sent Mail.", UNVERIFIED)
        return f"Email sent to {to}: '{subject}'."
    return ResultText(f"Could not send email to {to}.", FAILED)


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
            return (f"From: {decode_mail_header(msg.get('From', '?'))}\n"
                    f"Date: {msg.get('Date', '?')}\n"
                    f"Subject: {decode_mail_header(msg.get('Subject', '(no subject)'))}\n\n{body or '[no text body]'}")

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
            if typ != "OK":
                return "Gmail search failed."
            uids = (data[0] or b"").split()
            if unread_only:
                # Pass 25: the UNSEEN criterion used to be built
                # and then discarded on the query branch, so
                # unread_only was silently ignored. Intersect with
                # a second UNSEEN search instead.
                typ2, data2 = m.uid("SEARCH", None, "UNSEEN")
                if typ2 != "OK":
                    return "Gmail search failed."
                unseen = set((data2[0] or b"").split())
                uids = [u for u in uids if u in unseen]
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
            frm = decode_mail_header(h.get("From") or "?")[:60]
            subj = decode_mail_header(h.get("Subject") or "(no subject)")[:160]
            dt = (h.get("Date") or "?")[:31]
            lines.append(f"[uid={u}] {dt} | {frm} | \"{subj}\"")
        add_log(f"Email read: {len(lines)} message(s) listed")
        return f"{len(lines)} email(s), newest first:\n" + "\n".join(lines)
    except Exception as e:
        return f"Could not read Gmail: {e}"
    finally:
        try:
            m.logout()
        except Exception as _e_silent:
            _config_silent.log_silent("tool_read_email", _e_silent)


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


@legacy_error_results('Push failed:')
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


@legacy_error_results('Repo creation failed:')
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
    """Write an entry to the daily dated markdown journal.

    Pass 25: the journal directory is resolved per project, so a
    non-default project's entries no longer land in the default
    workspace's journal files alongside other projects' entries.
    """
    spine_append("journal", {"entry": entry})
    try:
        # The legacy dir is computed at call time (not from the
        # import-time JOURNAL_DIR constant) so the project
        # resolution sees the live workspace root.
        journal_dir = workspaces.path(
            "journal", os.path.join(WORKSPACE_DIR, "journal"))
        os.makedirs(journal_dir, exist_ok=True)
        day = datetime.now()
        path = os.path.join(journal_dir, day.strftime("%Y-%m-%d") + ".md")
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
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    journal_write(f"Note to self: {text}")
    try:
        # Pass 25: minute-resolution keys collided — two notes in
        # one minute upserted the same key and the first was lost.
        # Seconds+microseconds keep every note its own row.
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
        conn = sqlite3.connect(workspaces.path("aria_memory.db", DB_PATH))
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
    # Pass 26 B5#1: the price-watch DB is per-project — resolve
    # through workspaces.path() so each project gets its own
    # price_watches.db; the default project keeps the legacy
    # file (PRICE_WATCH_DB) exactly as before.
    try:
        db_path = workspaces.path("price_watches.db", PRICE_WATCH_DB)
    except Exception:
        db_path = PRICE_WATCH_DB
    conn = sqlite3.connect(db_path)
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
    # Pass 26 B5#2: boot-time wiring is the only other caller of
    # ensure_pricecheck_task, and it runs once with whatever
    # watches exist then — a boot with zero watches scheduled
    # nothing, so a watch added later never got hourly checks
    # until restart. Best-effort and fail-soft: a scheduling
    # failure must never break adding the watch.
    try:
        from aria import pricecheck as _pricecheck_mod
        _pricecheck_mod.ensure_pricecheck_task()
    except Exception:
        pass
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


# ---------------------------------------------------------------------------
# MCP client tools — ARIA connects to MCP servers and uses their tools
# ---------------------------------------------------------------------------

def tool_mcp_setup(name: str = "", command: str = "", args: str = "",
                   url: str = "", env: str = "", headers: str = "",
                   transport: str = "") -> str:
    """Add an MCP server configuration. stdio needs command+args; sse/http need url.

    transport is optional: pass "http" for a Streamable HTTP endpoint
    (e.g. Robinhood), "sse" only for a legacy SSE endpoint. When it is
    omitted the legacy inference is kept exactly (command -> stdio,
    url -> sse) so existing setups are unaffected. An invalid value is
    rejected by add_server and saves nothing.
    """
    from aria import mcp as mcp_mod
    transport = (transport or "").strip().lower()
    if not transport:
        transport = "stdio" if command else ("sse" if url else "stdio")
    return mcp_mod.add_server(name=name, transport=transport, command=command,
                              args=args, env=env, url=url, headers=headers)


def tool_mcp_connect(name: str = "") -> str:
    """Connect to one MCP server (by name) or all enabled servers; loads their tools."""
    from aria import mcp as mcp_mod
    bridge = mcp_mod.get_bridge()
    if name and name.strip():
        ok, msg = bridge.connect(name.strip())
        return msg
    servers = mcp_mod.get_servers()
    enabled = [n for n, c in servers.items() if c.get("enabled", True)]
    if not enabled:
        return ("No MCP servers configured yet. Add one with mcp_setup, e.g. "
                "mcp_setup(name='filesystem', command='npx', "
                "args='[\"-y\", \"@modelcontextprotocol/server-filesystem\", \"C:/data\"]').")
    lines = []
    for n in enabled:
        ok, msg = bridge.connect(n)
        lines.append(("OK  " if ok else "FAIL") + f" {msg}")
    return "\n".join(lines)


def tool_mcp_disconnect(name: str = "") -> str:
    """Disconnect one MCP server (by name) or all; unloads their tools."""
    from aria import mcp as mcp_mod
    bridge = mcp_mod.get_bridge()
    if name and name.strip():
        ok, msg = bridge.disconnect(name.strip())
        return msg
    return bridge.disconnect_all()


def tool_mcp_list_servers() -> str:
    """List configured MCP servers with connection status and tool counts."""
    from aria import mcp as mcp_mod
    rows = mcp_mod.get_bridge().status()
    if not rows:
        return ("No MCP servers configured. Add one with mcp_setup, e.g. "
                "mcp_setup(name='filesystem', command='npx', "
                "args='[\"-y\", \"@modelcontextprotocol/server-filesystem\", \"C:/data\"]').")
    lines = []
    for r in rows:
        state = f"connected ({r['tools']} tools)" if r["connected"] else "disconnected"
        if not r["enabled"]:
            state += ", disabled"
        lines.append(f"- {r['name']} [{r['transport']}] — {state}")
    lines.append("MCP tools appear as mcp_<server>__<tool>; they live in the 'mcp' toolkit.")
    return "\n".join(lines)


def tool_mcp_remove_server(name: str = "") -> str:
    """Remove an MCP server configuration entirely (disconnects it first)."""
    from aria import mcp as mcp_mod
    if not (name or "").strip():
        return "Tell me which server to remove, e.g. mcp_remove_server(name='filesystem')."
    return mcp_mod.remove_server(name.strip())


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
        if ok:
            return f"Background job #{job_id} terminated."
        from aria.outcome import unverified
        return unverified('manage_background_job',
                          f'No owned running job #{job_id} could be stopped; check the process on this computer')

    if act in ("log", "logs", "output"):
        if not job_id:
            return "Provide a job_id to retrieve logs."
        return agent_mod.get_background_job_log(int(job_id))

    return f"Unknown action '{action}'. Use 'start', 'list', 'cancel', or 'logs'."


def tool_system_health_audit() -> str:
    """Inspect system resources, memory, disks, worker threads, and active jobs."""
    # Import from the DEFINING submodule, not via the aria.agent
    # package: the package-attribute form only works when
    # aria/agent/__init__ has already executed its re-exports, so
    # these tools broke with AttributeError in any context that
    # reached builtins first (headless runs, background workers —
    # caught by the registry self-test, tests/test_registry.py).
    from aria.agent.workers import system_health_audit
    return system_health_audit()


def tool_self_heal_diagnose(error_text: str = "", context: str = "") -> str:
    """Diagnose errors and view self-healing incidents."""
    from aria.agent.self_healing import (
        tool_self_heal_diagnose as _diagnose)
    return _diagnose(error_text=error_text, context=context)
