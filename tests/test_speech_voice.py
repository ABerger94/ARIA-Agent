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


def test_probe_not_gated_on_mixer(sp, monkeypatch, tmp_path):
    # The old probe init'd pygame's mixer BEFORE trying synthesis:
    # an audio device asleep/absent at boot failed the whole probe
    # and latched the session onto SAPI even though Edge synthesis
    # itself was fine. Synthesis is the probe's only question now.
    bad = types.ModuleType('pygame')

    def _boom(*a, **k):
        raise RuntimeError('No available audio device')

    bad.mixer = types.SimpleNamespace(init=_boom, get_init=lambda: None)
    monkeypatch.setitem(sys.modules, 'pygame', bad)
    monkeypatch.setattr(sp, '_edge_tts_bytes_strict',
                        lambda text, whisper_mode=False, voice=None: b'ID3')
    logs = []
    mgr = _manager(sp, tmp_path, logs)
    assert mgr._probe_edge_once() is True
    assert mgr.edge_ready


def test_ensure_mixer_reports_device_failure(sp, monkeypatch):
    bad = types.ModuleType('pygame')

    def _boom(*a, **k):
        raise RuntimeError('No available audio device')

    bad.mixer = types.SimpleNamespace(init=_boom, get_init=lambda: None)
    monkeypatch.setitem(sys.modules, 'pygame', bad)
    logs = []
    assert sp._ensure_mixer(logs.append) is False
    assert 'mixer init failed' in '\n'.join(logs)
    good = types.ModuleType('pygame')
    good.mixer = types.SimpleNamespace(init=lambda *a, **k: None,
                                       get_init=lambda: (44100, -16, 2))
    monkeypatch.setitem(sys.modules, 'pygame', good)
    assert sp._ensure_mixer(logs.append) is True


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


# -- Pass 36 #04: no cloud speech in Local only -------------------
# speech.py had zero PROVIDER_MODE refs: Google STT fallback and
# Edge TTS both ran in Local only, contradicting the toggle label.
# In local_only, STT is faster-whisper or typed-unavailable (never
# Google) and TTS is offline SAPI (never Edge); the Providers panel
# says so via speech_route_text().

class _Seg:
    def __init__(self, text, no_speech_prob=0.0):
        self.text = text
        self.no_speech_prob = no_speech_prob


class _FakeFwModel:
    def __init__(self, text="hello local world"):
        self._text = text

    def transcribe(self, pcm, **kw):
        return ([_Seg(self._text)], None)


def _fake_audio(sp):
    import speech_recognition as sr
    return sr.AudioData(b"\x00\x00" * 1600, 16000, 2)


def test_transcribe_local_only_never_calls_google(sp, monkeypatch):
    monkeypatch.setattr(sp, "_local_only", lambda: True)
    monkeypatch.setattr(sp, "get_fw_model", lambda on_log=None: None)
    monkeypatch.setattr(
        sp, "_recognize_google",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("Google STT must never run in Local only")))
    logs = []
    out = sp.transcribe(_fake_audio(sp), None, use_local_stt=True,
                        on_log=logs.append)
    assert out == "", out
    assert any("Local STT unavailable" in m for m in logs), logs


def test_transcribe_local_only_uses_local_model(sp, monkeypatch):
    monkeypatch.setattr(sp, "_local_only", lambda: True)
    monkeypatch.setattr(sp, "get_fw_model",
                        lambda on_log=None: _FakeFwModel())
    monkeypatch.setattr(
        sp, "_recognize_google",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("Google STT must never run in Local only")))
    out = sp.transcribe(_fake_audio(sp), None, use_local_stt=True)
    assert out == "hello local world", out


def test_transcribe_auto_still_uses_google_when_asked(sp, monkeypatch):
    monkeypatch.setattr(sp, "_local_only", lambda: False)
    monkeypatch.setattr(sp, "_recognize_google",
                        lambda *a, **k: "hello cloud")
    out = sp.transcribe(_fake_audio(sp), None, use_local_stt=False)
    assert out == "hello cloud", out


def test_edge_strict_refuses_in_local_only(sp, monkeypatch):
    monkeypatch.setattr(sp, "_local_only", lambda: True)
    with pytest.raises(RuntimeError, match="Local only mode"):
        sp._edge_tts_bytes_strict("hello")


def test_probe_refuses_edge_in_local_only(sp, monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "_local_only", lambda: True)
    monkeypatch.setattr(
        sp, "_edge_tts_bytes_strict",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("Edge must never be probed in Local only")))
    logs = []
    mgr = _manager(sp, tmp_path, logs)
    assert mgr._probe_edge_once() is False
    assert mgr.edge_ready is False
    assert any("Local only mode" in m for m in logs), logs


def test_bridge_tts_local_only_has_no_cloud_path(sp, monkeypatch):
    monkeypatch.setattr(sp, "_local_only", lambda: True)
    monkeypatch.setattr(
        sp, "_edge_tts_bytes_strict",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("Edge must never run in Local only")))
    with pytest.raises(RuntimeError, match="Local only mode"):
        sp.tts_bytes_for_bridge("hello")


def test_synth_worker_routes_to_sapi_in_local_only(sp, monkeypatch,
                                                   tmp_path):
    import threading
    import time
    monkeypatch.setattr(sp, "_local_only", lambda: True)
    logs = []
    mgr = _manager(sp, tmp_path, logs)
    mgr.edge_ready = True  # latched before the flip: must not matter
    monkeypatch.setattr(
        sp, "edge_tts_bytes",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("Edge must never synthesize in Local only")))
    t = threading.Thread(target=mgr._synth_worker, daemon=True)
    t.start()
    mgr.speech_queue.put("hello there")
    deadline = time.time() + 5
    item = None
    while time.time() < deadline:
        try:
            item = mgr.audio_play_queue.get(timeout=0.5)
            break
        except Exception:
            continue
    assert item is not None, "utterance never reached playback"
    text, audio_buf, is_edge = item
    assert text == "hello there" and audio_buf is None and is_edge is False


def test_speech_route_text_names_the_route(sp, monkeypatch):
    monkeypatch.setattr(sp, "_local_only", lambda: True)
    route = sp.speech_route_text()
    assert "Google STT" in route and "Edge TTS" in route
    assert "never called in Local only" in route
    monkeypatch.setattr(sp, "_local_only", lambda: False)
    route = sp.speech_route_text()
    assert "Google fallback" in route and "Edge TTS" in route
