"""
Coucou link — mirror ARIA's activity into the Coucou desktop
island (github.com/Louis-CFM/coucou) as a third-party agent pill.

Coucou is a notch/top-of-screen companion that watches coding
agents through Claude-Code-style hook events. Any program can
join in: write one newline-terminated JSON object per event to
the named pipe ``\\\\.\\pipe\\coucou-<user-SID>``, tagged with
``"coucou_agent": "aria"``, and Coucou gives ARIA her own pill —
thinking on UserPromptSubmit, working on PreToolUse, finished on
Stop, error on StopFailure.

Hard rules, mirroring coucou-hook's own contract:

  * Fail-silent. Coucou is an optional display surface, never a
    dependency: a missing pipe, a refused connection, a
    non-Windows host, or any error at all means "no pill today",
    never an exception in ARIA's flow and never a retry storm
    (one open attempt, one 50 ms busy retry, done).
  * No payloads beyond the event shape. Tool names are mirrored;
    tool arguments and results are NOT (Coucou drops them anyway,
    and they can carry secrets).
  * PermissionRequest is deliberately not sent: Coucou does not
    implement approval cards for third-party agents, and ARIA's
    own approval system stays the only approval surface.

Kill switch: set ARIA_COUCOU=0 in the environment to disable.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import subprocess
import sys
import time

AGENT_NAME = "aria"

# One session per ARIA boot: stable across every event she emits,
# distinct across restarts (pid + boot time).
_SESSION_ID = f"aria-{os.getpid()}-{int(time.time())}"

_session_started = False
_session_ended = False


def session_id() -> str:
    return _SESSION_ID


def enabled() -> bool:
    """Kill switch only; platform/pipe problems are handled by
    the send path returning False, so tests and callers can ask
    one question: is the link switched on?"""
    return os.environ.get("ARIA_COUCOU", "").strip() != "0"


def pipe_name(sid: str) -> str:
    """The pipe Coucou's relay listens on for this user. Must
    match coucou-hook's pipe_path() exactly."""
    return rf"\\.\pipe\coucou-{sid}"


def build_payload(event_name: str, **fields) -> dict:
    payload = {
        "hook_event_name": event_name,
        "session_id": _SESSION_ID,
        "coucou_agent": AGENT_NAME,
        "cwd": os.getcwd(),
    }
    payload.update(fields)
    return payload


def emit(event_name: str, **fields) -> bool:
    """Send one hook event to Coucou. Returns True only when the
    line was written to the pipe; every failure is a quiet False."""
    try:
        if not enabled():
            return False
        path = _pipe_path()
        if not path:
            return False
        line = (json.dumps(build_payload(event_name, **fields)) + "\n").encode("utf-8")
        return bool(_write_line(path, line))
    except Exception:
        return False


def session_start() -> bool:
    """Emit SessionStart once per boot and arm the atexit
    SessionEnd, so the pill appears when ARIA comes up and is
    retired on any clean interpreter exit."""
    global _session_started
    if _session_started:
        return False
    _session_started = True
    try:
        atexit.register(session_end)
    except Exception:
        pass
    return emit("SessionStart")


def session_end() -> bool:
    """Emit SessionEnd once. Safe to call from main()'s finally
    as well as from atexit — the second call is a no-op."""
    global _session_ended
    if _session_ended or not _session_started:
        return False
    _session_ended = True
    return emit("SessionEnd")


# ------------------------------------------------------------ pipe plumbing

def _pipe_path():
    if sys.platform != "win32":
        return None
    sid = _current_user_sid()
    if not sid:
        return None
    return pipe_name(sid)


def _current_user_sid():
    """Current user's SID string (S-1-5-21-...), via ctypes
    GetTokenInformation + ConvertSidToStringSidW; falls back to
    parsing `whoami /user`. None on any failure."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi32.ConvertSidToStringSidW.argtypes = [
            wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]

        token = wintypes.HANDLE()
        # TOKEN_QUERY = 0x0008, TokenUser = 1
        if not advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            return None
        try:
            needed = wintypes.DWORD(0)
            advapi32.GetTokenInformation(
                token, 1, None, 0, ctypes.byref(needed))
            if not needed.value:
                return None
            buf = ctypes.create_string_buffer(needed.value)
            if not advapi32.GetTokenInformation(
                    token, 1, buf, needed.value, ctypes.byref(needed)):
                return None
            # TOKEN_USER starts with the SID pointer.
            sid_ptr = ctypes.cast(buf, ctypes.POINTER(wintypes.LPVOID))[0]
            text = wintypes.LPWSTR()
            if not advapi32.ConvertSidToStringSidW(
                    sid_ptr, ctypes.byref(text)):
                return None
            try:
                return text.value
            finally:
                kernel32.LocalFree(text)
        finally:
            kernel32.CloseHandle(token)
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["whoami", "/user"], capture_output=True, text=True,
            timeout=3).stdout
        match = re.search(r"S-1-\d+(?:-\d+)+", out)
        if match:
            return match.group(0)
    except Exception:
        pass
    return None


def _write_line(path: str, line: bytes) -> bool:
    """Open the pipe once (one short retry on ERROR_PIPE_BUSY,
    the one error coucou-hook also retries), write the line,
    close. No loops beyond that: Coucou being busy or absent
    must never stall a turn or a tool call."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]

    generic_write = 0x40000000
    open_existing = 3
    error_pipe_busy = 231
    invalid = wintypes.HANDLE(-1).value

    handle = invalid
    for attempt in range(2):
        handle = kernel32.CreateFileW(
            path, generic_write, 0, None, open_existing, 0, None)
        if handle != invalid:
            break
        if ctypes.get_last_error() != error_pipe_busy or attempt:
            break
        time.sleep(0.05)
    if handle == invalid:
        return False
    try:
        written = wintypes.DWORD(0)
        ok = kernel32.WriteFile(handle, line, len(line),
                                ctypes.byref(written), None)
        return bool(ok) and written.value == len(line)
    finally:
        kernel32.CloseHandle(handle)
