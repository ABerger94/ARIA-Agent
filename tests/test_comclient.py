"""COM host client tests (crash round 5: process isolation).

aria/comclient.py owns the helper subprocess. These tests spawn a
REAL fake-helper subprocess (a tiny script speaking the JSON-lines
protocol) and pin:

  * request/response round-trips, including an op failure surfacing
    as ComHostError (not a hang, not a crash);
  * send() + the async sapi.finished event arriving through the
    event listeners with the request's id;
  * a helper that dies mid-flight: pending requests fail with
    ComHostDown, down listeners fire, the supervisor respawns with
    backoff and requests work again;
  * an always-dying helper: the backoff ladder exhausts and
    requests raise ComHostDown immediately instead of spawning in
    a hot loop.

The double is intentionally a separate OS process: the protocol is
the contract, and it must hold across a real pipe.
"""
import os
import sys
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))


_pkg = types.ModuleType("aria")
_pkg.__path__ = [os.path.join(HERE, "..", "aria")]
sys.modules["aria"] = _pkg
_agent = types.ModuleType("aria.agent")
_agent.__path__ = [os.path.join(HERE, "..", "aria", "agent")]
sys.modules["aria.agent"] = _agent
# failures module is imported lazily by the client on helper death;
# stub it so the suite never touches the real memory stores.
_surf = types.ModuleType("aria.agent.failures")
_surf.surface_failure = lambda *a, **k: None
sys.modules["aria.agent.failures"] = _surf

import importlib
comclient = importlib.import_module("aria.comclient")
ComHostClient = comclient.ComHostClient
ComHostDown = comclient.ComHostDown
ComHostError = comclient.ComHostError


# ------------------------------------------------------------- the double
_DOUBLE = r'''
import json, sys

# Mode and count path arrive via argv (NOT env): respawns must see
# the same values as the first incarnation, and env would have to
# survive in the parent for the client's whole lifetime.
mode = sys.argv[1] if len(sys.argv) > 1 else "good"
count_path = sys.argv[2] if len(sys.argv) > 2 else ""
if count_path:
    try:
        with open(count_path) as fh:
            n = int(fh.read().strip() or "0")
    except Exception:
        n = 0
    n += 1
    with open(count_path, "w") as fh:
        fh.write(str(n))
    if mode == "die_once" and n >= 2:
        mode = "good"
if mode in ("die_always", "die_once"):
    sys.exit(3)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    op = req.get("op")
    rid = req.get("id")
    if op == "die":
        sys.exit(3)
    if op == "ping":
        resp = {"id": rid, "ok": True, "result": {"pong": True}}
    elif op == "volume.get_state":
        resp = {"id": rid, "ok": True, "result": {
            "device_vol": 0.42, "spotify_vol": 0.9,
            "device_muted": False, "spotify_muted": True}}
    elif op == "badop":
        resp = {"id": rid, "ok": False, "error": "kaboom"}
    elif op == "sapi.say":
        sys.stdout.write(json.dumps(
            {"id": rid, "ok": True, "result": {"queued": True}}) + "\n")
        sys.stdout.flush()
        sys.stdout.write(json.dumps(
            {"event": "sapi.finished", "id": rid, "ok": True}) + "\n")
        sys.stdout.flush()
        continue
    else:
        resp = {"id": rid, "ok": False, "error": "unknown op"}
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()
'''


def _double_cmd():
    fd, path = tempfile.mkstemp(suffix=".py", prefix="fake_comhost_")
    with os.fdopen(fd, "w") as fh:
        fh.write(_DOUBLE)
    return [sys.executable, path], path


def _count_file():
    fd, path = tempfile.mkstemp(prefix="fake_comhost_count_")
    os.close(fd)
    os.remove(path)
    return path


def _wait_for(pred, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _client(mode="good", backoff=(0.1, 0.2, 0.4), count=None):
    cmd, path = _double_cmd()
    cmd = cmd + [mode, count or ""]
    return ComHostClient(command=cmd, backoff=backoff), path


# ------------------------------------------------------------------ tests
def test_a_request_roundtrip():
    client, _path = _client()
    try:
        assert client.is_up()
        assert client.request("ping", timeout=5) == {"pong": True}
        state = client.request("volume.get_state", timeout=5)
        assert state["device_vol"] == 0.42
        assert state["spotify_muted"] is True
    finally:
        client.close()


def test_b_op_failure_raises_comhost_error():
    client, _path = _client()
    try:
        try:
            client.request("badop", timeout=5)
        except ComHostError as e:
            assert "kaboom" in str(e)
        else:
            raise AssertionError("expected ComHostError")
        # The host is still usable afterwards.
        assert client.request("ping", timeout=5) == {"pong": True}
    finally:
        client.close()


def test_c_send_and_finished_event():
    client, _path = _client()
    events = []
    got = threading.Event()

    def _cb(msg):
        events.append(msg)
        got.set()

    client.add_event_listener(_cb)
    try:
        rid = client.send("sapi.say", {"text": "hi", "whisper": False})
        assert isinstance(rid, int)
        assert got.wait(5)
        assert events == [{"event": "sapi.finished", "id": rid,
                           "ok": True}]
    finally:
        client.close()


def test_d_death_fails_pending_and_respawns():
    count = _count_file()
    client, _path = _client(mode="die_once", count=count)
    downs = []
    client.add_down_listener(lambda: downs.append(1))
    try:
        # First incarnation dies at startup (die_once, count 1).
        assert _wait_for(lambda: not client.is_up())
        # The supervisor respawns; the second incarnation behaves.
        assert _wait_for(lambda: client.is_up())
        assert client.request("ping", timeout=5) == {"pong": True}
        # A mid-flight death also surfaces and recovers.
        try:
            client.request("die", timeout=5)
        except ComHostDown:
            pass
        else:
            raise AssertionError("expected ComHostDown on death")
        assert _wait_for(lambda: len(downs) >= 1)
        assert _wait_for(lambda: client.is_up())
        assert client.request("ping", timeout=5) == {"pong": True}
    finally:
        client.close()


def test_e_exhausted_ladder_stays_down():
    client, _path = _client(mode="die_always", backoff=(0.05, 0.1))
    try:
        # Spawn + 2 respawns all die; the ladder exhausts and the
        # client stops spawning instead of hot-looping.
        assert _wait_for(lambda: not client.is_up())
        time.sleep(1.0)  # well past the whole ladder
        assert not client.is_up()
        try:
            client.request("ping", timeout=2)
        except ComHostDown:
            pass
        else:
            raise AssertionError("expected ComHostDown while exhausted")
        assert not client.is_up()
    finally:
        client.close()


def test_f_default_command_is_the_comhost_file():
    cmd = comclient._default_command()
    assert cmd[0] == sys.executable
    assert cmd[1].endswith(os.path.join("aria", "comhost.py"))
    assert os.path.exists(cmd[1])
