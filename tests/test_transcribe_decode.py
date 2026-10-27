"""Phone-bridge transcription decode: container audio (webm/opus,
m4a) must reach the recognizer WITHOUT a system ffmpeg.

Alek's laptop has no ffmpeg on PATH (2026-10-27: every phone
voice request failed with 'transcribe needs ffmpeg to decode
webm audio'). PyAV is pinned in requirements.txt and bundles its
own decoder — _decode_to_pcm16 falls back to it.
"""
import io
import math
import struct
import sys
import types
from pathlib import Path

PKG = Path(__file__).parents[1] / 'aria'
_pkg = types.ModuleType('aria')
_pkg.__path__ = [str(PKG)]
sys.modules.setdefault('aria', _pkg)

try:
    import speech_recognition  # noqa: F401
except Exception:
    # Sandbox stand-in with the slice of the API speech.py uses.
    _sr = types.ModuleType('speech_recognition')

    class _AudioData:
        def __init__(self, raw_data, sample_rate, sample_width):
            self.raw_data = raw_data
            self.sample_rate = sample_rate
            self.sample_width = sample_width

        def get_raw_data(self):
            return self.raw_data

    class _Recognizer:
        def record(self, source):
            return source.read()

    _sr.Recognizer = _Recognizer
    _sr.AudioData = _AudioData
    _sr.AudioSource = object
    _sr.AudioFile = object
    sys.modules['speech_recognition'] = _sr

import pytest  # noqa: E402

from aria import speech  # noqa: E402


def _webm_opus_bytes(seconds=0.5, rate=48000):
    """A real webm/opus clip, encoded with PyAV itself."""
    av = pytest.importorskip('av')
    buf = io.BytesIO()
    with av.open(buf, mode='w', format='webm') as container:
        stream = container.add_stream('libopus', rate=rate)
        stream.layout = 'mono'
        frames = int(seconds * rate)
        done = 0
        while done < frames:
            n = min(960, frames - done)
            samples = [int(8000 * math.sin(2 * math.pi * 440 * (done + i) / rate))
                       for i in range(n)]
            frame = av.AudioFrame(format='s16', layout='mono', samples=n)
            frame.planes[0].update(struct.pack(f'<{n}h', *samples))
            frame.sample_rate = rate
            frame.pts = done
            frame.time_base = __import__('fractions').Fraction(1, rate)
            for packet in stream.encode(frame):
                container.mux(packet)
            done += n
        for packet in stream.encode(None):
            container.mux(packet)
    return buf.getvalue()


def test_av_fallback_decodes_webm_without_ffmpeg(monkeypatch):
    pytest.importorskip('av')
    clip = _webm_opus_bytes()

    def no_ffmpeg(_bytes):
        raise FileNotFoundError('ffmpeg not on PATH')
    monkeypatch.setattr(speech, '_ffmpeg_to_pcm16', no_ffmpeg)

    pcm = speech._decode_to_pcm16(clip)
    # ~0.5 s of mono s16 at 16 kHz, with resampler tolerance.
    expected = int(0.5 * 16000 * 2)
    assert expected * 0.6 < len(pcm) < expected * 1.2
    assert len(pcm) % 2 == 0


def test_no_ffmpeg_and_no_av_keeps_the_honest_error(monkeypatch):
    def no_ffmpeg(_bytes):
        raise FileNotFoundError('ffmpeg not on PATH')
    def no_av(_bytes):
        raise ImportError('No module named av')
    monkeypatch.setattr(speech, '_ffmpeg_to_pcm16', no_ffmpeg)
    monkeypatch.setattr(speech, '_av_to_pcm16', no_av)
    with pytest.raises(RuntimeError, match='ffmpeg.*not on PATH'):
        speech._decode_to_pcm16(b'whatever')


def test_ffmpeg_still_first_when_present(monkeypatch):
    monkeypatch.setattr(speech, '_ffmpeg_to_pcm16',
                        lambda _b: b'\x01\x02' * 100)
    def av_must_not_run(_b):
        raise AssertionError('PyAV ran despite ffmpeg succeeding')
    monkeypatch.setattr(speech, '_av_to_pcm16', av_must_not_run)
    assert speech._decode_to_pcm16(b'x') == b'\x01\x02' * 100


def test_transcribe_audio_routes_containers_through_the_decoder(monkeypatch):
    seen = {}

    def fake_decode(_bytes):
        return b'\x00\x00' * 8000
    def fake_pipeline(audio, recognizer):
        seen['rate'] = audio.sample_rate
        seen['width'] = audio.sample_width
        return ' hello '
    monkeypatch.setattr(speech, '_decode_to_pcm16', fake_decode)
    monkeypatch.setattr(speech, 'transcribe_local_or_cloud', fake_pipeline)
    assert speech.transcribe_audio(b'clip-bytes', 'audio/mp4') == 'hello'
    assert seen == {'rate': 16000, 'width': 2}
