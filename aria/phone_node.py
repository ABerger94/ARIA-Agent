"""Phone node tools — ARIA's channel to the paired Android phone.

The phone runs the ARIA Node app, which connects OUT to the
bridge's /node/ws WebSocket (aria/bridge.py) and registers with
aria/node_hub.py. These tools are the renderers over that hub,
adapted from Mark-LVI-ARIA actions/phone_node.py (CC BY-NC 4.0 —
see LICENSES/NOTICE.md): same wire actions (list_apps, open_app,
read_screen, notifications, tap, swipe, type, key), same result
shapes from the node, this app's tool conventions.

Control tools (tap / swipe / type / key) never run inside a
routine replay or a preauthorized (delegated) call: they act on
whatever happens to be on the phone's screen at that moment,
which no pre-approved step list can describe. Ask again live.
"""
from __future__ import annotations

import time

from aria import node_hub

NO_NODE = ("[No phone node is connected. Open the ARIA Node app "
           "on the phone and connect it to this bridge (address "
           "and bridge token are under Remote Control / Settings "
           "→ Connections), then ask again.]")

LIST_LIMIT = 6000
APPS_LIST_LIMIT = 30000     # a full app inventory runs past 6,000
_KEY_NAMES = ("back", "home", "recents", "enter")

_REPLAY_REFUSAL = ("[Phone control never runs inside a routine "
                   "replay or a delegated/preauthorized call — a "
                   "tap, swipe, key or typed text acts on whatever "
                   "is on the phone's screen at that moment, which "
                   "no pre-approved step list can describe. Ask for "
                   "it again as a live call. Nothing was sent to "
                   "the phone.]")


def _send(tool: str, action: str, args: dict):
    """One command to the node: (data, None) or (None, rendered)."""
    try:
        return node_hub.HUB.send_command(None, action, args), None
    except node_hub.NodeHubError as e:
        if e.code == "no_node":
            return None, NO_NODE
        return None, f"[{tool} failed: {e}]"
    except Exception as e:
        return None, f"[{tool} failed: the phone command failed: {e}]"


def _cap(text: str, limit: int = LIST_LIMIT) -> str:
    if len(text) > limit:
        return text[:limit] + "\n[list truncated]"
    return text


def _ago(secs: float) -> str:
    secs = max(0, int(secs))
    if secs < 5:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h {secs % 3600 // 60}m ago"


def _control_guard():
    """The replay refusal, or None when the call is live."""
    try:
        from aria.tools import dispatch
        if dispatch.in_preauthorized_scope():
            return _REPLAY_REFUSAL
    except Exception:
        pass
    return None


def _int_param(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ── Read side ─────────────────────────────────────────────────────

def tool_phone_status() -> str:
    nodes = node_hub.HUB.nodes()
    if not nodes:
        return NO_NODE
    now = time.time()
    lines = []
    for n in nodes:
        caps = ", ".join(str(c) for c in n["capabilities"]) or "none declared"
        lines.append(
            f"Phone node '{n['name']}' ({n['kind'] or 'unknown kind'}) — "
            f"connected {_ago(now - n['connected_at'])}. "
            f"Capabilities: {caps}.")
    return "\n".join(lines)


def tool_phone_list_apps() -> str:
    data, err = _send("phone_list_apps", "list_apps", {})
    if err:
        return err
    apps = data.get("apps") or []
    if not apps:
        return "The phone reports no installed apps."
    lines = [f"Apps on the phone ({len(apps)}):"]
    for a in apps:
        if not isinstance(a, dict):
            continue
        label = str(a.get("label") or "").strip()
        pkg = str(a.get("package") or "").strip()
        lines.append(f"- {label} ({pkg})" if label else f"- {pkg}")
    return _cap("\n".join(lines), limit=APPS_LIST_LIMIT)


def tool_phone_open_app(label: str = "", package: str = "") -> str:
    label = str(label or "").strip()
    package = str(package or "").strip()
    if not label and not package:
        return ("[phone_open_app failed: neither an app label nor a "
                "package name was given — name the app to open.]")
    args = {}
    if package:
        args["package"] = package
    if label:
        args["label"] = label
    data, err = _send("phone_open_app", "open_app", args)
    if err:
        return err
    opened = str(data.get("label") or label or package)
    opened_pkg = str(data.get("package") or package)
    return f"Opened {opened} ({opened_pkg}) on the phone."


def _fmt_screen_node(n: dict) -> str:
    cls = str(n.get("class") or "").rsplit(".", 1)[-1] or "view"
    label = (str(n.get("text") or "").strip()
             or str(n.get("desc") or "").strip())
    flags = [name for name in ("clickable", "editable", "scrollable")
             if n.get(name)]
    bounds = n.get("bounds") or []
    loc = ""
    if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
        try:
            loc = " @ [" + ",".join(str(int(v)) for v in bounds) + "]"
        except (TypeError, ValueError):
            loc = ""
    out = f"{n.get('ref')}: {cls}"
    if label:
        out += f' "{label}"'
    if flags:
        out += f" ({', '.join(flags)})"
    return out + loc


def tool_phone_read_screen() -> str:
    data, err = _send("phone_read_screen", "read_screen", {})
    if err:
        return err
    nodes = [n for n in (data.get("nodes") or []) if isinstance(n, dict)]
    pkg = str(data.get("package") or "unknown app")
    head = f"Screen: {pkg} — {len(nodes)} elements"
    if data.get("truncated"):
        head += " (the phone truncated this list)"
    head += "."
    if not nodes:
        return head + " Nothing readable on this screen."
    body = "\n".join(_fmt_screen_node(n) for n in nodes)
    return _cap(head + "\n" + body +
                "\nTo tap an element, pass its line's number to "
                "phone_tap as 'ref'.")


def _fmt_ts(ts) -> str:
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return ""
    if t > 1e12:            # Android reports epoch milliseconds
        t /= 1000.0
    if t <= 0:
        return ""
    try:
        return time.strftime("%a %H:%M", time.localtime(t))
    except (OverflowError, OSError, ValueError):
        return ""


def tool_phone_notifications(limit=10) -> str:
    try:
        limit = int(float(limit))
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(50, limit))
    data, err = _send("phone_notifications", "notifications",
                      {"limit": limit})
    if err:
        return err
    items = [i for i in (data.get("items") or []) if isinstance(i, dict)]
    if not items:
        return "No notifications on the phone."
    lines = [f"Notifications on the phone ({len(items)}):"]
    for it in items:
        app = str(it.get("app") or it.get("package") or "unknown app")
        title = str(it.get("title") or "").strip()
        text = str(it.get("text") or "").strip()
        when = _fmt_ts(it.get("ts"))
        line = f"- [{app}] {title}"
        if text:
            line += f" — {text}"
        if when:
            line += f" ({when})"
        lines.append(line)
    return _cap("\n".join(lines))


# ── Control side ──────────────────────────────────────────────────

def tool_phone_tap(ref: str = "", x=None, y=None) -> str:
    if (refusal := _control_guard()) is not None:
        return refusal
    ref = str(ref or "").strip()
    args: dict = {}
    if ref:
        args["ref"] = ref
        target = f"element ref '{ref}'"
    else:
        xi, yi = _int_param(x), _int_param(y)
        if xi is None or yi is None:
            return ("[phone_tap failed: no target was given — pass "
                    "a 'ref' from phone_read_screen, or both 'x' "
                    "and 'y'.]")
        args = {"x": xi, "y": yi}
        target = f"coordinates ({xi}, {yi})"
    data, err = _send("phone_tap", "tap", args)
    return err if err else f"Tapped {target} on the phone."


def tool_phone_swipe(x1=None, y1=None, x2=None, y2=None,
                     duration_ms="") -> str:
    if (refusal := _control_guard()) is not None:
        return refusal
    coords = {"x1": _int_param(x1), "y1": _int_param(y1),
              "x2": _int_param(x2), "y2": _int_param(y2)}
    if any(v is None for v in coords.values()):
        return ("[phone_swipe failed: a swipe needs all four "
                "coordinates — 'x1', 'y1', 'x2', 'y2' — as numbers.]")
    duration = _int_param(duration_ms)
    duration = 300 if duration is None else max(50, min(2000, duration))
    args = dict(coords, duration_ms=duration)
    path = (f"({coords['x1']}, {coords['y1']}) → "
            f"({coords['x2']}, {coords['y2']})")
    data, err = _send("phone_swipe", "swipe", args)
    return err if err else f"Swiped {path} on the phone."


def tool_phone_type(text: str = "", ref: str = "") -> str:
    if (refusal := _control_guard()) is not None:
        return refusal
    text = str(text or "")
    if not text:
        return "[phone_type failed: no text was given to type.]"
    ref = str(ref or "").strip()
    args: dict = {"text": text}
    if ref:
        args["ref"] = ref
    where = f"into element ref '{ref}'" if ref else "into the focused field"
    data, err = _send("phone_type", "type", args)
    return err if err else f"Typed {where} on the phone."


def tool_phone_key(key: str = "") -> str:
    if (refusal := _control_guard()) is not None:
        return refusal
    key = str(key or "").strip().lower()
    if key not in _KEY_NAMES:
        return (f"[phone_key failed: '{key or '(none)'}' is not a "
                f"key this tool can send — use one of: "
                f"{', '.join(_KEY_NAMES)}.]")
    data, err = _send("phone_key", "key", {"key": key})
    return err if err else f"Pressed {key} on the phone."
