"""Gemini Live voice mode (aria/live.py) — fake-session tests.

No google-genai, no audio devices, no network: the config builder
is driven with a stand-in types module, and run_session with a
scripted fake session. Pins: the config shape (AUDIO, chosen
voice, her declarations verbatim, transcriptions on), tool calls
routed through the injected dispatcher and answered with
FunctionResponse payloads, transcripts flushing on turn_complete,
audio reaching the sink, and the manager refusing to start
without a key or a dispatcher (the pipeline fallback contract).
"""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'live_test', Path(__file__).parents[1] / 'aria' / 'live.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


# -- fake google.genai types --------------------------------------
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


def test_build_config_shape():
    decls = [{'name': 'noop', 'parameters': {'type': 'OBJECT'}}]
    cfg = live.build_live_config(
        _FakeTypes, voice='Kore', system_instruction='SOUL',
        declarations=decls)
    assert cfg.response_modalities == ['AUDIO']
    vc = cfg.speech_config.voice_config.prebuilt_voice_config
    assert vc.voice_name == 'Kore'
    assert cfg.tools == [{'function_declarations': decls}]
    assert cfg.system_instruction == 'SOUL'
    assert cfg.output_audio_transcription == {}
    assert cfg.input_audio_transcription == {}


def test_build_config_default_voice():
    cfg = live.build_live_config(
        _FakeTypes, voice='', system_instruction='S', declarations=[])
    vc = cfg.speech_config.voice_config.prebuilt_voice_config
    assert vc.voice_name == live.DEFAULT_VOICE == 'Kore'


# -- fake session ---------------------------------------------------
def _resp(**kw):
    return types.SimpleNamespace(
        server_content=kw.get('sc'), tool_call=kw.get('tc'))


def _sc(model_parts=None, it=None, ot=None, turn=False):
    mt = None
    if model_parts is not None:
        mt = types.SimpleNamespace(parts=model_parts)
    return types.SimpleNamespace(
        model_turn=mt,
        input_transcription=(
            types.SimpleNamespace(text=it) if it is not None else None),
        output_transcription=(
            types.SimpleNamespace(text=ot) if ot is not None else None),
        turn_complete=turn)


def _audio_part(data):
    return types.SimpleNamespace(
        inline_data=types.SimpleNamespace(data=data))


class _FakeSession:
    """Models the real SDK's receive() contract: each receive()
    call yields ONE turn (the messages up to its turn_complete),
    then returns — google-genai's AsyncSession.receive() breaks
    its loop at interaction-complete. After the scripted turns
    are exhausted, the next receive() yields nothing (clean end),
    or with end='raise' raises, modelling the SDK's disconnect
    (APIError when the websocket closes)."""

    def __init__(self, turns, end='empty'):
        self._turns = [list(t) for t in turns]
        self._end = end
        self.tool_responses = []
        self.receive_calls = 0

    async def receive(self):
        self.receive_calls += 1
        if self._turns:
            for item in self._turns.pop(0):
                yield item
            return
        if self._end == 'raise':
            raise RuntimeError('websocket closed (fake disconnect)')
        return
        yield  # unreachable — marks this an async generator

    async def send_tool_response(self, function_responses):
        self.tool_responses.append(function_responses)


def test_run_session_routes_tools_and_transcripts():
    fc = types.SimpleNamespace(
        id='c1', name='get_time', args={'tz': 'ET'})
    turns = [
        [_resp(sc=_sc(model_parts=[_audio_part(b'\x01\x02')], it='what time')),
         _resp(sc=_sc(ot='It is noon', turn=True))],
        [_resp(tc=types.SimpleNamespace(function_calls=[fc])),
         _resp(sc=_sc(ot='Done', turn=True))],
    ]
    calls = []

    def runner(name, args):
        calls.append((name, args))
        return ('12:00', True)

    heard = {'user': [], 'asst': [], 'audio': []}
    session = _FakeSession(turns)
    asyncio.run(live.run_session(
        session, tool_runner=runner,
        on_user_text=heard['user'].append,
        on_assistant_text=heard['asst'].append,
        on_audio=heard['audio'].append))
    assert calls == [('get_time', {'tz': 'ET'})]
    assert session.tool_responses == [
        [{'id': 'c1', 'name': 'get_time',
          'response': {'result': '12:00'}}]]
    assert heard['user'] == ['what time']
    assert heard['asst'] == ['It is noon', 'Done']
    assert heard['audio'] == [b'\x01\x02']


def test_run_session_tool_error_is_an_answer():
    fc = types.SimpleNamespace(id='c2', name='boom', args={})

    def runner(name, args):
        raise RuntimeError('kaboom')

    session = _FakeSession(
        [[_resp(tc=types.SimpleNamespace(function_calls=[fc]))]])
    asyncio.run(live.run_session(session, tool_runner=runner))
    result = session.tool_responses[0][0]['response']['result']
    assert 'Tool Error' in result and 'kaboom' in result


# -- multi-turn receive (ChatGPT pass 2 runner-up, fixed 2026-10-28) --

def test_run_session_survives_multiple_turns():
    """One receive() pass = one turn. The session must re-enter
    receive() on the same connection and keep going: three turns
    in, all three transcripts land, in order."""
    turns = [
        [_resp(sc=_sc(it='first', ot='one', turn=True))],
        [_resp(sc=_sc(it='second', ot='two', turn=True))],
        [_resp(sc=_sc(it='third', ot='three', turn=True))],
    ]
    heard = {'user': [], 'asst': []}
    session = _FakeSession(turns)
    asyncio.run(live.run_session(
        session, tool_runner=lambda n, a: ('', True),
        on_user_text=heard['user'].append,
        on_assistant_text=heard['asst'].append))
    assert heard['user'] == ['first', 'second', 'third']
    assert heard['asst'] == ['one', 'two', 'three']
    # 3 turn passes + the final empty pass that ends the session.
    assert session.receive_calls == 4


def test_run_session_disconnect_propagates():
    """A genuine disconnect raises out of receive() (the SDK
    raises APIError on websocket close). run_session must NOT
    swallow it into a retry spin — it propagates to the manager,
    and the completed turn before it was still flushed."""
    import pytest
    turns = [[_resp(sc=_sc(ot='before the drop', turn=True))]]
    heard = []
    session = _FakeSession(turns, end='raise')
    with pytest.raises(RuntimeError, match='websocket closed'):
        asyncio.run(live.run_session(
            session, tool_runner=lambda n, a: ('', True),
            on_assistant_text=heard.append))
    assert heard == ['before the drop']


def test_run_session_empty_stream_ends_cleanly():
    """A receive() pass yielding nothing ends the session at
    once — no hot re-entry loop."""
    session = _FakeSession([])
    asyncio.run(live.run_session(
        session, tool_runner=lambda n, a: ('', True)))
    assert session.receive_calls == 1


def test_manager_refuses_without_key_or_runner():
    m = live.LiveManager(api_key='', tool_runner=lambda n, a: ('', True))
    assert m.start() is False
    m2 = live.LiveManager(api_key='k', tool_runner=None)
    assert m2.start() is False


def test_model_ladder_order():
    assert live.LIVE_MODELS[0] == 'gemini-3.1-flash-live-preview'
    assert 'gemini-2.5-flash-native-audio' in live.LIVE_MODELS[1]


# -- pass 25: voice-off, stop, mic failure ---------------------------
# Fake client/session plumbing for _run-level tests. No google-genai,
# no sounddevice, no network: the types module is the _FakeTypes
# stand-in above, the mic pump and play sink are stubbed per test.

class _BlockingSession:
    """A session whose receive() blocks forever (the model never
    speaks and never disconnects) — the shape that exposed both
    the stop bug (receive loop never ends) and the mic bug (a dead
    mic left this session looking alive)."""

    def __init__(self):
        self.receive_calls = 0

    async def receive(self):
        self.receive_calls += 1
        await asyncio.sleep(3600)
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
    def __init__(self, session):
        self.session = session
        self.aio = types.SimpleNamespace(
            live=types.SimpleNamespace(
                connect=lambda model=None, config=None:
                    _FakeLiveCM(session)))


def _make_manager(monkeypatch, logs, session, mic_stub, voice_on=True):
    monkeypatch.setattr(live, '_genai_types', _FakeTypes)
    monkeypatch.setattr(live, '_voice_enabled', lambda: voice_on)
    mgr = live.LiveManager(
        api_key='<redacted>',
        tool_runner=lambda n, a: ('', True),
        client_factory=lambda key: _FakeClient(session),
        log=logs.append)
    monkeypatch.setattr(live.LiveManager, '_mic_pump', mic_stub)
    monkeypatch.setattr(
        live.LiveManager, '_play_sink',
        lambda self: _dummy_sink())
    return mgr


async def _dummy_sink():
    return (lambda data: None,
            types.SimpleNamespace(stop=lambda: None, close=lambda: None))


async def _pump_forever(self, session, loop):
    await asyncio.sleep(3600)


async def _pump_dead(self, session, loop):
    raise RuntimeError('mic exploded (fake)')


def test_start_refuses_when_voice_off(monkeypatch):
    logs = []
    session = _BlockingSession()
    mgr = _make_manager(monkeypatch, logs, session, _pump_forever,
                        voice_on=False)
    monkeypatch.setattr(live, 'HAVE_GENAI', True)
    monkeypatch.setattr(live, '_genai', types.SimpleNamespace())
    assert mgr.start() is False
    assert mgr._thread is None or not mgr._thread.is_alive()
    assert any('voice is off' in m for m in logs), logs


def test_start_works_when_voice_on(monkeypatch):
    logs = []
    session = _BlockingSession()
    mgr = _make_manager(monkeypatch, logs, session, _pump_forever,
                        voice_on=True)
    monkeypatch.setattr(live, 'HAVE_GENAI', True)
    monkeypatch.setattr(live, '_genai', types.SimpleNamespace())
    assert mgr.start() is True
    assert mgr._thread is not None and mgr._thread.is_alive()
    mgr.stop()
    mgr._thread.join(timeout=5)
    assert not mgr._thread.is_alive()


def test_stop_ends_blocked_session(monkeypatch):
    # Pass 25 (P1#7): stop() used to set the event only, and the
    # receive loop never ended. With a session blocked in
    # receive() forever, stop() must end the thread promptly and
    # clear active. Driven through the real start()/stop() path.
    import time
    logs = []
    session = _BlockingSession()
    mgr = _make_manager(monkeypatch, logs, session, _pump_forever)
    monkeypatch.setattr(live, 'HAVE_GENAI', True)
    monkeypatch.setattr(live, '_genai', types.SimpleNamespace())
    assert mgr.start() is True
    for _ in range(100):
        if mgr.active:
            break
        time.sleep(0.05)
    assert mgr.active, 'session never became live'
    mgr.stop()
    mgr._thread.join(timeout=5)
    assert not mgr._thread.is_alive()
    assert mgr.active is False


def test_mic_failure_ends_session(monkeypatch):
    # Pass 25 (P2#10): a dead mic used to leave an apparently
    # active, silent session. The mic task dying must end the
    # session, clear active, and say so in the log.
    logs = []
    session = _BlockingSession()
    mgr = _make_manager(monkeypatch, logs, session, _pump_dead)
    monkeypatch.setattr(live, 'HAVE_GENAI', True)
    monkeypatch.setattr(live, '_genai', types.SimpleNamespace())
    assert mgr.start() is True
    mgr._thread.join(timeout=5)
    assert not mgr._thread.is_alive()
    assert mgr.active is False
    assert any('mic failed' in m for m in logs), logs
    assert live.MIC_RATE == 16000 and live.OUT_RATE == 24000


# -- PROVIDER_MODE gate: local_only refuses Live -------------------
# Strict local-only mode (Alek, 2026-11-10) forbids API-keyed
# calls, and a Gemini Live session is one. The gate sits at the
# top of start(), ahead of every other refusal, so in local_only
# no client is created, no thread spawns, and no connection is
# attempted — the refusal is the mode message in the log, and
# main.py's caller falls back to the pipeline as usual.

def test_provider_mode_helper_reads_config_live(monkeypatch):
    """The gate reads aria.config.PROVIDER_MODE at call time,
    not an import-time copy, so the value the keys file produced
    at startup is the value start() obeys."""
    from aria import config as _cfg
    monkeypatch.setattr(_cfg, 'PROVIDER_MODE', 'local_only')
    assert live._provider_mode() == 'local_only'
    assert live._local_only() is True
    monkeypatch.setattr(_cfg, 'PROVIDER_MODE', 'auto')
    assert live._provider_mode() == 'auto'
    assert live._local_only() is False


def test_start_refused_in_local_only_mode(monkeypatch):
    logs = []
    factory_calls = []

    def _spy_factory(key):
        factory_calls.append(key)
        return _FakeClient(_BlockingSession())

    monkeypatch.setattr(live, 'HAVE_GENAI', True)
    monkeypatch.setattr(live, '_genai', types.SimpleNamespace())
    monkeypatch.setattr(live, '_genai_types', _FakeTypes)
    monkeypatch.setattr(live, '_voice_enabled', lambda: True)
    monkeypatch.setattr(live, '_provider_mode', lambda: 'local_only')
    mgr = live.LiveManager(
        api_key='fake-key', tool_runner=lambda n, a: ('', True),
        client_factory=_spy_factory, log=logs.append)
    assert mgr.start() is False
    # Refused BEFORE any connection machinery: the client
    # factory never fired, no session thread exists, and the
    # manager never became active.
    assert factory_calls == [], factory_calls
    assert mgr._thread is None
    assert mgr.active is False
    assert any('local-only mode is on' in m and 'Gemini cloud' in m
               for m in logs), logs


def test_start_allowed_in_auto_mode(monkeypatch):
    """PROVIDER_MODE=auto: the mode gate does not fire — with the
    usual fakes, start() proceeds exactly as before and the
    session comes up through the client factory."""
    import time
    logs = []
    session = _BlockingSession()
    mgr = _make_manager(monkeypatch, logs, session, _pump_forever)
    monkeypatch.setattr(live, 'HAVE_GENAI', True)
    monkeypatch.setattr(live, '_genai', types.SimpleNamespace())
    monkeypatch.setattr(live, '_provider_mode', lambda: 'auto')
    assert mgr.start() is True
    assert mgr._thread is not None and mgr._thread.is_alive()
    for _ in range(100):
        if mgr.active:
            break
        time.sleep(0.05)
    assert mgr.active, 'session never became live in auto mode'
    assert not any('local-only' in m for m in logs), logs
    mgr.stop()
    mgr._thread.join(timeout=5)
    assert not mgr._thread.is_alive()


# -- Pass 36 #03: a mode flip mid-session stops the cloud session --
# start() checked the mode once; the toggle never signalled the
# manager, so an open session kept streaming mic audio after the
# flip to Local only, and there was no re-arm on return to Auto.
# Now: _run checks the mode before every (re)connect attempt, the
# mic pump stops sending on a flip, stop() drains queued audio,
# and flips are published to main.py's listener (tested here at
# the live.py publish seam; the main.py handler is exercised by
# the toggle path).

def test_run_never_connects_after_flip_to_local_only(monkeypatch):
    logs = []
    connects = []
    session = _BlockingSession()

    class _RecordingClient:
        def __init__(self, sess):
            self.session = sess
            self.aio = types.SimpleNamespace(
                live=types.SimpleNamespace(
                    connect=lambda model=None, config=None: (
                        connects.append(model),
                        _FakeLiveCM(session))[1]))

    mgr = live.LiveManager(
        api_key="k", tool_runner=lambda n, a: ("", True),
        client_factory=lambda key: _RecordingClient(session),
        log=logs.append)
    monkeypatch.setattr(live, "_genai_types", _FakeTypes)
    monkeypatch.setattr(live, "_local_only", lambda: True)
    asyncio.run(mgr._run())
    assert connects == [], connects
    assert mgr.active is False
    assert any("local-only mode is on" in m for m in logs), logs


def test_stop_drains_queued_mic_audio():
    import queue
    mgr = live.LiveManager(api_key="k",
                           tool_runner=lambda n, a: ("", True))
    q = queue.Queue()
    q.put(b"chunk-one")
    q.put(b"chunk-two")
    mgr._mic_queue = q
    mgr.stop()
    assert q.empty(), "queued mic audio must not survive the stop"


def test_mode_listeners_get_published_flips():
    live._MODE_LISTENERS.clear()
    try:
        seen = []
        live.register_mode_listener(seen.append)
        live.register_mode_listener(seen.append)  # deduped
        live.publish_provider_mode("local_only")
        assert seen == ["local_only"]
        # A raising listener never breaks the publish for others.
        live.register_mode_listener(lambda m: 1 / 0)
        live.publish_provider_mode("auto")
        assert seen == ["local_only", "auto"]
    finally:
        live._MODE_LISTENERS.clear()
