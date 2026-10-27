"""Measured playback levels for UI animation; no audio capture or playback."""
import time

_OUTPUT_LEVEL = 0.0
_OUTPUT_AT = 0.0


def publish(level):
    global _OUTPUT_LEVEL, _OUTPUT_AT
    _OUTPUT_LEVEL = max(0.0, min(1.0, float(level)))
    _OUTPUT_AT = time.monotonic()


def current():
    return _OUTPUT_LEVEL if time.monotonic() - _OUTPUT_AT < .2 else 0.0


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
