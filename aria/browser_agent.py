"""ARIA supervised browser agent (Alek, approved 2026-10-28).

Build 3 of 3. `browser_control` can open a page or fetch text; this
is a real driver: it navigates, clicks, types, scrolls and reads in
a live Chromium window the user can watch, one explicit action per
tool call, every action written to the Activity log.

Safety shape (the design Alek approved):
  * HER OWN browser profile (WORKSPACE_DIR/browser_profile) — she
    never drives the user's real Edge/Chrome, never inherits his
    logins. She can only act on accounts he signs her into there.
  * Irreversible actions HALT: a click whose label matches pay /
    place order / buy / send / post / delete / book / confirm is
    never executed cold — it is stashed as a pending approval for
    the exact browser_agent call, and runs only if he approves it.
  * Private / loopback URLs are refused (router admin pages, the
    phone bridge itself) unless the browser_agent_allow_lan
    setting is on.
  * Everything runs on one dedicated session thread (Playwright's
    sync API is thread-affine) behind a command queue, so tool
    calls from any worker thread are safe.

If Playwright is not installed the tool says so honestly, with the
install commands, instead of failing obscurely.
"""

import os
import queue
import re
import threading
import urllib.parse

from aria.config import WORKSPACE_DIR, add_log, get_setting

PROFILE_DIR = os.path.join(WORKSPACE_DIR, "browser_profile")
SHOTS_DIR = os.path.join(WORKSPACE_DIR, "browser_shots")

_UNION = "a, button, input, select, textarea, [role='button']"
_MAX_ELEMENTS = 60

_IRREVERSIBLE = re.compile(
    r"(pay\b|payment|check\s?out|place (an )?order|buy now|purchase|"
    r"send\b|post\b|publish|delete|remove item|confirm booking|"
    r"book now|complete order|submit order|subscribe now)",
    re.I)

def _is_private_host(host: str) -> bool:
    host = (host or "").lower()
    if host in ("localhost", "::1") or host.endswith(".local"):
        return True
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        a, b = int(parts[0]), int(parts[1])
        return (a in (10, 127) or (a == 192 and b == 168)
                or (a == 172 and 16 <= b <= 31))
    return False


def _url_blocked(url: str) -> str:
    """'' if the URL may be opened, else the refusal reason."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return "that URL could not be parsed."
    if parsed.scheme not in ("http", "https"):
        return f"scheme '{parsed.scheme}' is not allowed (http/https only)."
    host = parsed.hostname or ""
    if _is_private_host(host) and not get_setting(
            "browser_agent_allow_lan", False):
        return (f"{host} is a private/loopback address — refused. "
                "Set browser_agent_allow_lan to allow it.")
    return ""


def _is_irreversible(label: str) -> bool:
    return bool(label and _IRREVERSIBLE.search(label))


# ---------------- the session (Playwright, one thread) ----------------

class BrowserSession:
    """A live Chromium + page. Only ever touched on the runner thread."""

    def __init__(self):
        self._pw = None
        self._ctx = None
        self.page = None
        self.elements = []      # display idx -> (real idx, tag, text)
        self.log = []           # step log lines

    def start(self):
        from playwright.sync_api import sync_playwright
        os.makedirs(PROFILE_DIR, exist_ok=True)
        self._pw = sync_playwright().start()
        headless = bool(get_setting("browser_agent_headless", False))
        self._ctx = self._pw.chromium.launch_persistent_context(
            PROFILE_DIR, headless=headless)
        self.page = self._ctx.pages[0] if self._ctx.pages \
            else self._ctx.new_page()
        self.page.set_default_timeout(15000)

    def close(self):
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()
            self._ctx = self.page = self._pw = None

    # -- actions --
    def open(self, url):
        self.page.goto(url, wait_until="domcontentloaded")
        return self.read()

    def read(self):
        title = self.page.title()
        url = self.page.url
        text = ""
        try:
            text = self.page.inner_text("body")[:4000]
        except Exception:
            pass
        self.elements = []
        loc = self.page.locator(_UNION)
        lines = [f"Page: {title} ({url})", "", "Interactive elements:"]
        shown = 0
        for i in range(min(loc.count(), 200)):
            if shown >= _MAX_ELEMENTS:
                break
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                label = (el.inner_text(timeout=400)
                         or el.get_attribute("value")
                         or el.get_attribute("aria-label")
                         or el.get_attribute("placeholder") or "").strip()
            except Exception:
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            self.elements.append((i, tag, label[:60]))
            lines.append(f"[{shown}] <{tag}> {label[:60]}")
            shown += 1
        if text:
            lines += ["", "Text:", text]
        return "\n".join(lines)

    def _resolve(self, target):
        """Target: 'index:N', a CSS selector, or 'text=...' -> locator."""
        if target.startswith("index:"):
            idx = int(target.split(":", 1)[1])
            real, _tag, label = self.elements[idx]
            return self.page.locator(_UNION).nth(real), label
        if target.startswith("text="):
            label = target[5:]
            return self.page.get_by_text(label, exact=False).first, label
        label = ""
        try:
            label = (self.page.locator(target).first
                     .inner_text(timeout=800) or "").strip()
        except Exception:
            pass
        return self.page.locator(target).first, label

    def click(self, target):
        loc, label = self._resolve(target)
        loc.click()
        self.page.wait_for_timeout(600)
        return label

    def type_text(self, target, text):
        loc, _label = self._resolve(target)
        loc.fill(text)
        return f"typed {len(text)} chars"

    def press(self, key):
        self.page.keyboard.press(key)
        return f"pressed {key}"

    def scroll(self, direction):
        dy = -600 if direction == "up" else 600
        self.page.mouse.wheel(0, dy)
        return f"scrolled {direction}"

    def back(self):
        self.page.go_back()
        return self.read()

    def screenshot(self):
        os.makedirs(SHOTS_DIR, exist_ok=True)
        path = os.path.join(SHOTS_DIR, "last.png")
        self.page.screenshot(path=path)
        return path


# ---------------- the runner (session thread + queue) ----------------

class _Runner:
    def __init__(self, session_factory=None):
        self._q = queue.Queue()
        self._thread = None
        self.session = None
        self._factory = session_factory

    def _loop(self):
        while True:
            fn, done, box = self._q.get()
            try:
                box.append(("ok", fn(self)))
            except Exception as exc:
                box.append(("err", exc))
            finally:
                done.set()

    def call(self, fn, timeout=90):
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="aria-browser-agent")
            self._thread.start()
        done = threading.Event()
        box = []
        self._q.put((fn, done, box))
        if not done.wait(timeout):
            raise TimeoutError("browser action timed out")
        kind, value = box[0]
        if kind == "err":
            raise value
        return value

    def ensure_session(self):
        if self.session is None:
            self.session = (self._factory or BrowserSession)()
            self.session.start()
        return self.session

    def drop_session(self):
        if self.session is not None:
            try:
                self.session.close()
            finally:
                self.session = None


_RUNNER = _Runner()


def set_runner_for_tests(runner) -> None:
    global _RUNNER
    _RUNNER = runner


# ---------------- guarded execution ----------------

def _guarded_click(target: str, *, approve_fn=None) -> str:
    """Resolve + label-check a click on the session thread; if the
    label is irreversible, stash an approval and DO NOT click."""
    def _probe(runner):
        session = runner.ensure_session()
        _loc, label = session._resolve(target)
        return label
    label = _RUNNER.call(_probe)
    if _is_irreversible(label):
        from aria import approval as _approval
        request = approve_fn or _approval.request_approval
        return request(
            "browser_agent", {"action": "click", "target": target},
            desc=f"Browser agent is about to click '{label}' "
                 f"({target}) — this looks irreversible.")
    def _do(runner):
        session = runner.ensure_session()
        clicked = session.click(target)
        return f"Clicked '{clicked or target}'. " + session.read()
    return _RUNNER.call(_do)


# ---------------- the tool ----------------

def tool_browser_agent(args) -> str:
    args = args or {}
    action = str(args.get("action") or "").strip().lower()
    target = str(args.get("target") or "").strip()
    try:
        if action == "open":
            url = str(args.get("url") or target).strip()
            if not url:
                return "browser_agent: open needs a 'url'."
            blocked = _url_blocked(url)
            if blocked:
                return f"browser_agent: refused — {blocked}"
            result = _RUNNER.call(
                lambda r: r.ensure_session().open(url))
            add_log(f"[browser] open {url}")
            return result
        if action == "read":
            return _RUNNER.call(lambda r: r.ensure_session().read())
        if action == "click":
            if not target:
                return "browser_agent: click needs a 'target'."
            result = _guarded_click(target)
            add_log(f"[browser] click {target}")
            return result
        if action == "type":
            text = str(args.get("text") or "")
            if not target:
                return "browser_agent: type needs a 'target'."
            result = _RUNNER.call(
                lambda r: r.ensure_session().type_text(target, text))
            add_log(f"[browser] type into {target} ({len(text)} chars)")
            return result + ". Call read to see the page."
        if action == "press":
            key = str(args.get("key") or "Enter")
            return _RUNNER.call(
                lambda r: r.ensure_session().press(key))
        if action == "scroll":
            direction = str(args.get("direction") or "down")
            return _RUNNER.call(
                lambda r: r.ensure_session().scroll(direction))
        if action == "back":
            return _RUNNER.call(lambda r: r.ensure_session().back())
        if action == "screenshot":
            path = _RUNNER.call(
                lambda r: r.ensure_session().screenshot())
            return f"Screenshot saved: {path}"
        if action == "close":
            _RUNNER.call(lambda r: r.drop_session())
            add_log("[browser] session closed")
            return "Browser session closed."
        return (f"browser_agent: unknown action '{action}' — use open, "
                "read, click, type, press, scroll, back, screenshot, "
                "or close.")
    except ImportError:
        return ("browser_agent: Playwright is not installed. Install "
                "it with: pip install playwright, then: playwright "
                "install chromium — after that I can drive a browser.")
    except Exception as exc:
        return f"browser_agent: {action} failed: {exc}"
