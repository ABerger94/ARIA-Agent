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
    def __init__(self, script):
        self._script = script
        self.tool_responses = []

    async def receive(self):
        for item in self._script:
            yield item

    async def send_tool_response(self, function_responses):
        self.tool_responses.append(function_responses)


def test_run_session_routes_tools_and_transcripts():
    fc = types.SimpleNamespace(
        id='c1', name='get_time', args={'tz': 'ET'})
    script = [
        _resp(sc=_sc(model_parts=[_audio_part(b'\x01\x02')], it='what time')),
        _resp(sc=_sc(ot='It is noon', turn=True)),
        _resp(tc=types.SimpleNamespace(function_calls=[fc])),
        _resp(sc=_sc(ot='Done', turn=True)),
    ]
    calls = []

    def runner(name, args):
        calls.append((name, args))
        return ('12:00', True)

    heard = {'user': [], 'asst': [], 'audio': []}
    session = _FakeSession(script)
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
        [_resp(tc=types.SimpleNamespace(function_calls=[fc]))])
    asyncio.run(live.run_session(session, tool_runner=runner))
    result = session.tool_responses[0][0]['response']['result']
    assert 'Tool Error' in result and 'kaboom' in result


def test_manager_refuses_without_key_or_runner():
    m = live.LiveManager(api_key='', tool_runner=lambda n, a: ('', True))
    assert m.start() is False
    m2 = live.LiveManager(api_key='k', tool_runner=None)
    assert m2.start() is False


def test_model_ladder_order():
    assert live.LIVE_MODELS[0] == 'gemini-3.1-flash-live-preview'
    assert 'gemini-2.5-flash-native-audio' in live.LIVE_MODELS[1]
    assert live.MIC_RATE == 16000 and live.OUT_RATE == 24000
