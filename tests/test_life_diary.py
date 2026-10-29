"""Life diary reading + journal mediation (Alek, 2026-10-29):
the greeting clause is built from real diary entries only,
deduped and capped; the supervisor answers journal requests
through the main-side handler and never leaves the child stuck.
"""
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import life_diary as ld
from aria import life_process as lp
from aria.life_supervisor import LifeSupervisor

TZ = timezone(timedelta(hours=-4))
T0 = datetime(2026, 10, 29, 9, 0, tzinfo=TZ)


def _entry(minutes, kind="activity", **kw):
    e = {"ts": (T0 + timedelta(minutes=minutes)).isoformat(),
         "kind": kind}
    e.update(kw)
    return e


def test_clause_empty_when_nothing_done():
    assert ld.summarize_activities([]) == ""
    assert ld.summarize_activities(
        [_entry(1, kind="day", date="2026-10-29")]) == ""
    assert ld.summarize_activities(
        [_entry(1, kind="journal", text="dear diary")]) == ""


def test_clause_one_two_three_and_cap():
    one = [_entry(1, did="napped")]
    assert ld.summarize_activities(one) == \
        "While you were out, I napped."
    two = one + [_entry(2, did="read")]
    assert ld.summarize_activities(two) == \
        "While you were out, I napped and read."
    many = two + [_entry(3, did="watered the plant"),
                  _entry(4, did="tinkered"),
                  _entry(5, did="paced")]
    clause = ld.summarize_activities(many)
    assert clause == ("While you were out, I napped, read, "
                      "and watered the plant.")
    assert "tinkered" not in clause           # capped at three


def test_clause_dedupes():
    entries = [_entry(1, did="read"), _entry(50, did="read"),
               _entry(90, did="napped")]
    assert ld.summarize_activities(entries) == \
        "While you were out, I read and napped."


def test_read_entries_window_and_fail_soft(tmp_path):
    p = lp.paths(str(tmp_path))
    lines = [_entry(0, did="slept"), _entry(30, did="read"),
             _entry(90, did="tinkered")]
    Path(p["diary"]).write_text(
        "\n".join(json.dumps(e) for e in lines) + "\n{broken\n",
        encoding="utf-8")
    all_entries = ld.read_entries(str(tmp_path))
    assert [e["did"] for e in all_entries] == \
        ["slept", "read", "tinkered"]          # broken line skipped
    since = (T0 + timedelta(minutes=45)).timestamp()
    windowed = ld.read_entries(str(tmp_path), since_ts=since)
    assert [e["did"] for e in windowed] == ["tinkered"]
    clause = ld.summary_for_window(str(tmp_path), since)
    assert clause == "While you were out, I tinkered."
    assert ld.read_entries(str(tmp_path / "missing")) == []


class _FakeProc:
    def poll(self):
        return None

    def terminate(self):
        pass


def test_supervisor_mediates_journal(tmp_path):
    p = lp.paths(str(tmp_path))
    req = {"id": "42", "ts": 1.0,
           "context": {"did": ["read"], "thought": "tides."}}
    Path(p["journal_req"]).write_text(json.dumps(req),
                                       encoding="utf-8")
    seen = []

    def handler(context):
        seen.append(context)
        return "Dear diary: I read about tides."

    sup = LifeSupervisor(lambda: {}, base_dir=str(tmp_path),
                         popen=lambda args, **kw: _FakeProc(),
                         journal_handler=handler)
    assert sup._mediate_journal_once() is True
    resp = json.loads(Path(p["journal_resp"]).read_text(
        encoding="utf-8"))
    assert resp == {"id": "42",
                    "text": "Dear diary: I read about tides."}
    assert seen == [{"did": ["read"], "thought": "tides."}]
    # an in-flight answer is not overwritten
    assert sup._mediate_journal_once() is False


def test_supervisor_journal_handler_failure_answers_empty(tmp_path):
    p = lp.paths(str(tmp_path))
    Path(p["journal_req"]).write_text(
        json.dumps({"id": "7", "context": {}}), encoding="utf-8")

    def boom(context):
        raise RuntimeError("provider down")

    sup = LifeSupervisor(lambda: {}, base_dir=str(tmp_path),
                         popen=lambda args, **kw: _FakeProc(),
                         journal_handler=boom)
    assert sup._mediate_journal_once() is True
    resp = json.loads(Path(p["journal_resp"]).read_text(
        encoding="utf-8"))
    assert resp == {"id": "7", "text": ""}     # child never stuck


def test_no_handler_no_mediation(tmp_path):
    p = lp.paths(str(tmp_path))
    Path(p["journal_req"]).write_text(
        json.dumps({"id": "9", "context": {}}), encoding="utf-8")
    sup = LifeSupervisor(lambda: {}, base_dir=str(tmp_path),
                         popen=lambda args, **kw: _FakeProc())
    assert sup._mediate_journal_once() is False
    assert not Path(p["journal_resp"]).exists()
