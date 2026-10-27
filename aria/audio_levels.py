"""Measured playback levels for UI animation; no audio capture or playback."""
import time

_OUTPUT_LEVEL = 0.0
_OUTPUT_AT = 0.0


def publish(level):
    global _OUTPUT_LEVEL, _OUTPUT_AT, _VISEME, _VISEME_AT
    _OUTPUT_LEVEL = max(0.0, min(1.0, float(level)))
    _OUTPUT_AT = time.monotonic()
    # A plain level publish is a level-only viseme (the SAPI path
    # has no PCM to read formants from): openness/width stay None
    # and the head falls back to loudness articulation.
    _VISEME = (_OUTPUT_LEVEL, None, None)
    _VISEME_AT = _OUTPUT_AT


def current():
    return _OUTPUT_LEVEL if time.monotonic() - _OUTPUT_AT < .2 else 0.0


_VISEME = (0.0, None, None)
_VISEME_AT = 0.0


def publish_viseme(level, openness, width):
    """Publish one viseme frame (level, openness 0..1, width
    -1..1) from the playback path's formant read."""
    global _VISEME, _VISEME_AT
    publish(level)
    _VISEME = (max(0.0, min(1.0, float(level))),
               None if openness is None else max(0.0, min(1.0, float(openness))),
               None if width is None else max(-1.0, min(1.0, float(width))))
    _VISEME_AT = time.monotonic()


def viseme():
    """The freshest viseme frame, or None when stale (>0.25 s).
    openness/width may be None on the level-only path."""
    return _VISEME if time.monotonic() - _VISEME_AT < .25 else None


def mp3_envelope(raw, pygame):
    """Best effort SDL decode, at mixer rate, to 40 ms RMS bins.

    If a platform cannot decode Sound MP3, speech still works and the orb
    uses state animation; no synthetic measurements are substituted.
    """
    try:
        import io
        import numpy as np
        sound = pygame.mixer.Sound(file=io.BytesIO(raw))
        samples = pygame.sndarray.array(sound).astype(np.float32)
        rate, bits, channels = pygame.mixer.get_init()
        if channels > 1:
            samples = samples.mean(axis=1)
        size = max(1, int(rate * .04))
        samples = samples[:len(samples) // size * size].reshape(-1, size)
        if not len(samples):
            return []
        scale = float(2 ** (abs(bits) - 1))
        if bits > 0:
            samples -= scale
        return np.minimum(1.0, np.sqrt(np.mean((samples / scale) ** 2, axis=1)) * 5).tolist()
    except Exception:
        return []


# Mark-LVI's formant bands (main.py _pcm_visemes, CC BY-NC — see
# LICENSES/Mark-LVI.txt): F1 separates close from open vowels, F2
# separates rounded from spread, the hiss band catches fricatives.
_VIS_BANDS = ((150, 450), (450, 1100), (600, 1300), (1700, 3200),
              (3800, 8000))
_LEVEL_FLOOR = 60.0      # int16 RMS treated as silence
_LEVEL_FULL = 2600.0     # int16 RMS treated as loud


def mp3_visemes(raw, pygame):
    """(level, openness, width) frames, one per 20 ms, read from
    the MP3's own formants — the schedule the holo head's mouth
    articulates from. [] when the platform cannot decode; the
    caller falls back to the loudness envelope and the mouth
    falls back with it, exactly as LVI's did."""
    try:
        import io
        import numpy as np
        sound = pygame.mixer.Sound(file=io.BytesIO(raw))
        samples = pygame.sndarray.array(sound).astype(np.float32)
        rate, bits, channels = pygame.mixer.get_init()
        if not rate:
            return []
        if channels > 1:
            samples = samples.mean(axis=1)
        scale = float(2 ** (abs(bits) - 1))
        if bits > 0:
            samples = samples - scale
        x = samples * (32768.0 / scale)          # int16-equivalent
        win_n = max(64, int(round(rate * 1024 / 24000)))
        hop = max(1, int(round(rate * 0.02)))
        if x.size < hop:
            return []
        win = np.hanning(win_n).astype(np.float32)
        freqs = np.fft.rfftfreq(win_n, 1.0 / rate)
        bands = [(freqs >= lo) & (freqs < hi) for lo, hi in _VIS_BANDS]
        out = []
        for start in range(0, x.size, hop):
            seg = x[start:start + hop]
            rms = float(np.sqrt(np.mean(seg * seg))) if seg.size else 0.0
            level = 0.0 if rms <= _LEVEL_FLOOR else min(
                1.0, (rms - _LEVEL_FLOOR) / (_LEVEL_FULL - _LEVEL_FLOOR))
            if level <= 0.0:
                out.append((0.0, 0.0, 0.0))
                continue
            wseg = x[start:start + win_n]
            if wseg.size < win_n:
                wseg = np.concatenate(
                    [wseg, np.zeros(win_n - wseg.size, dtype=np.float32)])
            mag = np.abs(np.fft.rfft((wseg - wseg.mean()) * win))
            f1l, f1h, f2b, f2f, hiss = (float(mag[b].sum()) for b in bands)
            openness = f1h / (f1l + f1h + 1e-6)
            width = (f2f - f2b) / (f2f + f2b + 1e-6)
            # A wide-open jaw cannot purse (keeps /a/ from reading
            # rounded); fricatives form nearly closed.
            width *= (1.0 - openness) ** 0.8
            h = hiss / (f1l + f1h + f2b + f2f + hiss + 1e-6)
            openness *= 1.0 - 0.65 * min(1.0, h * 2.5)
            out.append((level,
                        float(min(1.0, max(0.0, openness))),
                        float(min(1.0, max(-1.0, width)))))
        return out
    except Exception:
        return []
