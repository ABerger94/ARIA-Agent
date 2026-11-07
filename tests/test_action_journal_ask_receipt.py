"""No-effect ASK receipts must not block a resumed task (pass 28 #30).

A tool returning ASK asked for input and did nothing — like the
[AWAITING_APPROVAL carve-out in finish(), it owns no receipt.
The old code stored state='ask' in action_receipts, and begin()
returned the cached ASK for the same operation id forever: the
resumed task re-asked the same question on every attempt while
the tool never re-ran to consume the answer. The fix deletes
the row on ASK (mirroring approval) and makes begin() treat a
stored 'ask' row as non-blocking, which also heals rows written
before the fix. DONE / UNVERIFIED receipts keep their dedup
behavior: a success is still cached, and an uncertain outcome
still blocks replay (it may have taken effect).

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import tempfile
import time
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

_cfg = importlib.import_module("aria.config")
_memory = importlib.import_module("aria.memory")
_memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_ask_receipt_"), "test.db")
_memory.init_databases()
_memory._embed = lambda text: None

for _name in ("aria.scheduler", "aria.vision", "aria.hardware",
              "aria.spotify", "aria.hud"):
    sys.modules[_name] = types.ModuleType(_name)

action_journal = importlib.import_module("aria.action_journal")
from aria import outcome  # noqa: E402

_ARGS = {"query": "which file?"}


def test_ask_result_is_not_persisted_as_blocking_receipt():
    op = "op-ask-1"
    assert action_journal.begin(op, "web_search", _ARGS) is None
    action_journal.finish(op, outcome.ask("web_search", "which one?"))
    assert action_journal.get(op) is None, "ASK must not leave a receipt"
    assert action_journal.begin(op, "web_search", _ARGS) is None, \
        "the resumed attempt must execute fresh, not get the stale ASK"


def test_stored_ask_row_from_before_fix_does_not_block():
    # A row written by the old code (state='ask') must not block
    # the resumed task either — begin() treats it as no-effect.
    op = "op-ask-2"
    with action_journal._connect() as db:
        now = time.time()
        db.execute(
            "INSERT INTO action_receipts"
            "(id,tool,fingerprint,state,result,receipt,artifacts,"
            "created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
            (op, "send_email", action_journal.signature("send_email", _ARGS),
             "ask", "status=ask action=send_email question=\"which one?\"",
             "{}", "[]", now, now))
    assert action_journal.begin(op, "send_email", _ARGS) is None, \
        "a stored ASK must grant execution even for an effect tool"


def test_done_receipt_still_returned_cached():
    op = "op-done-1"
    assert action_journal.begin(op, "web_search", _ARGS) is None
    action_journal.finish(op, outcome.done("web_search", result="A-OUT"))
    cached = action_journal.begin(op, "web_search", _ARGS)
    assert cached is not None and cached.status == "done", cached


def test_unverified_receipt_still_blocks_replay():
    op = "op-unv-1"
    assert action_journal.begin(op, "web_search", _ARGS) is None
    action_journal.finish(
        op, outcome.unverified("web_search", "may have happened"))
    cached = action_journal.begin(op, "web_search", _ARGS)
    assert cached is not None and cached.status == "unverified", cached


def test_awaiting_approval_still_deletes_receipt():
    op = "op-appr-1"
    assert action_journal.begin(op, "web_search", _ARGS) is None
    action_journal.finish(op, "[AWAITING_APPROVAL token=<redacted>]")
    assert action_journal.get(op) is None
    assert action_journal.begin(op, "web_search", _ARGS) is None
