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
from aria import outcome

NO_NODE = ("No phone node is connected. Open the ARIA Node app "
           "on the phone and connect it to this bridge (address "
           "and bridge token are under Remote Control / Settings "
           "→ Connections), then ask again.")

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


def _send(tool: str, action: str, args: dict, node: str = None):
    """One command to the node: (data, None) or (None, rendered).

    `node` names the phone node to command. When a node is named
    explicitly, send_command targets exactly that node: a named
    node that is not connected raises no_node and the failure is
    returned as-is — there is no silent fallback to another phone.
    When no node is named, the hub's _pick(None) behaviour applies
    (the most recently connected node).

    Every failure path returns outcome.failed(...) so normalize
    yields FAILED instead of reading a failure as DONE — EXCEPT
    a failure AFTER the command was delivered (pass 27 #30): a
    timeout or disconnect means the phone may have acted and the
    reply was lost, so that returns outcome.unverified(...) with
    the operation identity, never a definite failure. A blind
    retry after a FAILED would be safe; after an UNVERIFIED it
    could double-execute, so the text says to check first.
    """
    try:
        return node_hub.HUB.send_command(node, action, args), None
    except node_hub.NodeHubError as e:
        if e.code == "no_node":
            if node:
                return None, outcome.failed(
                    tool,
                    f"no phone node named '{node}' is connected — "
                    "nothing was sent to any phone")
            return None, outcome.failed(tool, NO_NODE)
        if e.code in ("timeout", "disconnected") or getattr(
                e, "delivered", False):
            return None, outcome.unverified(tool, str(e))
        return None, outcome.failed(tool, str(e))
    except Exception as e:
        return None, outcome.failed(
            tool, f"the phone command failed: {e}")


def current_node_name():
    """Name of the most recently connected phone node, or None.

    The node a live phone call would reach today (NodeHub._pick
    semantics): the binding and the dispatch share the hub's
    current_phone_name selection (pass 27 #23). The old code took
    the max over ALL nodes, so a desktop companion that
    connected most recently was named — and the approval replay
    then targeted it by name. Desktop companions are never
    phones; when only one is connected this returns None.
    """
    try:
        return node_hub.HUB.current_phone_name()
    except Exception:
        return None


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


def _control_guard(tool=None, raw_args=None):
    """The replay refusal, or None when the call is live.

    Both non-live scopes refuse (pass 18, closing the pass-16
    runner-up): delegated tasks run execute_tool with
    preauthorized=False inside delegated_scope(), so checking
    only the preauthorized flag let a delegated tap/type/swipe/
    key reach the phone — the refusal text always claimed
    delegated calls were covered; now the check matches it.
    shelltool and restart_program check both scopes the same
    way.

    The approve replay is exempt (pass 25): under confirm-all
    the approved call replays preauthorized, and the guard used
    to refuse it — the approval was spent, the action died, and
    the token logged "executed". approve_token sets the replay
    marker for exactly the call it is replaying on this thread,
    so a marker for this tool (exact args when the raw args are
    known, name-only when a caller drives the tool function
    directly) is the approved call, not a routine replay.
    """
    try:
        from aria import approval as _approval_mod
        if raw_args is not None:
            if _approval_mod.in_approve_replay(tool, raw_args):
                return None
        elif tool is not None and _approval_mod.in_approve_replay_tool(tool):
            return None
    except Exception:
        pass
    try:
        from aria.tools import dispatch
        if dispatch.in_preauthorized_scope() or \
                dispatch.in_delegated_scope():
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

def tool_phone_status(node: str = None) -> str:
    nodes = node_hub.HUB.nodes()
    if node:
        nodes = [n for n in nodes if n["name"] == node]
        if not nodes:
            return outcome.failed(
                "phone_status",
                f"no phone node named '{node}' is connected — "
                "nothing was sent to any phone")
    elif not nodes:
        return outcome.failed("phone_status", NO_NODE)
    now = time.time()
    lines = []
    for n in nodes:
        caps = ", ".join(str(c) for c in n["capabilities"]) or "none declared"
        lines.append(
            f"Phone node '{n['name']}' ({n['kind'] or 'unknown kind'}) — "
            f"connected {_ago(now - n['connected_at'])}. "
            f"Capabilities: {caps}.")
    return "\n".join(lines)


def tool_phone_list_apps(node: str = None) -> str:
    data, err = _send("phone_list_apps", "list_apps", {}, node=node)
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


def tool_phone_open_app(label: str = "", package: str = "",
                        node: str = None) -> str:
    label = str(label or "").strip()
    package = str(package or "").strip()
    if not label and not package:
        return outcome.failed(
            "phone_open_app",
            "neither an app label nor a package name was given — "
            "name the app to open")
    args = {}
    if package:
        args["package"] = package
    if label:
        args["label"] = label
    data, err = _send("phone_open_app", "open_app", args, node=node)
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


def tool_phone_read_screen(node: str = None) -> str:
    data, err = _send("phone_read_screen", "read_screen", {}, node=node)
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


def tool_phone_notifications(limit=10, node: str = None) -> str:
    try:
        limit = int(float(limit))
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(50, limit))
    data, err = _send("phone_notifications", "notifications",
                      {"limit": limit}, node=node)
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

def tool_phone_tap(ref: str = "", x=None, y=None, node: str = None,
                   _raw_args=None) -> str:
    if (refusal := _control_guard("phone_tap", _raw_args)) is not None:
        return refusal
    ref = str(ref or "").strip()
    args: dict = {}
    if ref:
        args["ref"] = ref
        target = f"element ref '{ref}'"
    else:
        xi, yi = _int_param(x), _int_param(y)
        if xi is None or yi is None:
            return outcome.failed(
                "phone_tap",
                "no target was given — pass a 'ref' from "
                "phone_read_screen, or both 'x' and 'y'")
        args = {"x": xi, "y": yi}
        target = f"coordinates ({xi}, {yi})"
    data, err = _send("phone_tap", "tap", args, node=node)
    return err if err else f"Tapped {target} on the phone."


def tool_phone_swipe(x1=None, y1=None, x2=None, y2=None,
                     duration_ms="", node: str = None, _raw_args=None) -> str:
    if (refusal := _control_guard("phone_swipe", _raw_args)) is not None:
        return refusal
    coords = {"x1": _int_param(x1), "y1": _int_param(y1),
              "x2": _int_param(x2), "y2": _int_param(y2)}
    if any(v is None for v in coords.values()):
        return outcome.failed(
            "phone_swipe",
            "a swipe needs all four coordinates — 'x1', 'y1', "
            "'x2', 'y2' — as numbers")
    duration = _int_param(duration_ms)
    duration = 300 if duration is None else max(50, min(2000, duration))
    args = dict(coords, duration_ms=duration)
    path = (f"({coords['x1']}, {coords['y1']}) → "
            f"({coords['x2']}, {coords['y2']})")
    data, err = _send("phone_swipe", "swipe", args, node=node)
    return err if err else f"Swiped {path} on the phone."


def tool_phone_type(text: str = "", ref: str = "", node: str = None,
                    _raw_args=None) -> str:
    if (refusal := _control_guard("phone_type", _raw_args)) is not None:
        return refusal
    text = str(text or "")
    if not text:
        return outcome.failed("phone_type", "no text was given to type")
    ref = str(ref or "").strip()
    args: dict = {"text": text}
    if ref:
        args["ref"] = ref
    where = f"into element ref '{ref}'" if ref else "into the focused field"
    data, err = _send("phone_type", "type", args, node=node)
    return err if err else f"Typed {where} on the phone."


def tool_phone_key(key: str = "", node: str = None, _raw_args=None) -> str:
    if (refusal := _control_guard("phone_key", _raw_args)) is not None:
        return refusal
    key = str(key or "").strip().lower()
    if key not in _KEY_NAMES:
        return outcome.failed(
            "phone_key",
            f"'{key or '(none)'}' is not a key this tool can send — "
            f"use one of: {', '.join(_KEY_NAMES)}")
    data, err = _send("phone_key", "key", {"key": key}, node=node)
    return err if err else f"Pressed {key} on the phone."
