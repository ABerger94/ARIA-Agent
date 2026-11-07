"""Pass 27 #12: cancelling a Live session must cancel its tool worker.

Before: session cancellation removed only the async waiter — the
tool call already running in the executor kept going, and its
runtime.check_cancelled() passed because nothing ever set the
turn's event, so the effect happened after the session was gone.
Now run_session wires a per-turn cancellation event into the
execution context (propagated into the executor by copied_call),
the manager sets it before cancelling the session task on every
stop path, and run_session sets it for any other cancellation too.
"""
import asyncio
import os
import sys
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import speech_recognition  # noqa: F401
except ImportError:
    sys.modules.setdefault(
        'speech_recognition', types.ModuleType('speech_recognition'))

from aria import live as live_mod
from aria import runtime as runtime_mod


def _resp(**kw):
    return types.SimpleNamespace(
        server_content=kw.get('sc'), tool_call=kw.get('tc'))


class _FakeSession:
    """Yields one tool-call turn, then waits until cancelled
    (second test: ends cleanly instead)."""

    def __init__(self, *, end_after_turn=False):
        self._end_after_turn = end_after_turn
        self._turns_yielded = 0
        self.sent = []

    async def receive(self):
        # One tool-call turn, ever — later passes yield nothing
        # so run_session ends cleanly (an empty pass breaks its
        # loop); without this the session would replay the turn
        # on every receive() and run_session would never end.
        if self._turns_yielded == 0:
            self._turns_yielded += 1
            fc = types.SimpleNamespace(
                name="do_thing", id="fc1", args={})
            yield _resp(tc=types.SimpleNamespace(function_calls=[fc]))
        if self._end_after_turn:
            return
        await asyncio.sleep(3600)

    async def send_tool_response(self, function_responses):
        self.sent.append(function_responses)


def _wait_until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_cancelled_session_cancels_tool_worker():
    started = threading.Event()
    release = threading.Event()
    outcome = []
    sink = live_mod._TurnCancelSink()

    def tool_runner(name, args):
        started.set()
        release.wait(5)
        try:
            runtime_mod.check_cancelled()
        except runtime_mod.Cancelled:
            outcome.append("cancelled")
            return "cancelled"
        outcome.append("effect")
        return "effect"

    async def main():
        session = _FakeSession()
        task = asyncio.create_task(live_mod.run_session(
            session, tool_runner=tool_runner, cancel_sink=sink))
        for _ in range(300):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set(), "tool call never started"
        assert sink.current is not None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("run_session should end cancelled")
        assert sink.current is None
        # Release the worker BEFORE asyncio.run() closes the loop:
        # loop shutdown waits for the default executor, and the
        # worker is still blocked on release until now.
        release.set()
        for _ in range(300):
            if outcome:
                break
            await asyncio.sleep(0.01)

    asyncio.run(main())
    # The worker was released AFTER the cancellation: its
    # check_cancelled() must have failed, so no effect happened.
    assert outcome == ["cancelled"]


def test_clean_turn_leaves_worker_uncancelled():
    release = threading.Event()
    outcome = []
    sink = live_mod._TurnCancelSink()

    def tool_runner(name, args):
        try:
            runtime_mod.check_cancelled()
        except runtime_mod.Cancelled:
            outcome.append("cancelled")
            return "cancelled"
        outcome.append("effect")
        return "effect"

    async def main():
        session = _FakeSession(end_after_turn=True)
        await live_mod.run_session(
            session, tool_runner=tool_runner, cancel_sink=sink)
        assert sink.current is None

    asyncio.run(main())
    assert outcome == ["effect"]


def test_manager_stop_sets_turn_event():
    mgr = live_mod.LiveManager(api_key="test")
    event = threading.Event()
    mgr._turn_cancel.current = event
    mgr._cancel_turn_tools()
    assert event.is_set()
    # stop() with no live task must not raise, and sets the event.
    event2 = threading.Event()
    mgr._turn_cancel.current = event2
    mgr.stop()
    assert event2.is_set()
