"""Pass 27 #23: a phone approval binding must never name a
desktop companion.

Before: phone_node.current_node_name() took the max over ALL
connected nodes by connected_at, while NodeHub._pick(None) —
the dispatch — filters kind != 'desktop'. When a desktop
companion connected most recently, the binding named it, and
the approval replay targeted it by name.

Fix contract pinned here:
- the binding and the dispatch share one selection:
  NodeHub.current_phone_name(), which is _pick(None) — the
  newest non-desktop node, or None;
- a desktop connected most recently never wins the binding;
  with only a desktop connected there is no binding (None);
- the newest phone still wins among phones, and the binding
  still names exactly the phone dispatch would reach.

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import types

pkg = types.ModuleType("aria")
pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "aria")]
sys.modules.setdefault("aria", pkg)

from aria.node_hub import NodeHub

import aria.phone_node as phone_node
import aria.approval as approval


def _hub_with(monkeypatch, nodes):
    """nodes: [(name, kind, connected_at)] — registered in order."""
    hub = NodeHub()
    for name, kind, at in nodes:
        hub.register(name, kind=kind, capabilities=["apps"],
                     send=lambda frame: None)
        hub._nodes[name].connected_at = at
    monkeypatch.setattr(phone_node.node_hub, "HUB", hub)
    return hub


def test_newest_desktop_never_wins_binding(monkeypatch):
    _hub_with(monkeypatch, [
        ("my-phone", "android", 100),
        ("desktop-companion", "desktop", 200),
    ])
    assert phone_node.current_node_name() == "my-phone"
    assert approval._phone_binding({}) == {"node": "my-phone"}


def test_only_desktop_connected_has_no_binding(monkeypatch):
    _hub_with(monkeypatch, [
        ("desktop-companion", "desktop", 200),
    ])
    assert phone_node.current_node_name() is None
    assert approval._phone_binding({}) is None


def test_newest_phone_still_wins_among_phones(monkeypatch):
    _hub_with(monkeypatch, [
        ("phone-a", "android", 100),
        ("phone-b", "ios", 200),
        ("desktop-companion", "desktop", 150),
    ])
    assert phone_node.current_node_name() == "phone-b"
    assert approval._phone_binding({}) == {"node": "phone-b"}


def test_binding_matches_dispatch_selection(monkeypatch):
    """The point of the shared helper: the node the binding
    names is the node an unnamed dispatch reaches."""
    hub = _hub_with(monkeypatch, [
        ("my-phone", "android", 100),
        ("desktop-companion", "desktop", 200),
    ])
    bound = approval._phone_binding({})
    assert bound == {"node": "my-phone"}
    sent = {"my-phone": [], "desktop-companion": []}

    def make_send(name):
        def send(frame):
            sent[name].append(frame)
            hub.handle_result({"id": frame["id"], "ok": True,
                               "data": {"apps": []}})
        return send

    hub._nodes["my-phone"].send = make_send("my-phone")
    hub._nodes["desktop-companion"].send = make_send(
        "desktop-companion")
    out = hub.send_command(None, "list_apps", {})
    assert out == {"apps": []}, out
    assert len(sent["my-phone"]) == 1, sent
    assert sent["desktop-companion"] == [], sent
