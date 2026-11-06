"""Phone approvals are bound to the phone they were parked against.

Pass 26 B2#2 (wave 2): phone_node._send used to call
HUB.send_command(None, ...) and NodeHub._pick(None) returns the
most recently connected phone — so parking a tap with phone A
connected, connecting phone B, then confirming sent the tap to B.
Now request_approval captures the connected node at park time
(explicit `node` arg wins, else the currently newest node) and
approve_token replays the call against exactly that node; a
named node that is no longer connected is refused with nothing
sent to any phone.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import re
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMPHOME = tempfile.mkdtemp(prefix='aria_phone_binding_')

pkg = types.ModuleType("aria")
pkg.__path__ = [str(Path(__file__).parents[1] / "aria")]
sys.modules.setdefault("aria", pkg)

for _name in ("aria.scheduler", "aria.speech", "aria.hud",
              "aria.frame", "aria.vision", "aria.hardware",
              "aria.agent.taskqueue"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

import pytest

phone_node = importlib.import_module("aria.phone_node")
node_hub = importlib.import_module("aria.node_hub")
dispatch = importlib.import_module("aria.tools.dispatch")
approval = importlib.import_module("aria.approval")
from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, 'ARIA')

SENT = []
NODES = []  # list of dicts: {name, connected_at, kind, capabilities}


def _fresh():
    SENT.clear()
    NODES.clear()
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    approval._pending_loaded = True  # keep the tmp store empty
    os.environ.pop('ARIA_SAFE_MODE', None)


class _FakeHub:
    def nodes(self):
        return [dict(n) for n in NODES]

    def send_command(self, node, action, args):
        if node is not None:
            if not any(n["name"] == node for n in NODES):
                raise node_hub.NodeHubError(
                    "no_node", f"no phone node named '{node}' is connected")
        elif not NODES:
            raise node_hub.NodeHubError("no_node", "no phone node connected")
        SENT.append((node, action, args))
        return {"ok": True}


@pytest.fixture(autouse=True)
def hub(monkeypatch):
    _fresh()
    monkeypatch.setattr(phone_node.node_hub, "HUB", _FakeHub())
    yield
    SENT.clear()
    NODES.clear()


def _park(tool, args):
    out = approval.request_approval(tool, dict(args))
    m = re.search(r"token=([0-9a-f]{8})", out)
    assert m, out
    return m.group(1)


def _connect(name, at):
    NODES.append({"name": name, "connected_at": at,
                  "kind": "phone", "capabilities": []})


def test_approve_sends_to_parked_phone_not_newest():
    _connect("A", 100)
    token = _park("phone_tap", {"x": 10, "y": 20})
    _connect("B", 200)  # B is now the newest phone
    result = approval.approve_token(token)
    assert "Tapped" in result, result
    assert len(SENT) == 1
    assert SENT[0][0] == "A"  # the parked phone, not the newest
    assert SENT[0][1] == "tap"


def test_approve_refuses_when_parked_phone_gone():
    _connect("A", 100)
    token = _park("phone_tap", {"x": 10, "y": 20})
    NODES.clear()
    _connect("B", 200)  # A disconnected; B is connected instead
    result = approval.approve_token(token)
    assert "no phone node named 'A'" in result, result
    assert SENT == []  # nothing was sent to B (or anywhere)


def test_explicit_node_arg_is_the_binding():
    _connect("A", 200)  # A is newest, but the call names B
    _connect("B", 100)
    token = _park("phone_tap", {"x": 10, "y": 20, "node": "B"})
    result = approval.approve_token(token)
    assert "Tapped" in result, result
    assert len(SENT) == 1
    assert SENT[0][0] == "B"


def test_binding_persists_across_save_load():
    _connect("A", 100)
    token = _park("phone_tap", {"x": 10, "y": 20})
    _connect("B", 200)
    # Simulate a restart: drop the in-memory store, reload from disk.
    approval._PENDING.clear()
    approval._pending_loaded = False
    assert approval.load_pending() == 1
    result = approval.approve_token(token)
    assert "Tapped" in result, result
    assert len(SENT) == 1
    assert SENT[0][0] == "A"


def test_no_node_at_park_leaves_unbound():
    token = _park("phone_tap", {"x": 10, "y": 20})
    result = approval.approve_token(token)
    assert "No phone node is connected" in result, result
    assert SENT == []


def test_open_app_is_bound_too():
    _connect("A", 100)
    token = _park("phone_open_app", {"label": "Maps"})
    _connect("B", 200)
    result = approval.approve_token(token)
    assert "Opened" in result, result
    assert len(SENT) == 1
    assert SENT[0][0] == "A"
    assert SENT[0][1] == "open_app"


def test_banner_names_the_parked_phone():
    _connect("A", 100)
    out = approval.request_approval("phone_tap", {"x": 10, "y": 20})
    assert "on phone 'A'" in out, out
