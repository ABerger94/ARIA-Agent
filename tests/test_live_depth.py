"""Gemini Live depth (aria/live.py) — resumption + failover latch.

No google-genai, no audio devices, no network: the config builder
is driven with a stand-in types module (the test_live.py fakes'
shape), run_session with a scripted fake session, and LiveManager
through the client_factory seam with _genai_types patched to the
fake, _play_sink/_mic_pump replaced by inert fakes. Pins:
config carries the handle (incl. the no-arg-constructor fallback),
run_session captures only resumable resumption updates, the
manager stores a handle and hands it back on the next connect, a
rejected handle is dropped and retried ONCE fresh on the same
model, and the failover latch (rung0 fails -> rung1 connects,
on_state('failover') once, next _run starts at rung1).
"""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

# Stub the `aria` package like tests/test_fleet.py so the real
# package __init__ (which pulls the whole app) never runs here;
# live.py itself is loaded straight from its file, as test_live.py
# does.
_pkg = types.ModuleType('aria')
_pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', _pkg)

_spec = importlib.util.spec_from_file_location(
    'live_depth', Path(__file__).parents[1] / 'aria' / 'live.py')
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)


# -- fake google.genai types (same shape as test_live.py) ---------
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


class _NoArgResumption:
    """A SessionResumptionConfig that rejects constructor kwargs
    (the fallback shape build_live_config must tolerate)."""

    def __init__(self):
        pass


class _StrictFakeTypes(_FakeTypes):
    SessionResumptionConfig = _NoArgResumption


# -- config ---------------------------------------------------------
def test_config_carries_the_handle():
    cfg = live.build_live_config(
        _FakeTypes, voice='Kore', system_instruction='SOUL',
        declarations=[], handle='handle-abc')
    assert cfg.session_resumption.handle == 'handle-abc'
    # The rest of the shape is unchanged.
    assert cfg.response_modalities == ['AUDIO']
    vc = cfg.speech_config.voice_config.prebuilt_voice_config
    assert vc.voice_name == 'Kore'


def test_config_default_handle_is_none():
    cfg = live.build_live_config(
        _FakeTypes, voice='Kore', system_instruction='S',
        declarations=[])
    assert cfg.session_resumption.handle is None


def test_config_handle_fallback_for_noarg_resumption_config():
    cfg = live.build_live_config(
        _StrictFakeTypes, voice='Kore', system_instruction='S',
        declarations=[], handle='handle-xyz')
    # Constructor refused the kwarg; the attribute fallback set it.
    assert cfg.session_resumption.handle == 'handle-xyz'


# -- fake sessions ----------------------------------------------------
def _resp(sc=None, tc=None, sru='absent'):
    ns = types.SimpleNamespace(server_content=sc, tool_call=tc)
    if sru != 'absent':
        ns.session_resumption_update = sru
    return ns


def _sru(resumable, new_handle):
    return types.SimpleNamespace(
        resumable=resumable, new_handle=new_handle)


class _FakeSession:
    """One script = one receive() pass, consumed as served —
    matching the real SDK (each receive() yields one turn, then
    returns). A second receive() yields nothing (clean end), so
    run_session's multi-turn loop terminates."""

    def __init__(self, script):
        self._script = list(script)
        self.tool_responses = []

    async def receive(self):
        items, self._script = self._script, []
        for item in items:
            yield item

    async def send_tool_response(self, function_responses):
        self.tool_responses.append(function_responses)


def test_run_session_captures_resumption_updates():
    script = [
        _resp(sru=_sru(False, 'mid-turn-handle')),   # not resumable
        _resp(sru=_sru(True, 'handle-1')),           # kept
        _resp(sru=_sru(True, None)),                 # no handle
        _resp(),                                     # no update at all
        _resp(sru=_sru(True, 'handle-2')),           # kept, latest
        _resp(sru=None),                             # explicit None
    ]
    captured = []
    asyncio.run(live.run_session(
        _FakeSession(script), tool_runner=lambda n, a: ('ok', True),
        on_resumption=captured.append))
    assert captured == ['handle-1', 'handle-2']


def test_run_session_resumption_callback_error_is_guarded():
    def boom(handle):
        raise RuntimeError('callback exploded')

    script = [_resp(sru=_sru(True, 'handle-1'))]
    # Must complete without raising.
    asyncio.run(live.run_session(
        _FakeSession(script), tool_runner=lambda n, a: ('ok', True),
        on_resumption=boom))


# -- manager fakes ----------------------------------------------------
class _FakeStream:
    def stop(self):
        pass

    def close(self):
        pass


class _ConnectCM:
    def __init__(self, session=None, error=None):
        self._session = session
        self._error = error

    async def __aenter__(self):
        if self._error is not None:
            raise self._error
        return self._session

    async def __aexit__(self, *exc):
        return False


class _FakeLiveNs:
    """client.aio.live stand-in: records (model, config) per call
    and delegates the outcome to handler(model, config, n_calls)."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def connect(self, model=None, config=None):
        self.calls.append((model, config))
        outcome = self._handler(model, config, len(self.calls))
        if isinstance(outcome, Exception):
            return _ConnectCM(error=outcome)
        return _ConnectCM(session=outcome)


class _FakeClient:
    def __init__(self, live_ns):
        self.aio = types.SimpleNamespace(live=live_ns)


def _make_manager(monkeypatch, handler):
    monkeypatch.setattr(live, '_genai_types', _FakeTypes)
    live_ns = _FakeLiveNs(handler)
    client = _FakeClient(live_ns)
    states = []
    logs = []
    manager = live.LiveManager(
        api_key='k', voice='Kore', system_instruction='S',
        declarations=[],
        tool_runner=lambda n, a: ('ok', True),
        on_state=states.append, log=logs.append,
        client_factory=lambda api_key: client)

    async def _sink():
        return (lambda data: None, _FakeStream())

    async def _mic(session, loop):
        await asyncio.sleep(3600)

    manager._play_sink = _sink
    manager._mic_pump = _mic
    return manager, live_ns, states, logs


def _session_with_handle(handle):
    return _FakeSession([_resp(sru=_sru(True, handle))])


def test_manager_stores_handle_and_reuses_it_on_second_start(monkeypatch):
    def handler(model, config, n):
        return _session_with_handle('handle-xyz')

    manager, live_ns, states, logs = _make_manager(monkeypatch, handler)
    assert manager.resume_handle is None

    asyncio.run(manager._run())
    assert live_ns.calls[0][0] == live.LIVE_MODELS[0]
    assert live_ns.calls[0][1].session_resumption.handle is None
    assert manager.resume_handle == 'handle-xyz'
    assert manager._live_model == live.LIVE_MODELS[0]
    assert manager.live_model == live.LIVE_MODELS[0]

    asyncio.run(manager._run())
    assert len(live_ns.calls) == 2
    assert live_ns.calls[1][0] == live.LIVE_MODELS[0]
    assert live_ns.calls[1][1].session_resumption.handle == 'handle-xyz'
    assert manager.resume_handle == 'handle-xyz'

    manager.drop_resumption()
    assert manager.resume_handle is None
    asyncio.run(manager._run())
    assert live_ns.calls[2][1].session_resumption.handle is None


def test_manager_rejected_handle_retries_fresh_on_same_model(monkeypatch):
    def handler(model, config, n):
        if n == 1:
            return RuntimeError('Invalid resumption handle supplied')
        return _FakeSession([])

    manager, live_ns, states, logs = _make_manager(monkeypatch, handler)
    manager._resume_handle = 'stale-handle'

    asyncio.run(manager._run())
    assert len(live_ns.calls) == 2
    # Same model both times — the retry happens before stepping down.
    assert live_ns.calls[0][0] == live.LIVE_MODELS[0]
    assert live_ns.calls[1][0] == live.LIVE_MODELS[0]
    assert live_ns.calls[0][1].session_resumption.handle == 'stale-handle'
    assert live_ns.calls[1][1].session_resumption.handle is None
    assert manager.resume_handle is None
    assert any('Resumption handle rejected — starting a fresh session'
               in line for line in logs)


class _ExplodingSession:
    """Connect succeeds, then the receive stream raises — the
    'session raises' half of the rejected-handle rule."""

    def __init__(self, error):
        self._error = error
        self.tool_responses = []

    async def receive(self):
        raise self._error
        yield  # pragma: no cover — makes this an async generator

    async def send_tool_response(self, function_responses):
        self.tool_responses.append(function_responses)


def test_manager_rejected_handle_from_live_session_retries_fresh(monkeypatch):
    def handler(model, config, n):
        if n == 1:
            return _ExplodingSession(
                RuntimeError('session resumption expired'))
        return _FakeSession([])

    manager, live_ns, states, logs = _make_manager(monkeypatch, handler)
    manager._resume_handle = 'stale-2'

    asyncio.run(manager._run())
    assert len(live_ns.calls) == 2
    assert live_ns.calls[0][0] == live_ns.calls[1][0] == live.LIVE_MODELS[0]
    assert live_ns.calls[0][1].session_resumption.handle == 'stale-2'
    assert live_ns.calls[1][1].session_resumption.handle is None
    assert manager.resume_handle is None
    assert any('Resumption handle rejected' in line for line in logs)


def test_manager_failover_latches_and_next_run_starts_at_rung1(monkeypatch):
    rung0, rung1 = live.LIVE_MODELS[0], live.LIVE_MODELS[1]

    def handler(model, config, n):
        if model == rung0:
            return RuntimeError('connect boom')
        return _FakeSession([])

    manager, live_ns, states, logs = _make_manager(monkeypatch, handler)

    asyncio.run(manager._run())
    assert [m for m, _c in live_ns.calls] == [rung0, rung1]
    assert states.count('failover') == 1
    assert any(f'Gemini Live failed over to {rung1}' in line
               for line in logs)
    assert manager._live_model == rung1
    assert manager.live_model == rung1

    # Next _run starts at the latched rung: rung0 is not retried
    # first, and a clean run emits no second failover notice. A
    # clean session end does not clear the latch.
    live_ns.calls.clear()
    asyncio.run(manager._run())
    assert [m for m, _c in live_ns.calls] == [rung1]
    assert states.count('failover') == 1
    assert manager._live_model == rung1
