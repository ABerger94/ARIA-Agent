"""Fix B2#2 — phone tools thread an explicit node through to the hub.

_send() used to hardcode HUB.send_command(None, ...), so a command
always landed on _pick(None)'s most-recently-connected node and no
phone tool could ever name a node. The tools now accept an optional
`node` argument: a named node is delivered to exactly that node even
when another node is newer, and a named-but-disconnected node is
refused outright — never silently fallen back to another phone.
Calls that request no node keep the old _pick(None) behaviour.

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import types

pkg = types.ModuleType("aria")
pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "aria")]
sys.modules.setdefault("aria", pkg)

from aria import node_hub
from aria.node_hub import NodeHub

import aria.phone_node as phone_node


def _two_node_hub():
    """Hub with two answering nodes; 'phone-b' is the newest."""
    hub = NodeHub()
    sent = {"phone-a": [], "phone-b": []}

    def make_send(name):
        def send(frame):
            sent[name].append(frame)
            hub.handle_result({"id": frame["id"], "ok": True,
                               "data": {"apps": [{"label": "Maps",
                                                  "package": "com.maps"}]}})
        return send

    hub.register("phone-a", kind="android", capabilities=["apps"],
                 send=make_send("phone-a"))
    hub.register("phone-b", kind="android", capabilities=["apps"],
                 send=make_send("phone-b"))
    # Force the ordering even if the two registrations land in the
    # same clock tick: B is the most recently connected node.
    hub._nodes["phone-a"].connected_at -= 10
    return hub, sent


def test_named_node_delivers_to_named_node_even_when_other_is_newest(monkeypatch):
    hub, sent = _two_node_hub()
    monkeypatch.setattr(phone_node.node_hub, "HUB", hub)
    out = phone_node.tool_phone_list_apps(node="phone-a")
    assert "Maps" in out, out
    assert len(sent["phone-a"]) == 1, sent
    assert sent["phone-b"] == [], sent  # nothing leaked to the newest node


def test_named_disconnected_node_is_refused_and_nothing_sent(monkeypatch):
    hub, sent = _two_node_hub()
    assert hub.unregister("phone-a") is True
    monkeypatch.setattr(phone_node.node_hub, "HUB", hub)
    out = phone_node.tool_phone_list_apps(node="phone-a")
    assert "phone-a" in out, out
    assert "no phone node named 'phone-a' is connected" in out.lower(), out
    assert "nothing was sent to any phone" in out.lower(), out
    assert sent == {"phone-a": [], "phone-b": []}, sent


def test_no_node_request_still_uses_newest_node(monkeypatch):
    hub, sent = _two_node_hub()
    monkeypatch.setattr(phone_node.node_hub, "HUB", hub)
    out = phone_node.tool_phone_list_apps()
    assert "Maps" in out, out
    assert len(sent["phone-b"]) == 1, sent
    assert sent["phone-a"] == [], sent
