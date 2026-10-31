"""Phone-bridge approval surface (Alek, 2026-10-31): "Confirmation
bands, etc need to show up on the phone bridge as well when asked
for confirmation."

Before this round the pending-approval store had exactly two
surfaces — the desktop dashboard's ConfirmBanner and the Shell
Home action cards — so a call ARIA parked while Alek was away
from the laptop could only be resolved at the laptop. The bridge
now carries a third surface on the SAME store:

  * GET  /api/approvals          -> {"pending": [...]} — the
    pending_entries() snapshot (full untruncated desc + the token
    handle), behind the bridge's existing auth;
  * POST /api/approvals/resolve  -> {"ok", "result", "pending"} —
    Confirm calls approval.approve_token, Cancel calls
    approval.deny_token. There is NO second execution route: the
    entry is popped once by the shared functions, so a request
    resolved on the phone is gone from the banner and the Shell
    cards too, and the replay rides the approve-replay marker
    (the 2026-10-31 dead-Confirm fix) exactly as a banner tap does;
  * the phone page renders a confirmation band at the top (hidden
    when nothing is pending, polled on the page's existing cadence)
    with phone-sized Confirm/Cancel buttons — type=button outside
    any form, Cancel first, so Enter can never confirm.

Delivered-reminder cards are NOT in this store (they ride
aria.reminder_gate.pending_cards) and are deliberately untouched.

Harness: the real BridgeHandler on an ephemeral port, the real
aria.approval store, and — for the exactly-once pin — the real
dispatch.execute_tool + shelltool chain (nothing in the approval
path is faked; peripheral subsystems are stubbed at the
sys.modules seam, as in tests/test_bridge.py /
test_approve_replay_dupguard.py). The bridge's own add_log is
captured to pin that the new endpoints never write an approval
token to a log.
"""
import http.client
import importlib
import json
import os
import sys
import tempfile
import threading
import types
import uuid
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")


def _pkg(name, path):
    m = types.ModuleType(name)
    m.__path__ = [path]
    sys.modules[name] = m
    return m


_pkg("aria", PKG)
_pkg("aria.tools", os.path.join(PKG, "tools"))
_pkg("aria.agent", os.path.join(PKG, "agent"))
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

_vision = types.ModuleType("aria.vision")
_vision.get_face_frame_jpeg = lambda: None
_vision.publish_phone_frame = lambda jpeg: True
_vision.get_phone_frame_jpeg = lambda: None
_vision.get_phone_frame_status = lambda: {"active": False, "age_s": -1.0}
_vision.describe_phone_view = lambda q="": "[stubbed]"
sys.modules["aria.vision"] = _vision
_speech = types.ModuleType("aria.speech")
_speech.tts_bytes_for_bridge = lambda text: (b"", "no speech in tests")
_speech.transcribe_audio = lambda audio, mime: ""
sys.modules["aria.speech"] = _speech

# Peripheral subsystems dispatch binds at import time but the
# approval chain never calls (same seam as the dupguard suite).
for _name in ("aria.scheduler", "aria.hud", "aria.frame",
              "aria.hardware", "aria.spotify", "aria.agent.taskqueue"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_bridge_appr_"), "test.db")
memory.init_databases()
memory._embed = lambda text: None
data_dir = importlib.import_module("aria.data_dir")
approval = importlib.import_module("aria.approval")
dispatch = importlib.import_module("aria.tools.dispatch")
bridge = importlib.import_module("aria.bridge")

_TOKEN = "-".join(["bridge", "approvals", "test", "token"])


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """Real bridge server + real approval store rooted in tmp."""
    monkeypatch.setattr(data_dir, "_ROOT", str(tmp_path / "install"))
    ops_logs = []
    monkeypatch.setattr(
        config, "add_log", lambda msg, *a, **k: ops_logs.append(str(msg)))
    bridge_logs = []
    monkeypatch.setattr(
        bridge, "add_log", lambda msg, *a, **k: bridge_logs.append(str(msg)))
    bridge.BRIDGE_TOKEN = _TOKEN
    bridge.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria_bridge_appr_ws_")
    bridge.lan_ip = lambda: "192.168.99.10"
    bridge.get_setting = lambda key, default=None: default
    bridge.BRIDGE_SCHEME = "http"
    bridge.BRIDGE_CERT = None
    bridge.BRIDGE_KEY = None
    approval._PENDING.clear()
    dispatch.reset_turn_state()
    assert approval.set_mode("confirm-risky").startswith(
        "Approval mode set to 'confirm-risky'")

    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bridge.BridgeHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield types.SimpleNamespace(
        port=srv.server_address[1], tmp=tmp_path,
        bridge_logs=bridge_logs, ops_logs=ops_logs)
    approval._PENDING.clear()
    dispatch.reset_turn_state()
    srv.shutdown()
    srv.server_close()


def _req(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        payload = json.dumps(body) if body is not None else None
        hdrs = dict(headers or {})
        if payload is not None:
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _auth():
    return {"X-Bridge-Token": _TOKEN}


def _pending(port):
    status, body = _req(port, "GET", "/api/approvals", _auth())
    assert status == 200, (status, body)
    return json.loads(body)["pending"]


def _park_shell(tmp):
    """One live run_shell_command through the real dispatcher:
    parks (confirm-risky) without executing. Returns marker path."""
    tag = uuid.uuid4().hex[:8]
    marker = tmp / f"pulled-{tag}.txt"
    args = {"command": f"echo pulled-{tag} > {marker.name}",
            "cwd": str(Path(tmp).resolve()), "timeout_seconds": 60}
    result, _ok = dispatch.execute_tool("run_shell_command", args)
    assert "AWAITING_APPROVAL" in str(result), result
    assert not marker.exists()
    return marker


def test_list_empty_and_band_hidden_state(server):
    # The list endpoint answers, empty, behind auth.
    assert _pending(server.port) == []
    # The page carries the band: hidden by default, shown only when
    # entries exist, fed by the endpoint on a poll.
    html = bridge.BRIDGE_HTML
    assert 'id="approvals"' in html and 'id="approval-list"' in html
    assert 'id="approvals" class="panel" hidden' in html
    assert "/api/approvals" in html and "loadApprovals" in html
    assert "box.hidden=!es.length" in html
    assert "setInterval(loadApprovals, 5000)" in html
    # Safe-default affordances: both buttons are type=button (no
    # form, so Enter can never confirm) and Cancel is wired first.
    assert "cancel.type='button'" in html
    assert "confirm.type='button'" in html
    assert html.index("appendChild(cancel)") < html.index(
        "appendChild(confirm)")
    # Full text, never truncated, never parsed as HTML.
    assert "desc.textContent=e.desc||''" in html
    print("ok list_empty_and_band_hidden_state")


def test_list_entries_full_text(server):
    long_desc = ("run shell command 'git pull' in 'E:\\\\ARIA' — "
                 + "detail " * 40)  # ~340 chars, past any summary cut
    approval.request_approval("run_shell_command",
                              {"command": "git pull"}, desc=long_desc)
    approval.request_approval("send_email", {"to": "a@b.c"},
                              desc="send email to 'a@b.c'")
    entries = _pending(server.port)
    assert len(entries) == 2
    by_tool = {e["tool"]: e for e in entries}
    assert by_tool["run_shell_command"]["desc"] == long_desc
    assert by_tool["send_email"]["desc"] == "send email to 'a@b.c'"
    for e in entries:
        assert e["token"] and isinstance(e["age_s"], int)
    # Oldest first, same order the banner renders.
    assert entries[0]["tool"] == "run_shell_command"
    print("ok list_entries_full_text")


def test_confirm_executes_exactly_once(server):
    marker = _park_shell(server.tmp)
    (entry,) = _pending(server.port)
    status, body = _req(server.port, "POST", "/api/approvals/resolve",
                        _auth(), {"token": entry["token"],
                                  "action": "approve"})
    assert status == 200, (status, body)
    j = json.loads(body)
    assert j["ok"] is True, j
    assert j["pending"] == []
    # The stashed shell call really ran — once.
    assert marker.read_text().strip().count("pulled-") == 1
    assert _pending(server.port) == []
    # The token is single-use: a second Confirm cannot re-execute.
    status, body = _req(server.port, "POST", "/api/approvals/resolve",
                        _auth(), {"token": entry["token"],
                                  "action": "approve"})
    j = json.loads(body)
    assert j["ok"] is False and "unknown or expired" in j["result"], j
    assert marker.read_text().strip().count("pulled-") == 1
    print("ok confirm_executes_exactly_once")


def test_cancel_denies_and_clears(server):
    marker = _park_shell(server.tmp)
    (entry,) = _pending(server.port)
    status, body = _req(server.port, "POST", "/api/approvals/resolve",
                        _auth(), {"token": entry["token"],
                                  "action": "deny"})
    assert status == 200, (status, body)
    j = json.loads(body)
    assert j["ok"] is True, j
    assert "Denied and dropped" in j["result"], j
    assert "not executed" in j["result"], j
    assert not marker.exists()  # the call never ran
    assert _pending(server.port) == []
    print("ok cancel_denies_and_clears")


def test_unauthenticated_rejected(server):
    approval.request_approval("send_email", {"to": "a@b.c"},
                              desc="send email to 'a@b.c'")
    (entry,) = approval.pending_entries()
    # No credentials and wrong credentials are both refused, for
    # the read and for the action.
    status, _b = _req(server.port, "GET", "/api/approvals")
    assert status == 401, status
    status, _b = _req(server.port, "GET", "/api/approvals",
                      {"X-Bridge-Token": "nope"})
    assert status == 401, status
    status, _b = _req(server.port, "POST", "/api/approvals/resolve",
                      None, {"token": entry["token"],
                             "action": "approve"})
    assert status == 401, status
    # Nothing was resolved behind the refusal.
    assert len(approval.pending_entries()) == 1
    # The session-cookie path the page actually uses also works.
    status, _b = _req(server.port, "GET", "/api/approvals",
                      {"Cookie": f"aria_bridge_token={_TOKEN}"})
    assert status == 200, status
    print("ok unauthenticated_rejected")


def test_resolve_validation(server):
    status, _b = _req(server.port, "POST", "/api/approvals/resolve",
                      _auth(), {})
    assert status == 400, status
    status, _b = _req(server.port, "POST", "/api/approvals/resolve",
                      _auth(), {"token": "abc", "action": "maybe"})
    assert status == 400, status
    status, body = _req(server.port, "POST", "/api/approvals/resolve",
                        _auth(), {"token": "abc", "action": "approve"})
    j = json.loads(body)
    assert status == 200 and j["ok"] is False, (status, j)
    assert "unknown or expired" in j["result"]
    print("ok resolve_validation")


def test_bridge_never_logs_the_token(server):
    marker = _park_shell(server.tmp)
    (entry,) = _pending(server.port)
    status, _b = _req(server.port, "POST", "/api/approvals/resolve",
                      _auth(), {"token": entry["token"],
                                "action": "approve"})
    assert status == 200
    assert marker.exists()
    # The bridge writes no per-request logs, and nothing the new
    # endpoints caused it to log carries the approval token.
    for line in server.bridge_logs:
        assert entry["token"] not in line, line
    print("ok bridge_never_logs_the_token")
