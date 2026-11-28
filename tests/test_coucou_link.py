"""Coucou link (aria/coucou_link.py): ARIA mirrors her session,
turn and tool activity into the Coucou desktop island as a
third-party agent pill ("aria") via newline-terminated JSON hook
events on the named pipe \\\\.\\pipe\\coucou-<user-SID>.

These tests pin the wire contract and the fail-silent rule:
  * payload shape — hook_event_name, the "aria" agent tag, a
    session id that is stable within a boot, cwd, and caller
    fields (tool_name) passed through untouched;
  * pipe-name construction from a SID, matching coucou-hook's
    pipe_path() exactly;
  * emit() writes exactly one JSON line to the right pipe when
    the (mocked) transport works, and returns False — never
    raises — when the pipe is absent, the transport blows up,
    or the ARIA_COUCOU=0 kill switch is set.

All pipe I/O is mocked: the suite passes on Linux CI, where no
Coucou pipe exists. Plain functions only (no pytest fixtures),
per the _run_suite.py convention.
"""
import importlib
import json
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)

coucou = importlib.import_module("aria.coucou_link")

FAKE_SID = "S-1-5-21-1111111111-2222222222-3333333333-1002"


class _patched:
    """Tiny save/restore monkeypatch context (fixture-free)."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.old)
        return False


def _capture_transport(calls):
    def fake_write(path, line):
        calls.append((path, line))
        return True
    return fake_write


# ------------------------------------------------------------- payload

def test_payload_shape():
    payload = coucou.build_payload("PreToolUse", tool_name="read_file")
    assert payload["hook_event_name"] == "PreToolUse"
    assert payload["coucou_agent"] == "aria"
    assert payload["session_id"] == coucou.session_id()
    assert payload["tool_name"] == "read_file"
    assert payload["cwd"] == os.getcwd()


def test_session_id_stable_within_boot():
    first = coucou.build_payload("SessionStart")
    second = coucou.build_payload("Stop")
    assert first["session_id"] == second["session_id"]
    assert first["session_id"].startswith("aria-")


def test_payload_session_id_override():
    # The on-laptop probe emits with an explicit test session id.
    payload = coucou.build_payload("SessionStart", session_id="aria-probe-1")
    assert payload["session_id"] == "aria-probe-1"


# ------------------------------------------------------------- pipe name

def test_pipe_name_from_sid():
    assert coucou.pipe_name(FAKE_SID) == rf"\\.\pipe\coucou-{FAKE_SID}"


def test_island_pipe_name_from_sid():
    assert coucou.island_pipe_name(FAKE_SID) == rf"\\.\pipe\aria-island-{FAKE_SID}"


# ------------------------------------------------------------- emit

def test_emit_writes_one_json_line():
    calls = []
    with _patched(coucou, "_current_user_sid", lambda: FAKE_SID), \
         _patched(coucou, "_write_line", _capture_transport(calls)), \
         _patched(sys, "platform", "win32"):
        assert coucou.emit("PreToolUse", tool_name="web_search") is True
    # Both island surfaces get the same single line: the original
    # Coucou pipe and the ARIA Island fork pipe.
    assert len(calls) == 2
    paths = [path for path, _line in calls]
    assert paths == [coucou.pipe_name(FAKE_SID), coucou.island_pipe_name(FAKE_SID)]
    assert calls[0][1] == calls[1][1]
    line = calls[0][1]
    assert line.endswith(b"\n")
    payload = json.loads(line.decode("utf-8"))
    assert payload["hook_event_name"] == "PreToolUse"
    assert payload["coucou_agent"] == "aria"
    assert payload["tool_name"] == "web_search"


def test_emit_one_pipe_failing_does_not_break_the_other():
    calls = []

    def flaky(path, line):
        calls.append((path, line))
        if "aria-island" in path:
            raise OSError("fork not running")
        return True

    with _patched(coucou, "_current_user_sid", lambda: FAKE_SID), \
         _patched(coucou, "_write_line", flaky), \
         _patched(sys, "platform", "win32"):
        assert coucou.emit("SessionStart") is True
    assert len(calls) == 2

    calls.clear()

    def flaky_reverse(path, line):
        calls.append((path, line))
        if "aria-island" not in path:
            raise OSError("coucou not running")
        return True

    with _patched(coucou, "_current_user_sid", lambda: FAKE_SID), \
         _patched(coucou, "_write_line", flaky_reverse), \
         _patched(sys, "platform", "win32"):
        assert coucou.emit("SessionStart") is True
    assert len(calls) == 2


def test_emit_fail_silent_when_pipe_absent():
    def boom(path, line):
        raise OSError("pipe does not exist")
    with _patched(coucou, "_current_user_sid", lambda: FAKE_SID), \
         _patched(coucou, "_write_line", boom), \
         _patched(sys, "platform", "win32"):
        assert coucou.emit("SessionStart") is False


def test_emit_fail_silent_without_sid_or_platform():
    calls = []
    with _patched(coucou, "_current_user_sid", lambda: None), \
         _patched(coucou, "_write_line", _capture_transport(calls)), \
         _patched(sys, "platform", "win32"):
        assert coucou.emit("SessionStart") is False
    with _patched(coucou, "_current_user_sid", lambda: FAKE_SID), \
         _patched(coucou, "_write_line", _capture_transport(calls)), \
         _patched(sys, "platform", "linux"):
        assert coucou.emit("SessionStart") is False
    assert calls == []


def test_kill_switch_disables_emit():
    calls = []
    old = os.environ.get("ARIA_COUCOU")
    os.environ["ARIA_COUCOU"] = "0"
    try:
        assert coucou.enabled() is False
        with _patched(coucou, "_write_line", _capture_transport(calls)):
            assert coucou.emit("SessionStart") is False
    finally:
        if old is None:
            os.environ.pop("ARIA_COUCOU", None)
        else:
            os.environ["ARIA_COUCOU"] = old
    assert calls == []


# ------------------------------------------------------------- lifecycle

def test_session_lifecycle_fires_once_each():
    events = []
    with _patched(coucou, "_session_started", False), \
         _patched(coucou, "_session_ended", False), \
         _patched(coucou, "emit", lambda event, **f: events.append(event) or True):
        assert coucou.session_end() is False          # end before start: no-op
        assert coucou.session_start() is True
        assert coucou.session_start() is False        # second start: no-op
        assert coucou.session_end() is True
        assert coucou.session_end() is False          # second end: no-op
    assert events == ["SessionStart", "SessionEnd"]
