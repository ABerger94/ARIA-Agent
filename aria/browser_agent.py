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
    the exact browser_agent call, and runs only if he approves it
    (the approved replay is recognized through
    approval.in_approve_replay, so the confirmed click completes
    instead of re-stashing itself). Keyboard activation gets the
    same treatment: Enter/Space on a focused control is checked
    against the focused control (and, for Enter in a form field,
    the form's submit controls) before the key is sent. The one
    exception is approval mode 'auto': a guarded activation
    then runs without the approval step — Alek's ruling,
    2026-10-31: "make auto mean auto", extended to these gates
    the day it was made for run_shell_command. While safe mode
    is on, the auto lane is closed and the guards park in
    every mode.
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

    def focused_info(self):
        """Describe document.activeElement for the press guard.

        Best-effort DOM probe: the focused element's tag/type/role,
        whether keyboard activation would activate it, whether it
        is a form field, a usable label, and the labels of the
        submit/default controls in its form. The press guard treats
        a failed probe as unverifiable, never as safe.
        """
        return self.page.evaluate(r"""() => {
            const clean = (value) =>
                String(value || "").replace(/\s+/g, " ").trim();
            const labelFor = (el) => {
                if (!el) return "";
                let associated = "";
                try {
                    associated = (el.labels && el.labels.length)
                        ? el.labels[0].innerText : "";
                } catch (_) {}
                return clean(el.innerText || el.value ||
                    el.getAttribute("aria-label") || el.title ||
                    el.getAttribute("placeholder") || el.name ||
                    associated || "");
            };
            const typeOf = (el) =>
                clean(el.getAttribute && el.getAttribute("type")
                    || "").toLowerCase();
            const roleOf = (el) =>
                clean(el.getAttribute && el.getAttribute("role")
                    || "").toLowerCase();
            const isActivatable = (el) => {
                const tag = el.tagName.toLowerCase();
                const type = typeOf(el);
                const role = roleOf(el);
                return tag === "button" ||
                    (tag === "a" && el.hasAttribute("href")) ||
                    (tag === "input" && ["submit", "button", "reset",
                        "image", "checkbox", "radio"].includes(type)) ||
                    ["button", "link", "menuitem", "tab", "checkbox",
                     "radio", "switch"].includes(role) ||
                    typeof el.onclick === "function";
            };
            const el = document.activeElement;
            if (!el || el === document.body || el === document.documentElement) {
                return {tag: "body", type: "", role: "",
                        contenteditable: false, label: "",
                        activatable: false, is_form_field: false,
                        form_submit_labels: []};
            }
            const tag = el.tagName.toLowerCase();
            let form = null;
            try {
                form = el.form || (el.closest ? el.closest("form") : null);
            } catch (_) {}
            const submitLabels = [];
            if (form) {
                const controls = form.querySelectorAll(
                    "button, input, [role='button']");
                for (const control of controls) {
                    const ctag = control.tagName.toLowerCase();
                    const ctype = typeOf(control);
                    const isSubmit = (ctag === "input" &&
                            ["submit", "image"].includes(ctype)) ||
                        (ctag === "button" &&
                            (ctype === "" || ctype === "submit"));
                    if (isSubmit) submitLabels.push(labelFor(control));
                }
            }
            return {
                tag: tag,
                type: typeOf(el),
                role: roleOf(el),
                contenteditable: Boolean(el.isContentEditable),
                label: labelFor(el),
                activatable: isActivatable(el),
                is_form_field: ["input", "textarea", "select"].includes(tag)
                    || Boolean(el.isContentEditable),
                form_submit_labels: submitLabels
            };
        }""")

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


# ---------------- the auto lane (Alek's ruling) ----------------

def _safe_mode_on() -> bool:
    """aria.config.is_safe_mode(), read lazily and fail-soft.

    This read only closes the guards' auto lane; it is not the
    safe-mode enforcement itself. An unreadable config therefore
    reads as not-safe-mode here rather than inventing a block
    the enforcement layers did not make. (Same helper shape as
    aria/tools/shelltool.py.)
    """
    try:
        from aria import config as _config
        fn = getattr(_config, "is_safe_mode", None)
        return bool(fn()) if callable(fn) else False
    except Exception:
        return False


def _auto_lane_open(approval_module) -> bool:
    """True when a guarded activation may proceed with no
    approval at all: approval mode 'auto' and safe mode off.

    Alek ruled "make auto mean auto" for shell commands
    (run_shell_command) and extended it the same day to these
    browser gates: a live activation in auto mode executes —
    no pending entry, no banner. In the confirm modes this
    returns False and the guards park exactly as before; while
    safe mode is on the lane is closed in every mode, so auto
    never becomes safe mode's unattended-execution hole. The
    mode is approval's own get_mode() — the same source
    dispatch's needs_approval consults, never a second store;
    anything unreadable (even a missing get_mode) reads as
    not-auto and the activation parks, the pre-ruling
    behaviour (fail-safe).
    """
    try:
        mode = str(approval_module.get_mode() or "").strip().lower()
    except Exception:
        mode = ""
    return mode == "auto" and not _safe_mode_on()


# ---------------- guarded execution ----------------

def _guarded_click(target: str, *, approve_fn=None) -> str:
    """Resolve + label-check a click on the session thread; if the
    label is irreversible, stash an approval and DO NOT click —
    unless this call IS the approved replay of that exact click."""
    def _probe(runner):
        session = runner.ensure_session()
        _loc, label = session._resolve(target)
        return label
    label = _RUNNER.call(_probe)
    if _is_irreversible(label):
        from aria import approval as _approval
        # Approve replay (2026-10-28): approve_token() stashes
        # exactly {"action": "click", "target": target} for
        # browser_agent and sets its replay marker while replaying
        # it — the user already confirmed THIS click on screen, so
        # it goes through. Without this check the replay re-probed
        # the label and stashed a FRESH approval instead of
        # clicking, so a confirmed irreversible click could never
        # complete. Same marker run_shell_command checks
        # (aria/tools/shelltool.py); read defensively so a partial
        # approval module can never turn the guard off.
        try:
            _in_replay = getattr(_approval, "in_approve_replay", None)
            in_replay = bool(_in_replay(
                "browser_agent",
                {"action": "click", "target": target})) \
                if callable(_in_replay) else False
        except Exception:
            in_replay = False
        # Auto lane (see _auto_lane_open): an approved replay, or
        # a live call in auto mode with safe mode off, falls
        # through and clicks; anything else parks below.
        if not in_replay and not _auto_lane_open(_approval):
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


_UNLABELLED_CONTROL = "unlabelled focused control"


def _activation_kind(key):
    """'enter' / 'space' for activation keys, else None.

    Chords are judged by their final segment (Control+Enter is an
    Enter activation). Playwright accepts " ", "Space" and the
    legacy "Spacebar" spelling for the space key.
    """
    raw = str(key or "")
    if raw == " ":
        return "space"
    parts = [p.strip() for p in raw.split("+") if p.strip()]
    if not parts:
        return None
    final = parts[-1].strip().lower()
    if final in ("enter", "numpadenter"):
        return "enter"
    if final in ("space", "spacebar", " "):
        return "space"
    return None


def _focused_activatable(info) -> bool:
    if "activatable" in info:
        return bool(info.get("activatable"))
    tag = str(info.get("tag") or "").strip().lower()
    typ = str(info.get("type") or "").strip().lower()
    role = str(info.get("role") or "").strip().lower()
    return (tag == "button" or tag == "a"
            or (tag == "input" and typ in (
                "submit", "button", "reset", "image", "checkbox", "radio"))
            or role in ("button", "link", "menuitem", "tab", "checkbox",
                        "radio", "switch"))


def _focused_form_field(info) -> bool:
    if "is_form_field" in info:
        return bool(info.get("is_form_field"))
    if info.get("contenteditable") in (True, "true", "True"):
        return True
    tag = str(info.get("tag") or "").strip().lower()
    typ = str(info.get("type") or "").strip().lower()
    if tag in ("textarea", "select"):
        return True
    return tag == "input" and typ not in (
        "submit", "button", "reset", "image", "checkbox", "radio", "file")


def _press_guard_label(info, key):
    """The irreversible label a press would trigger, or None.

    Pure decision logic for _guarded_press. An activation key is
    gated when the focused activatable control's own label is
    irreversible, when that control has no usable label at all
    (fail closed), or — for Enter only — when focus is in a form
    field whose form has an irreversibly-labelled submit/default
    control. Space in a text field is just a space: it never gates
    on the form's submit labels.
    """
    if _activation_kind(key) is None:
        return None
    if not isinstance(info, dict):
        return _UNLABELLED_CONTROL
    label = str(info.get("label") or info.get("text")
                or info.get("name") or "").strip()
    if _focused_activatable(info):
        if _is_irreversible(label):
            return label
        if not label:
            return _UNLABELLED_CONTROL
        return None
    if _activation_kind(key) != "enter":
        return None
    if _focused_form_field(info):
        labels = (info.get("form_submit_labels")
                  or info.get("submit_labels") or [])
        if isinstance(labels, str):
            labels = [labels]
        for candidate in labels:
            candidate = str(candidate or "").strip()
            if _is_irreversible(candidate):
                return candidate
    return None


def _press_needs_guard(info, key) -> bool:
    return _press_guard_label(info, key) is not None


def _guarded_press(key: str, *, approve_fn=None) -> str:
    """Focus-check an activation key before sending it.

    Mirrors _guarded_click: the key's effect depends on what is
    focused, so probe the focused control on the session thread;
    if the press would trigger an irreversible action, stash an
    approval for the exact press call and DO NOT press — unless
    this call IS the approved replay of that exact press. A failed
    probe on an activation key never results in a press.
    Non-activation keys pass through with no probe at all.
    """
    def _do(runner):
        session = runner.ensure_session()
        return session.press(key)

    if _activation_kind(key) is None:
        return _RUNNER.call(_do)

    def _probe(runner):
        session = runner.ensure_session()
        return session.focused_info()

    try:
        info = _RUNNER.call(_probe)
    except Exception:
        return (f"browser_agent: press refused — I could not verify "
                f"the focused control, so '{key}' was not pressed.")
    label = _press_guard_label(info, key)
    if label is None:
        return _RUNNER.call(_do)

    from aria import approval as _approval
    try:
        _in_replay = getattr(_approval, "in_approve_replay", None)
        in_replay = bool(_in_replay(
            "browser_agent", {"action": "press", "key": key})) \
            if callable(_in_replay) else False
    except Exception:
        in_replay = False
    # Auto lane (see _auto_lane_open): an approved replay, or a
    # live press in auto mode with safe mode off, falls through
    # and presses; anything else parks below.
    if not in_replay and not _auto_lane_open(_approval):
        request = approve_fn or _approval.request_approval
        if label == _UNLABELLED_CONTROL:
            desc = (f"Browser agent is about to press '{key}' with "
                    f"an unlabelled control focused — this cannot "
                    f"be proven safe.")
        else:
            desc = (f"Browser agent is about to press '{key}' — "
                    f"this would trigger '{label}', which looks "
                    f"irreversible.")
        return request(
            "browser_agent", {"action": "press", "key": key}, desc=desc)
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
            result = _guarded_press(key)
            add_log(f"[browser] press {key}")
            return result
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
