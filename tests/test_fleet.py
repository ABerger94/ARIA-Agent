"""Fleet registry + rendering (the Mark-LVI port, 2026-10-27).

The view's rule: rows come from runtime state, never from the
model's claims — and the empty state is the feature. These pin
the registry semantics and the rendered text; snapshot sources
are stubbed at the module seam fleet imports lazily."""
import sys
import types
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import fleet


def setup_function():
    fleet.reset_for_tests()


def test_begin_finish_moves_a_call_from_inflight_to_recent():
    tok = fleet.begin('read_file', '/tmp/x.py')
    cur = fleet.current()
    assert len(cur) == 1 and cur[0]['tool'] == 'read_file'
    assert cur[0]['detail'] == '/tmp/x.py' and cur[0]['elapsed'] >= 0
    fleet.finish(tok, ok=True)
    assert fleet.current() == []
    rec = fleet.recent()
    assert len(rec) == 1 and rec[0]['ok'] is True
    assert rec[0]['duration'] >= 0


def test_finish_unknown_token_is_a_noop_and_failures_flag():
    fleet.finish(None)
    fleet.finish(9999)
    tok = fleet.begin('spotify')
    fleet.finish(tok, ok=False)
    assert fleet.recent()[0]['ok'] is False


def test_detail_for_records_evidence_not_summaries():
    assert fleet.detail_for(
        'run_python_code', {'code': 'import os\nprint(1)'}) == 'import os'
    assert fleet.detail_for('read_file', {'path': '/a/b.py'}) == '/a/b.py'
    assert fleet.detail_for('spotify', {'action': 'play'}) == ''
    assert len(fleet.detail_for('read_file', {'path': 'x' * 300})) == 120


def test_result_ok_reads_the_funnels_vocabulary():
    assert fleet.result_ok('done') is True
    assert fleet.result_ok('Unknown tool: wat') is False
    assert fleet.result_ok('[Tool Error: boom]') is False
    assert fleet.result_ok('[Argument error: missing]') is False
    assert fleet.result_ok('fine [Self-Heal: noted]') is True


def test_empty_state_is_the_feature():
    text = fleet.render_text({'inflight': [], 'recent': [],
                              'delegated': [], 'scheduled': 0,
                              'routines': 0, 'mcp': [], 'running': 0})
    assert 'Nothing running' in text
    assert 'it will appear here' in text


def test_render_shows_inflight_recent_and_counts(monkeypatch):
    queue = types.ModuleType('aria.agent.taskqueue')
    queue.get_queue = lambda: types.SimpleNamespace(
        get_all_statuses=lambda: [
            {'goal': 'research X', 'status': 'running'},
            {'goal': 'old job', 'status': 'done', 'result': 'ok'}])
    sched = types.ModuleType('aria.scheduler')
    sched.sched_list = lambda: [(1,), (2,)]
    monkeypatch.setitem(sys.modules, 'aria.agent.taskqueue', queue)
    monkeypatch.setitem(sys.modules, 'aria.scheduler', sched)
    tok = fleet.begin('web_search', 'doja cat tickets')
    snap = fleet.snapshot()
    assert snap['running'] == 2  # 1 in flight + 1 active delegated
    assert snap['scheduled'] == 2
    text = fleet.render_text(snap)
    assert 'IN FLIGHT (1)' in text and 'web_search' in text
    assert 'DELEGATED (1 active / 2 total)' in text
    assert 'SCHEDULED: 2' in text
    fleet.finish(tok, ok=True)
    text2 = fleet.render_text(fleet.snapshot())
    assert 'RECENT CALLS' in text2 and '[ok,' in text2
