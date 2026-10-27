"""Viseme feed: formant frames from the playback MP3, published
through audio_levels, shaping the holo head's mouth.

Mark-LVI computed (level, openness, width) per 20 ms from the
audio being played; ARIA-Agent's renderer supported the shape
since the port but nothing fed it — the mouth flapped on
loudness. These tests pin the feed: the math (fake pygame
decoder, synthesized vowel), the publish contract, and the
head's jaw following a closing schedule."""
import os
import sys
import types

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest

pkg = types.ModuleType('aria')
pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import audio_levels


def _fake_pygame(samples, rate=24000):
    mixer = types.SimpleNamespace(
        Sound=lambda file: object(),
        get_init=lambda: (rate, -16, 1))
    sndarray = types.SimpleNamespace(array=lambda sound: samples)
    return types.SimpleNamespace(mixer=mixer, sndarray=sndarray)


def test_mp3_visemes_reads_a_vowel_then_silence():
    sr = 24000
    t = np.arange(sr // 2) / sr
    vowel = (9000 * np.sin(2 * np.pi * 300 * t)
             + 9000 * np.sin(2 * np.pi * 900 * t)).astype(np.float32)
    samples = np.concatenate([vowel, np.zeros(sr // 2, dtype=np.float32)])
    frames = audio_levels.mp3_visemes(b'fake', _fake_pygame(samples, sr))
    assert len(frames) >= 45                      # ~50 frames per second
    loud = frames[10]
    assert loud[0] > 0.3                          # level present
    assert 0.0 < loud[1] <= 1.0                   # openness shaped
    assert -1.0 <= loud[2] <= 1.0                 # width bounded
    assert frames[-1] == (0.0, 0.0, 0.0)          # silence closes


def test_mp3_visemes_undecodable_is_empty_not_fatal():
    bad = types.SimpleNamespace(
        mixer=types.SimpleNamespace(
            Sound=lambda file: (_ for _ in ()).throw(ValueError('no')),
            get_init=lambda: (24000, -16, 1)),
        sndarray=types.SimpleNamespace(array=lambda s: None))
    assert audio_levels.mp3_visemes(b'x', bad) == []


def test_publish_contract_level_only_and_full():
    audio_levels.publish_viseme(0.5, 0.7, -0.2)
    v = audio_levels.viseme()
    assert v[0] == 0.5 and abs(v[1] - 0.7) < 1e-9 and abs(v[2] + 0.2) < 1e-9
    audio_levels.publish(0.3)                    # SAPI path: level only
    v = audio_levels.viseme()
    assert v[0] == pytest.approx(0.3) and v[1] is None and v[2] is None


def test_holo_jaw_follows_a_closing_schedule():
    pytest.importorskip('PyQt6')
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from aria.holo_face import HoloFace
    face = HoloFace()
    for _ in range(60):
        face.advance('speaking', 0.8, False, viseme=(0.8, 0.04, 0.0))
    assert face._avatar._v_open < 0.4             # jaw shape followed down
    for _ in range(60):
        face.advance('speaking', 0.8, False, viseme=(0.8, 0.95, 0.0))
    assert face._avatar._v_open > 0.6             # and back up
