"""Pass 17 (ChatGPT x ARIA loop, 2026-11-03): close the approval
loop. ChatGPT's audit of the confirm/deny system found the
central defect behind Alek's 2026-11-03 incidents: the system
parks correctly, then drops the answer.

Verified against the code before fixing:
- approve_token() wrote the executed result into
  origin.calls — a dict with NO reader anywhere in the codebase
  (the duplicate guard uses dispatch's own per-turn _TURN_CALLS,
  which resets every turn, so not even re-asking recovered it).
  The dashboard click path discards approve_token's return; the
  OPS log's 200-char line was the only trace. 05:14: a confirmed
  `git pull` executed (exit 0) and was never reported.
- deny_token()'s return is discarded the same way; expiry
  (purge_expired) dropped entries with no record at all. Denied,
  expired, and executed were indistinguishable to the model.
- No turn ever received authoritative approval state, so at
  05:27 the model narrated a "Pending approval" table for a
  token that never existed, straight from stale history.

Fix contract pinned here:
- approval records EVERY resolution — executed / did NOT
  execute / errored / denied / expired — in a journal.
- approval_state_note() renders the live pending snapshot +
  recent journal with NO tokens, and brain's system instruction
  carries it (via brain._approval_state_block) as authoritative
  state that overrides conversation history.
- The AWAITING_APPROVAL string keeps its machine contract
  (prefix + token, parsed by aria.missions) and now also tells
  the model the expiry and to verify pending state through
  list_pending_approvals, never from memory.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import time
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pkg = types.ModuleType("aria")
pkg.__path__ = [str(Path(__file__).parents[1] / "aria")]
sys.modules.setdefault("aria", pkg)

for _name in ("aria.speech", "aria.hud",
              "aria.frame", "aria.vision", "aria.hardware",
              "aria.agent.taskqueue"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
# NOTE: aria.scheduler is deliberately NOT stubbed — brain's
# import chain (aria.agent.proactive) reads real names from it
# (BREAK_REMINDERS), and the real module imports cleanly offline.

# aria.agent.shortcuts (pulled in by brain) imports these two
# names from aria.spotify; a bare stub module breaks that import.
_spotify_stub = types.ModuleType("aria.spotify")
_spotify_stub.tool_media_key = lambda args=None: ""
_spotify_stub.tool_spotify = lambda args=None: ""
sys.modules.setdefault("aria.spotify", _spotify_stub)

import pytest

approval = importlib.import_module("aria.approval")
config = importlib.import_module("aria.config")
dispatch = importlib.import_module("aria.tools.dispatch")

ARGS = {"to": "a@example.com", "subject": "hi", "body": "hello"}


@pytest.fixture()
def state(tmp_path, monkeypatch):
    """Temp data root, clean approval store + journal, stubbed
    send_email handler, captured OPS log."""
    h = tmp_path / "home"
    (h / "ARIA").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    _data_dir = importlib.import_module("aria.data_dir")
    monkeypatch.setattr(_data_dir, "_ROOT", str(h / "ARIA"))
    logs = []
    monkeypatch.setattr(
        config, "add_log",
        lambda msg, *a, **k: logs.append(str(msg)))
    approval._PENDING.clear()
    approval._RESOLUTIONS.clear()
    approval._pending_loaded = False
    dispatch.reset_turn_state()
    approval.set_mode("confirm-risky")
    yield types.SimpleNamespace(dir=h, logs=logs)
    approval._PENDING.clear()
    approval._RESOLUTIONS.clear()
    approval._pending_loaded = False
    dispatch.reset_turn_state()


def test_note_is_scoped_to_current_project(state):
    # Pass 25: a turn bound to one project used to see every
    # project's pending approvals and resolutions.
    from aria import workspaces
    proj_b = workspaces.create("Project B")
    with workspaces.use(proj_b):
        token_b = _park(desc="send email to B project")
    token_a = _park(desc="send email to A project")
    # Pending entries carry their origin project.
    by_token = {e["token"]: e for e in approval.pending_entries()}
    assert by_token[token_b]["project"] == proj_b
    assert by_token[token_a]["project"] == "default"
    # The default-project turn sees only its own pending item.
    note = approval.approval_state_note()
    assert "A project" in note and "B project" not in note, note
    # The B turn sees only B's.
    with workspaces.use(proj_b):
        note_b = approval.approval_state_note()
    assert "B project" in note_b and "A project" not in note_b, note_b
    # Resolutions are scoped the same way: denying B's request
    # must not surface in the default project's note.
    approval.deny_token(token_b)
    note = approval.approval_state_note()
    assert "denied" not in note, note
    with workspaces.use(proj_b):
        note_b = approval.approval_state_note()
    assert "denied" in note_b, note_b


def _park(desc="send email to 'a@example.com'"):
    approval.request_approval("send_email", dict(ARGS), desc=desc)
    return list(approval._PENDING)[-1]


def test_executed_resolution_is_journaled_and_noted(state, monkeypatch):
    monkeypatch.setitem(dispatch._REGISTRY, "send_email",
                        lambda args: "stub: email sent ok")
    token = _park()
    out = approval.approve_token(token)
    assert "stub: email sent ok" in out
    (rec,) = approval.recent_resolutions()
    assert rec["tool"] == "send_email"
    assert rec["verdict"] == "executed"
    assert "stub: email sent ok" in rec["detail"]
    note = approval.approval_state_note()
    assert "Pending approvals right now: none." in note
    assert "executed" in note and "send_email" in note
    assert token not in note  # tokens never reach the model


def test_denied_resolution_is_journaled(state):
    token = _park()
    approval.deny_token(token)
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "denied"
    note = approval.approval_state_note()
    assert "denied" in note
    assert token not in note


def test_expired_resolution_is_journaled(state):
    token = _park()
    approval._PENDING[token]["created_ts"] = time.time() - 601
    assert approval.purge_expired() == 1
    assert approval.pending_entries() == []
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "expired"
    assert "not executed" in rec["detail"]


def test_blocked_replay_journals_did_not_execute(state, monkeypatch):
    monkeypatch.setitem(dispatch._REGISTRY, "send_email",
                        lambda args: "[Safe mode: send_email is blocked]")
    token = _park()
    approval.approve_token(token)
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "did NOT execute"
    assert "Safe mode" in rec["detail"]


def test_errored_replay_is_journaled(state, monkeypatch):
    def _boom(args):
        raise RuntimeError("smtp exploded")
    monkeypatch.setitem(dispatch._REGISTRY, "send_email", _boom)
    token = _park()
    approval.approve_token(token)
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "errored"


def test_note_is_empty_with_no_state(state):
    assert approval.approval_state_note() == ""
    assert approval.recent_resolutions() == []


def test_pending_snapshot_lists_without_token(state):
    token = _park()
    note = approval.approval_state_note()
    assert "Pending approvals right now" in note
    assert "send_email" in note
    assert token not in note


def test_awaiting_string_keeps_machine_contract_and_adds_verify_rule(state):
    text = approval.request_approval("send_email", dict(ARGS))
    assert text.startswith("[AWAITING_APPROVAL token=")
    assert "list_pending_approvals" in text
    assert str(approval.APPROVAL_EXPIRY_S) in text


def _simulate_restart():
    """Drop the in-memory store the way a process restart does;
    the persisted file (under the fixture's data root) survives."""
    approval._PENDING.clear()
    approval._RESOLUTIONS.clear()
    approval._pending_loaded = False


def test_pending_survives_restart_and_still_resolves(state, monkeypatch):
    monkeypatch.setitem(dispatch._REGISTRY, "send_email",
                        lambda args: "stub: email sent ok")
    token = _park()
    _simulate_restart()
    entries = approval.pending_entries()
    assert [e["token"] for e in entries] == [token]
    assert entries[0]["tool"] == "send_email"
    out = approval.approve_token(token)
    assert "stub: email sent ok" in out
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "executed"


def test_expired_while_down_is_tombstoned_not_restored(state):
    token = _park()
    # Age the entry past expiry and persist that aged state,
    # as if ARIA had been off while the clock ran out.
    approval._PENDING[token]["created_ts"] = time.time() - 601
    approval._save_pending()
    _simulate_restart()
    assert approval.pending_entries() == []
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "expired"
    assert "not running" in rec["detail"]
    assert "none" in approval.approval_state_note()


def test_denied_pending_does_not_come_back_after_restart(state):
    token = _park()
    approval.deny_token(token)
    _simulate_restart()
    assert approval.pending_entries() == []


def test_brain_block_renders_authoritative_state(state):
    brain = importlib.import_module("aria.agent.brain")
    assert brain._approval_state_block() == ""
    token = _park()
    approval.deny_token(token)
    block = brain._approval_state_block()
    assert "Approval state (system-generated" in block
    assert "denied" in block
    assert token not in block
