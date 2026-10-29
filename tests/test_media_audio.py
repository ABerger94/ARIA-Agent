"""media_audio: FFT band math and the publish/current feed.

The band math is pure numpy and is driven here with synthetic
tones. The capture thread is exercised only for its fallback
contract: on a machine without soundcard (this sandbox) start()
must still publish a simulated spectrum with live() False, and
stop() must let current() decay to zeros.
"""
import sys
import time
import types
from pathlib import Path

import numpy as np

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import media_audio


def _tone(freq, seconds=1.0, rate=48000, amp=0.8):
    t = np.arange(int(rate * seconds)) / rate
    return amp * np.sin(2 * np.pi * freq * t)


def test_band_count_and_range():
    bands = media_audio.compute_bands(_tone(440), 48000)
    assert len(bands) == media_audio.BANDS
    assert all(0.0 <= b <= 1.0 for b in bands)


def test_silence_is_zero():
    bands = media_audio.compute_bands(np.zeros(48000), 48000)
    assert max(bands) < 0.05


def _band_of(freq, rate=48000):
    for i, (lo, hi) in enumerate(media_audio.band_edges(rate)):
        if lo <= freq < hi:
            return i
    return media_audio.BANDS - 1


def test_bass_tone_lands_in_low_bands():
    bands = media_audio.compute_bands(_tone(60), 48000)
    home = _band_of(60)
    assert bands[home] == max(bands)
    assert bands[home] > 0.5
    assert bands[-1] < bands[home]


def test_no_dead_bands_around_a_tone():
    # Every band adjacent to a strong tone's home band must carry
    # some energy (the bin-owner fix): no zero holes at the low end.
    bands = media_audio.compute_bands(_tone(60), 48000)
    home = _band_of(60)
    assert bands[max(0, home - 1)] > 0.0
    assert bands[min(media_audio.BANDS - 1, home + 1)] > 0.0


def test_treble_tone_lands_in_high_bands():
    bands = media_audio.compute_bands(_tone(8000), 48000)
    home = _band_of(8000)
    assert bands[home] > 0.5
    assert bands[0] < bands[home]


def test_stereo_block_is_mixed_down():
    mono = _tone(120)
    stereo = np.stack([mono, mono], axis=1)
    bands = media_audio.compute_bands(stereo, 48000)
    assert bands[_band_of(120)] > 0.5


def test_publish_current_roundtrip_and_clamp():
    media_audio.publish((2.0,) * media_audio.BANDS, live=True)
    now = media_audio.current()
    assert len(now) == media_audio.BANDS
    assert all(b == 1.0 for b in now)
    assert media_audio.live()


def test_fallback_feed_publishes_simulation_not_live():
    # Reset the shared feed state: an earlier test in this file
    # published live=True, and live() is a freshness read.
    media_audio._BANDS_AT = 0.0
    media_audio._LIVE = False
    media_audio.start()
    try:
        deadline = time.monotonic() + 5.0
        got = (0.0,) * media_audio.BANDS
        while time.monotonic() < deadline:
            got = media_audio.current()
            if max(got) > 0.05:
                break
            time.sleep(0.05)
        assert max(got) > 0.05
        # soundcard is not installed in this sandbox: the feed
        # must be honest that it is simulating.
        import importlib.util
        if importlib.util.find_spec('soundcard') is None:
            assert not media_audio.live()
    finally:
        media_audio.stop()
