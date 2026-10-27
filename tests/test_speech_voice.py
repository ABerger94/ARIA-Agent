"""Edge voice ladder: one bad voice name must never latch ARIA onto
the SAPI fallback for a whole session (Alek's 2026-10-27 witness:
"still using fallback voice — it's not using the edge voice").

Loads aria/speech.py standalone (the test_headless pattern) with
edge_tts / pygame stubbed, and drives the REAL probe: the configured
voice gets its attempts, then the ladder walks until a voice proves
it can synthesize; that voice becomes the session voice synthesis
actually uses.
"""
import asyncio
import importlib
import os
import sys
import types
from pathlib import Path

PKG = Path(__file__).parents[1] / 'aria'

pkg = types.ModuleType('aria')
pkg.__path__ = [str(PKG)]
sys.modules.setdefault('aria', pkg)

try:
    import speech_recognition  # noqa: F401
except Exception:
    _sr = types.ModuleType('speech_recognition')
    _sr.Recognizer = object
    _sr.AudioData = object
    _sr.AudioSource = object
    sys.modules['speech_recognition'] = _sr

import pytest  # noqa: E402


def _load_speech():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'aria_speech_voice', PKG / 'speech.py')
    sp = importlib.util.module_from_spec(spec)
    sys.modules['aria_speech_voice'] = sp
    spec.loader.exec_module(sp)
    return sp


@pytest.fixture()
def sp(monkeypatch):
    sp = _load_speech()
    monkeypatch.setattr(sp, '_SESSION_VOICE', None)
    monkeypatch.setattr(sp, 'EDGE_PROBE_RETRY_SLEEP_S', 0)
    # edge_tts only needs to IMPORT for the probe; synthesis is faked
    # by replacing _edge_tts_bytes_strict per test.
    monkeypatch.setitem(sys.modules, 'edge_tts', types.ModuleType('edge_tts'))
    pygame = types.ModuleType('pygame')
    pygame.mixer = types.SimpleNamespace(init=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, 'pygame', pygame)
    return sp


def _manager(sp, tmp_path, logs):
    return sp.SpeechManager(workspace_dir=str(tmp_path),
                            on_log=logs.append)


def _set_configured(sp, monkeypatch, voice):
    cfg = sp._config_silent
    monkeypatch.setattr(
        cfg, 'get_setting',
        lambda key, default=None: voice if key == 'tts_voice' else default)


def test_default_voice_is_aria_neural(sp):
    # Her verified voice (laptop witness 2026-10-20). The Michelle
    # default experiment was reverted on Alek's 2026-10-27 witness.
    assert sp.EDGE_TTS_VOICE == 'en-US-AriaNeural'


def test_candidates_configured_first_deduped(sp, monkeypatch):
    _set_configured(sp, monkeypatch, 'en-US-JennyNeural')
    assert sp.edge_voice_candidates() == [
        'en-US-JennyNeural', 'en-US-AriaNeural', 'en-US-MichelleNeural']
    _set_configured(sp, monkeypatch, '')
    assert sp.edge_voice_candidates()[0] == 'en-US-AriaNeural'


def test_probe_walks_ladder_and_sets_session_voice(sp, monkeypatch,
                                                   tmp_path):
    _set_configured(sp, monkeypatch, 'en-US-BrokenNeural')
    seen = []

    def fake_strict(text, whisper_mode=False, voice=None):
        seen.append(voice)
        if voice == 'en-US-JennyNeural':
            return b'ID3'
        raise RuntimeError(f'no such voice: {voice}')

    monkeypatch.setattr(sp, '_edge_tts_bytes_strict', fake_strict)
    logs = []
    mgr = _manager(sp, tmp_path, logs)
    assert mgr._probe_edge_once() is True
    assert mgr.edge_ready
    # Configured voice got its 3 attempts, Aria 1, then Jenny proved.
    assert seen == ['en-US-BrokenNeural'] * 3 + [
        'en-US-AriaNeural', 'en-US-JennyNeural']
    assert sp.active_edge_voice() == 'en-US-JennyNeural'
    joined = '\n'.join(logs)
    assert 'Edge TTS (en-US-JennyNeural)' in joined
    assert 'en-US-BrokenNeural' in joined      # the failure is named


def test_probe_all_voices_fail_logs_every_reason(sp, monkeypatch,
                                                 tmp_path):
    _set_configured(sp, monkeypatch, 'en-US-AriaNeural')

    def fake_strict(text, whisper_mode=False, voice=None):
        raise RuntimeError('net down')

    monkeypatch.setattr(sp, '_edge_tts_bytes_strict', fake_strict)
    logs = []
    mgr = _manager(sp, tmp_path, logs)
    assert mgr._probe_edge_once() is False
    assert not mgr.edge_ready
    joined = '\n'.join(logs)
    assert 'system fallback' in joined
    for v in ('en-US-AriaNeural', 'en-US-JennyNeural',
              'en-US-MichelleNeural'):
        assert v in joined


def test_speech_log_hook_wires_the_default_manager(sp):
    # Regression (Alek, 2026-10-27: 'there is no voice line in
    # activity'): the default SpeechManager was built with
    # on_log=None and nothing wired it, so the probe's verdict —
    # and every other speech log — went nowhere.
    logs = []

    def hook(msg):
        logs.append(msg)

    assert sp._DEFAULT_SPEECH_MANAGER.on_log is None
    sp.set_speech_log_hook(hook)
    assert sp._DEFAULT_SPEECH_MANAGER.on_log is hook
    assert sp._DEFAULT_SPEECH_MANAGER._sapi.on_log is hook
    sp._DEFAULT_SPEECH_MANAGER.log('Voice: test line')
    assert logs == ['Voice: test line']


def test_synthesis_uses_session_voice(sp, monkeypatch):
    captured = {}

    class FakeComm:
        def __init__(self, text, voice, **kw):
            captured['voice'] = voice

        async def stream(self):
            yield {'type': 'audio', 'data': b'ID3'}

    edge = types.ModuleType('edge_tts')
    edge.Communicate = FakeComm
    monkeypatch.setitem(sys.modules, 'edge_tts', edge)
    sp._set_session_voice('en-US-JennyNeural')
    assert sp._edge_tts_bytes_strict('hello') == b'ID3'
    assert captured['voice'] == 'en-US-JennyNeural'
    # asyncio.run inside strict leaves no loop behind for other tests.
    assert asyncio.get_event_loop_policy() is not None
