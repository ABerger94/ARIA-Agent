"""Gemini Live session exceptions (B5#6): a receive-loop exception
must NOT die unretrieved while _run returns as a clean end. When
the session task finishes first WITH an exception, _run must log
it, mark the failure, and break out of the retry loop so the
outer for-model loop fails over to the next Live model. No
google-genai, no audio devices, no network: fake client/session
plumbing, _run driven directly on a loop with a recording
exception handler (so a "Task exception was never retrieved"
warning is observable, not just hoped absent)."""
import asyncio
import gc
import importlib.util
import sys
import types
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'live_test_session_exc',
    Path(__file__).parents[1] / 'aria' / 'live.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class _Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeTypes:
    LiveConnectConfig = _Cfg
    SpeechConfig = _Cfg
    VoiceConfig = _Cfg
    PrebuiltVoiceConfig = _Cfg
    SessionResumptionConfig = _Cfg
    Blob = _Cfg


class _RaisingSession:
    """receive() raises at once — the SDK's disconnect shape
    (APIError when the websocket closes)."""

    def __init__(self):
        self.receive_calls = 0

    async def receive(self):
        self.receive_calls += 1
        raise RuntimeError('websocket closed (fake disconnect)')
        yield  # unreachable — marks this an async generator

    async def send_realtime_input(self, audio=None):
        return None

    async def send_tool_response(self, function_responses):
        return None


class _EmptySession:
    """receive() yields nothing — a clean end."""

    async def receive(self):
        return
        yield  # unreachable — marks this an async generator

    async def send_realtime_input(self, audio=None):
        return None

    async def send_tool_response(self, function_responses):
        return None


class _FakeLiveCM:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    """connect() records each model tried and hands out the
    session scripted for that model."""

    def __init__(self, sessions_by_model):
        self.sessions_by_model = sessions_by_model
        self.connects = []
        self.aio = types.SimpleNamespace(
            live=types.SimpleNamespace(connect=self._connect))

    def _connect(self, model=None, config=None):
        self.connects.append(model)
        return _FakeLiveCM(self.sessions_by_model[model])


async def _dummy_sink():
    return (lambda data: None,
            types.SimpleNamespace(stop=lambda: None, close=lambda: None))


async def _pump_forever(self, session, loop):
    await asyncio.sleep(3600)


def _make_manager(monkeypatch, logs, states, client):
    monkeypatch.setattr(live, '_genai_types', _FakeTypes)
    monkeypatch.setattr(live, '_voice_enabled', lambda: True)
    mgr = live.LiveManager(
        api_key='<redacted>',
        tool_runner=lambda n, a: ('', True),
        client_factory=lambda key: client,
        on_state=states.append,
        log=logs.append)
    monkeypatch.setattr(live.LiveManager, '_mic_pump', _pump_forever)
    monkeypatch.setattr(
        live.LiveManager, '_play_sink',
        lambda self: _dummy_sink())
    return mgr


def _run_with_handler(mgr):
    """Drive mgr._run() on a fresh loop with a recording
    exception handler; returns the recorded handler contexts."""
    contexts = []
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(
        lambda _loop, ctx: contexts.append(ctx))
    try:
        loop.run_until_complete(mgr._run())
    finally:
        # A task whose exception was never retrieved reports
        # itself to the loop's exception handler when GC'd.
        gc.collect()
        loop.close()
        asyncio.set_event_loop(None)
    return contexts


def test_session_exception_fails_over_to_next_model(monkeypatch):
    logs, states = [], []
    client = _FakeClient({
        live.LIVE_MODELS[0]: _RaisingSession(),
        live.LIVE_MODELS[1]: _EmptySession(),
    })
    mgr = _make_manager(monkeypatch, logs, states, client)
    contexts = _run_with_handler(mgr)
    # Both rungs tried, in ladder order — not a clean end after
    # the first model's session died.
    assert client.connects == list(live.LIVE_MODELS), client.connects
    # Failover was announced before the second model went live.
    assert states == ['live', 'failover', 'live'], states
    assert any('session failed' in m for m in logs), logs
    assert not any('never retrieved' in str(c.get('message', ''))
                   for c in contexts), contexts
    print("ok session_exception_failover")


def test_session_exception_on_last_model_raises(monkeypatch):
    """When every model's session dies the same way, _run must
    raise naming the real failure — not return cleanly and not
    report 'unavailable: None'."""
    import pytest
    logs, states = [], []
    client = _FakeClient({
        live.LIVE_MODELS[0]: _RaisingSession(),
        live.LIVE_MODELS[1]: _RaisingSession(),
    })
    mgr = _make_manager(monkeypatch, logs, states, client)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    contexts = []
    loop.set_exception_handler(
        lambda _loop, ctx: contexts.append(ctx))
    try:
        with pytest.raises(RuntimeError, match='websocket closed'):
            loop.run_until_complete(mgr._run())
    finally:
        gc.collect()
        loop.close()
        asyncio.set_event_loop(None)
    assert client.connects == list(live.LIVE_MODELS), client.connects
    assert not any('never retrieved' in str(c.get('message', ''))
                   for c in contexts), contexts
    print("ok session_exception_last_model")
