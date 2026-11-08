"""Pass 29 #52: Gmail and calendar setup must not claim persistence
after the save fails.

The gap: tool_gmail_setup called bare save_keys() and then
unconditionally returned "Gmail saved for ...", and
tool_calendar_setup did the same with "Calendar connected...".
save_keys() returns False on broken JSON or any write exception
(aria/config.py), which both handlers ignored — so a failed save
still claimed persistence, and the credentials vanished on
restart.

The fix: both handlers persist through _persist_key_updates,
which returns True only when save_keys() succeeds AND a fresh
read of aria_keys.json shows the new values. On failure the
in-memory entries roll back to their previous values (the
ui_panels precedent), nothing about the values is logged, and
the reply says NOT stored — session-only from environment
variables when env supplies the credential (key_get checks env
first), previously-stored when an older value remains, and not
set up when nothing remains. The success messages are unchanged.
"""
import json
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_pass29_setup_")
os.environ["ARIA_DATA_ROOT"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules.setdefault("aria", _pkg)
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules.setdefault("aria.tools", _tools_pkg)

import importlib

builtins = importlib.import_module("aria.tools.builtins")
config = importlib.import_module("aria.config")

GMAIL_USER = "a@example.com"
GMAIL_PW = "app-password-123"
CAL_URL = "https://calendar.google.com/calendar/ical/secret/basic.ics"


def _clear_env(monkeypatch):
    for var in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "ICAL_URL"):
        monkeypatch.delenv(var, raising=False)


def _seed_keys(monkeypatch, values):
    """Snapshot _KEYS, replace it with `values`, restore after."""
    saved = dict(builtins._KEYS)
    builtins._KEYS.clear()
    builtins._KEYS.update(values)
    monkeypatch.setattr(builtins, "_KEYS", builtins._KEYS)
    return saved


def _restore_keys(saved):
    builtins._KEYS.clear()
    builtins._KEYS.update(saved)


# ── Gmail ──────────────────────────────────────────────────────────

def test_gmail_save_success_message_unchanged(monkeypatch):
    _clear_env(monkeypatch)
    saved = _seed_keys(monkeypatch, {})
    try:
        monkeypatch.setattr(builtins, "save_keys", lambda: True)
        monkeypatch.setattr(
            builtins, "_keys_file_values",
            lambda keys: {"GMAIL_USER": GMAIL_USER,
                          "GMAIL_APP_PASSWORD": GMAIL_PW})
        out = builtins.tool_gmail_setup(GMAIL_USER, GMAIL_PW)
        assert out == (
            f"Gmail saved for {GMAIL_USER}. You can now send email "
            "with send_email and read it with read_email.")
        assert builtins._KEYS["GMAIL_USER"] == GMAIL_USER
        assert builtins._KEYS["GMAIL_APP_PASSWORD"] == GMAIL_PW
    finally:
        _restore_keys(saved)


def test_gmail_save_failure_never_claims_saved_and_rolls_back(
        monkeypatch):
    _clear_env(monkeypatch)
    saved = _seed_keys(monkeypatch, {"GMAIL_USER": "old@example.com",
                                     "GMAIL_APP_PASSWORD": "old-pw"})
    try:
        monkeypatch.setattr(builtins, "save_keys", lambda: False)
        out = builtins.tool_gmail_setup(GMAIL_USER, GMAIL_PW)
        assert "Gmail saved for" not in out
        assert "NOT stored" in out
        assert "previously stored credentials remain" in out
        # Rollback: the new values never landed in memory, the old
        # ones are intact — and no credential value is in the reply.
        assert builtins._KEYS["GMAIL_USER"] == "old@example.com"
        assert builtins._KEYS["GMAIL_APP_PASSWORD"] == "old-pw"
        assert GMAIL_PW not in out
    finally:
        _restore_keys(saved)


def test_gmail_save_failure_with_nothing_stored(monkeypatch):
    _clear_env(monkeypatch)
    saved = _seed_keys(monkeypatch, {})
    try:
        monkeypatch.setattr(builtins, "save_keys", lambda: False)
        out = builtins.tool_gmail_setup(GMAIL_USER, GMAIL_PW)
        assert "Gmail saved for" not in out
        assert "NOT stored" in out
        assert "not set up" in out
        assert "GMAIL_USER" not in builtins._KEYS
    finally:
        _restore_keys(saved)


def test_gmail_save_failure_with_env_is_session_only(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("GMAIL_USER", "env@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "env-pw")
    saved = _seed_keys(monkeypatch, {})
    try:
        monkeypatch.setattr(builtins, "save_keys", lambda: False)
        out = builtins.tool_gmail_setup(GMAIL_USER, GMAIL_PW)
        assert "Gmail saved for" not in out
        assert "NOT stored" in out
        assert "session only" in out
        assert "environment variables" in out
        # Rolled back: the new values are not in memory; the env
        # values are what _gmail_creds() will still read.
        assert "GMAIL_USER" not in builtins._KEYS
    finally:
        _restore_keys(saved)


def test_gmail_save_success_but_verify_mismatch_is_failure(monkeypatch):
    _clear_env(monkeypatch)
    saved = _seed_keys(monkeypatch, {})
    try:
        monkeypatch.setattr(builtins, "save_keys", lambda: True)
        # save_keys() said True, but the file does not hold the
        # new values — the durable check must catch that.
        monkeypatch.setattr(
            builtins, "_keys_file_values",
            lambda keys: {"GMAIL_USER": "someone-else@example.com",
                          "GMAIL_APP_PASSWORD": "other"})
        out = builtins.tool_gmail_setup(GMAIL_USER, GMAIL_PW)
        assert "Gmail saved for" not in out
        assert "NOT stored" in out
        assert "GMAIL_USER" not in builtins._KEYS
    finally:
        _restore_keys(saved)


def test_gmail_real_save_roundtrip(monkeypatch, tmp_path):
    # No mocks: the real save_keys() + the real file re-read.
    _clear_env(monkeypatch)
    saved = _seed_keys(monkeypatch, {})
    try:
        out = builtins.tool_gmail_setup(GMAIL_USER, GMAIL_PW)
        assert out.startswith(f"Gmail saved for {GMAIL_USER}.")
        with open(config.KEYS_FILE, encoding="utf-8") as f:
            stored = json.load(f)
        assert stored["GMAIL_USER"] == GMAIL_USER
        assert stored["GMAIL_APP_PASSWORD"] == GMAIL_PW
    finally:
        _restore_keys(saved)


# ── Calendar ───────────────────────────────────────────────────────

def test_calendar_save_success_message_unchanged(monkeypatch):
    _clear_env(monkeypatch)
    saved = _seed_keys(monkeypatch, {})
    try:
        monkeypatch.setattr(builtins, "save_keys", lambda: True)
        monkeypatch.setattr(
            builtins, "_keys_file_values",
            lambda keys: {"ICAL_URL": CAL_URL})
        out = builtins.tool_calendar_setup(CAL_URL)
        assert out == ("Calendar connected. I'll read your live "
                       "schedule from now on - check it anytime "
                       "with check_calendar.")
        assert builtins._KEYS["ICAL_URL"] == CAL_URL
    finally:
        _restore_keys(saved)


def test_calendar_save_failure_never_claims_connected(monkeypatch):
    _clear_env(monkeypatch)
    saved = _seed_keys(monkeypatch, {})
    try:
        monkeypatch.setattr(builtins, "save_keys", lambda: False)
        out = builtins.tool_calendar_setup(CAL_URL)
        assert "Calendar connected" not in out
        assert "NOT stored" in out
        assert "no calendar is connected" in out
        assert "ICAL_URL" not in builtins._KEYS
    finally:
        _restore_keys(saved)


def test_calendar_save_failure_with_env_is_session_only(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ICAL_URL", CAL_URL)
    saved = _seed_keys(monkeypatch, {})
    try:
        monkeypatch.setattr(builtins, "save_keys", lambda: False)
        out = builtins.tool_calendar_setup(CAL_URL)
        assert "Calendar connected" not in out
        assert "NOT stored" in out
        assert "session only" in out
        assert "ICAL_URL" not in builtins._KEYS
    finally:
        _restore_keys(saved)


def test_calendar_save_failure_keeps_previous_url(monkeypatch):
    _clear_env(monkeypatch)
    old = "https://calendar.google.com/calendar/ical/old/basic.ics"
    saved = _seed_keys(monkeypatch, {"ICAL_URL": old})
    try:
        monkeypatch.setattr(builtins, "save_keys", lambda: False)
        out = builtins.tool_calendar_setup(CAL_URL)
        assert "Calendar connected" not in out
        assert "previously stored URL remains" in out
        assert builtins._KEYS["ICAL_URL"] == old
    finally:
        _restore_keys(saved)


def test_calendar_real_save_roundtrip(monkeypatch):
    _clear_env(monkeypatch)
    saved = _seed_keys(monkeypatch, {})
    try:
        out = builtins.tool_calendar_setup(CAL_URL)
        assert out.startswith("Calendar connected.")
        with open(config.KEYS_FILE, encoding="utf-8") as f:
            assert json.load(f)["ICAL_URL"] == CAL_URL
    finally:
        _restore_keys(saved)
