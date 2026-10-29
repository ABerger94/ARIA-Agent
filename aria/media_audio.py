"""Live playback spectrum for the media visualizer (Alek, 2026-10-29).

Captures the default speaker's WASAPI loopback through the
`soundcard` package (when installed) on a daemon thread, reduces
each block to BANDS log-spaced frequency levels, and publishes
them for the dashboard's visualizer widget — the same
publish/current shape aria/audio_levels.py uses for her voice.

soundcard is optional: without it (or if loopback capture fails)
the thread publishes a simulated spectrum instead and live()
reports False, so the UI never pretends a simulation is the real
signal. Nothing here records or stores audio; blocks are reduced
to band levels in memory and discarded.
"""
from __future__ import annotations

import threading
import time

import numpy as np

BANDS = 28
_LOW_HZ = 40.0
_HIGH_HZ = 16000.0
_BLOCK = 2048
_RATE = 48000
_FRESH_S = 0.35

_BANDS_NOW = (0.0,) * BANDS
_BANDS_AT = 0.0
_LIVE = False

_EDGES_CACHE = {}


def band_edges(samplerate, bands=BANDS):
    """Log-spaced (low, high) Hz edges per band, cached per rate."""
    key = (samplerate, bands)
    edges = _EDGES_CACHE.get(key)
    if edges is None:
        pts = np.geomspace(_LOW_HZ, min(_HIGH_HZ, samplerate / 2.0),
                           bands + 1)
        edges = [(float(pts[i]), float(pts[i + 1])) for i in range(bands)]
        _EDGES_CACHE[key] = edges
    return edges


def compute_bands(samples, samplerate):
    """One block of float samples -> BANDS levels in 0..1.

    Pure (numpy only) so tests can drive it with synthetic tones.
    Accepts shape (n,) or (n, channels). Silence maps to zeros;
    a full-scale tone maps near 1 in its band.
    """
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if x.size == 0:
        return (0.0,) * BANDS
    if x.size < _BLOCK:
        x = np.pad(x, (0, _BLOCK - x.size))
    x = x[:_BLOCK] * np.hanning(_BLOCK)
    # Zero-padded FFT: interpolates the spectrum so the narrow
    # low bands (down to ~10 Hz wide) still land between FFT bins
    # (~23 Hz apart at this block size) instead of reading zero.
    _FFT = 8192
    mag = np.abs(np.fft.rfft(x, n=_FFT)) / (_BLOCK / 2.0)
    freqs = np.fft.rfftfreq(_FFT, 1.0 / samplerate)
    # Assign every FFT bin to exactly one band by its centre
    # frequency (clipped at the ends). A range-mask per band
    # leaves the narrow low bands with NO bins at all — at 48 kHz
    # the bin spacing is ~23 Hz and the first bands are ~10 Hz
    # wide — which drew dead bars at the bass end.
    edges = band_edges(samplerate)
    pts = np.array([lo for lo, _ in edges] + [edges[-1][1]])
    owner = np.clip(np.searchsorted(pts, freqs, side='right') - 1,
                    0, len(edges) - 1)
    peaks = np.zeros(len(edges))
    np.maximum.at(peaks, owner, mag)
    # dB-ish map: -54 dBFS peak -> 0, 0 dBFS -> 1.
    db = 20.0 * np.log10(peaks + 1e-6)
    return tuple(float(min(1.0, max(0.0, (v + 54.0) / 54.0)))
                 for v in db)


def publish(bands, live):
    global _BANDS_NOW, _BANDS_AT, _LIVE
    vals = tuple(float(min(1.0, max(0.0, b))) for b in bands)
    _BANDS_NOW = (vals + (0.0,) * BANDS)[:BANDS]
    _BANDS_AT = time.monotonic()
    _LIVE = bool(live)


def current():
    """Freshest band levels, or zeros when the feed has gone stale."""
    if time.monotonic() - _BANDS_AT < _FRESH_S:
        return _BANDS_NOW
    return (0.0,) * BANDS


def live():
    """True only while real loopback capture is publishing."""
    return _LIVE and time.monotonic() - _BANDS_AT < _FRESH_S


def _simulate(stop):
    """Fallback spectrum: layered sines with a slow random walk.

    Clearly a stand-in (live() stays False); it keeps the
    visualizer alive on machines without soundcard until the
    package is installed.
    """
    rng = np.random.default_rng(7)
    phase = rng.uniform(0.0, 6.28, BANDS)
    level = np.zeros(BANDS)
    t = 0
    while not stop.is_set():
        t += 1
        target = (0.45 + 0.35 * np.sin(t * 0.11 + phase)
                  + 0.2 * np.sin(t * 0.031 + phase * 2.0))
        target = np.clip(target, 0.03, 1.0)
        # Bass-heavy tilt, like real music.
        target = target * np.linspace(1.0, 0.45, BANDS)
        level = level + (target - level) * 0.25
        publish(tuple(float(v) for v in level), live=False)
        stop.wait(0.045)


def _capture(stop):
    try:
        import soundcard as sc
    except Exception:
        _simulate(stop)
        return
    try:
        speaker = sc.default_speaker()
        mic = sc.get_microphone(id=str(speaker.name),
                                include_loopback=True)
        if mic is None:
            raise RuntimeError('no loopback device')
        with mic.recorder(samplerate=_RATE, channels=2,
                          blocksize=_BLOCK) as rec:
            while not stop.is_set():
                data = rec.record(numframes=_BLOCK)
                publish(compute_bands(data, _RATE), live=True)
    except Exception:
        if not stop.is_set():
            _simulate(stop)


class _Feed:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=_capture,
                                        args=(self._stop,),
                                        daemon=True,
                                        name='aria-media-audio')
        self._thread.start()

    def stop(self):
        self._stop.set()
        global _LIVE
        _LIVE = False


_FEED = _Feed()


def start():
    """Start publishing band levels (idempotent)."""
    _FEED.start()


def stop():
    """Stop the capture thread; current() decays to zeros."""
    _FEED.stop()
