"""Fix B3#4 — phone failures normalize to FAILED, not DONE.

phone_node's failure paths used to return bare bracketed strings
("[No phone node is connected. ...]", "[phone_tap failed: ...]"),
none of which matches a FAILED prefix, so outcome.normalize mapped
every phone failure to DONE — a phone that was never reached read
as a success. The failure paths now return outcome.failed(...),
so normalize yields FAILED.

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
from aria import outcome


def test_no_node_phone_call_normalizes_failed(monkeypatch):
    monkeypatch.setattr(phone_node.node_hub, "HUB", NodeHub())
    for out in (phone_node.tool_phone_list_apps(),
                phone_node.tool_phone_status(),
                phone_node.tool_phone_read_screen()):
        assert outcome.normalize(out).status == outcome.FAILED, out


def test_named_disconnected_node_normalizes_failed(monkeypatch):
    hub = NodeHub()
    hub.register("phone-b", kind="android", capabilities=[],
                 send=lambda frame: None)
    monkeypatch.setattr(phone_node.node_hub, "HUB", hub)
    out = phone_node.tool_phone_list_apps(node="phone-b".replace("b", "a"))
    assert outcome.normalize(out).status == outcome.FAILED, out
    assert "phone-a" in out, out


def test_node_error_normalizes_failed(monkeypatch):
    hub = NodeHub()

    def _boom(frame):
        raise RuntimeError("transport down")

    hub.register("phone", kind="android", capabilities=[], send=_boom)
    monkeypatch.setattr(phone_node.node_hub, "HUB", hub)
    out = phone_node.tool_phone_list_apps()
    assert outcome.normalize(out).status == outcome.FAILED, out


def test_success_still_normalizes_done(monkeypatch):
    hub = NodeHub()

    def send(frame):
        hub.handle_result({"id": frame["id"], "ok": True,
                           "data": {"apps": [{"label": "Maps",
                                              "package": "com.maps"}]}})

    hub.register("phone", kind="android", capabilities=[], send=send)
    monkeypatch.setattr(phone_node.node_hub, "HUB", hub)
    out = phone_node.tool_phone_list_apps()
    assert outcome.normalize(out).status == outcome.DONE, out
    assert "Maps" in out, out
