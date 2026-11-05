"""Gemini Live per-turn execution context (2026-11-05, ChatGPT x ARIA
loop pass 23): Live bypasses brain.run_agent, so reset_turn_state()
never ran on its path — and the dispatcher duplicate-call cache
lives on the execution context, which for Live's run_in_executor
calls was the executor worker's own thread-local context. That
context survived across turns (the worker is reused), so a repeat
of an identical call in a LATER turn — "volume up" twice, a second
clipboard read — was swallowed as a duplicate and answered with
the stale earlier result. Calling reset_turn_state() from the
event-loop thread does not help: it clears a different thread's
context.

Pinned here against the REAL live module and the REAL dispatcher:
run_session binds one fresh execution context per model turn (one
receive() pass) and propagates it into the executor, so an
identical call in a later turn executes again, while a genuine
duplicate inside a single turn is still blocked, and no live-turn
context lingers after the session ends.

The runner records (context id, cache size at entry) for every
dispatch: the cache size proves whether the handler ran (the
dispatcher caches only after a real execution) and whether two
calls shared one context.

Run per-file (own process), like every suite in this repo.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aria import live as live_mod
from aria import runtime as runtime_mod
from aria.tools import dispatch as dispatch_mod


# -- fake session (same receive() contract as tests/test_live.py) --
def _resp(**kw):
    return types.SimpleNamespace(
        server_content=kw.get('sc'), tool_call=kw.get('tc'))


class _FakeSession:
    """Each receive() yields ONE turn, then returns — the SDK's
    multi-turn pattern. After the scripted turns are exhausted the
    next receive() yields nothing (clean end)."""

    def __init__(self, turns):
        self._turns = [list(t) for t in turns]
        self.tool_responses = []

    async def receive(self):
        if self._turns:
            for item in self._turns.pop(0):
                yield item
            return
        return
        yield  # unreachable — marks this an async generator

    async def send_tool_response(self, function_responses):
        self.tool_responses.append(function_responses)


def _fc(name, args, fid='c1'):
    return types.SimpleNamespace(id=fid, name=name, args=args)


def _call_turn(fc):
    return [_resp(tc=types.SimpleNamespace(function_calls=[fc]))]


def _run(turns, runner):
    session = _FakeSession(turns)
    asyncio.run(live_mod.run_session(session, tool_runner=runner))
    return session


class _ObservingRunner:
    """The production wiring (main.py): straight into the real
    dispatcher. Records the execution context each dispatch lands
    in, and how many calls that context had already cached."""

    def __init__(self):
        self.seen = []  # [(context id, kind, cached calls at entry)]

    def __call__(self, name, args):
        ctx = runtime_mod.current()
        self.seen.append((ctx.id, ctx.kind, len(ctx.calls)))
        return dispatch_mod.execute_tool(name, args)


def _results(session):
    out = []
    for batch in session.tool_responses:
        for r in batch:
            out.append(r['response']['result'])
    return out


def _is_dup_blocked(text):
    return text.startswith('[Duplicate call blocked')


def test_same_call_in_two_turns_executes_twice():
    runner = _ObservingRunner()
    session = _run([
        _call_turn(_fc('list_workspace', {}, 'c1')),
        _call_turn(_fc('list_workspace', {}, 'c2')),
    ], runner)
    results = _results(session)
    assert len(results) == 2
    assert not _is_dup_blocked(results[0])
    assert not _is_dup_blocked(results[1])
    # The second turn re-ran the tool: its result is a fresh
    # listing, not the first turn's cached text.
    assert results[1].startswith('Workspace files:')
    # Each turn got its own live-turn context with an empty cache
    # at entry — the handler ran in both.
    assert len(runner.seen) == 2
    assert runner.seen[0][1] == 'live-turn'
    assert runner.seen[1][1] == 'live-turn'
    assert runner.seen[0][0] != runner.seen[1][0]
    assert [s[2] for s in runner.seen] == [0, 0]


def test_same_call_twice_in_one_turn_still_blocked():
    runner = _ObservingRunner()
    session = _run([
        [_resp(tc=types.SimpleNamespace(function_calls=[
            _fc('list_workspace', {}, 'c1'),
            _fc('list_workspace', {}, 'c2')]))],
    ], runner)
    results = _results(session)
    assert len(results) == 2
    assert not _is_dup_blocked(results[0])
    assert _is_dup_blocked(results[1])
    assert 'Earlier result:' in results[1]
    # Both dispatches landed in the SAME live-turn context, and
    # the second found the first call already cached — the handler
    # ran once, the duplicate guard did the rest.
    assert len(runner.seen) == 2
    assert runner.seen[0][0] == runner.seen[1][0]
    assert [s[2] for s in runner.seen] == [0, 1]


def test_different_calls_in_one_turn_both_execute():
    runner = _ObservingRunner()
    session = _run([
        [_resp(tc=types.SimpleNamespace(function_calls=[
            _fc('list_workspace', {}, 'c1'),
            _fc('list_windows', {}, 'c2')]))],
    ], runner)
    results = _results(session)
    assert len(results) == 2
    assert not _is_dup_blocked(results[0])
    assert not _is_dup_blocked(results[1])
    assert runner.seen[0][0] == runner.seen[1][0]
    assert [s[2] for s in runner.seen] == [0, 1]


def test_no_live_turn_context_lingers_after_session():
    runner = _ObservingRunner()
    _run([
        _call_turn(_fc('list_workspace', {}, 'c1')),
    ], runner)
    assert runtime_mod.snapshot() == []
