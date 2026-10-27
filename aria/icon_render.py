"""The desktop icon: ARIA's waveform orb, rendered (Alek).

Ported from Mark-LVI-ARIA's MainWindow._build_aria_icon
(ui.py @ 61a47b0) — the design Alek approved there on
2026-10-23, replacing the donor arc-reactor icon, and asked
for here on 2026-10-27 when the shortcut shipped with the
plain Python interpreter icon instead.

Renders at 4x and downsamples for crisp results at every
size, seeded so every build draws the identical icon, and
saves a multi-resolution .ico (256/128/64/48/32/16). Pillow
does the drawing; any failure returns False and the caller
falls back honestly (the interpreter's own icon).
"""
from __future__ import annotations

from pathlib import Path
from typing import Union


def build_aria_icon(out_path: Union[str, Path]) -> bool:
    """Render the resonance-orb icon to out_path (.ico).
    True on success; False on any failure — never raises."""
    try:
        import math
        import random

        import PIL.Image
        import PIL.ImageDraw
        import PIL.ImageFilter
    except ImportError:
        return False

    CYAN = (0, 212, 255)
    BRIGHT = (120, 235, 255)
    DIM = (0, 110, 150)
    DARK = (0, 6, 10)
    WHITE = (225, 245, 255)

    # Equalizer spike envelope: smooth pseudo-random, seeded so
    # every build draws the same icon.
    _rng = random.Random(1917)
    _spikes = []
    _v = 0.45
    for _i in range(48):
        _v = max(0.12, min(1.0, _v + _rng.uniform(-0.22, 0.22)))
        _spikes.append(_v)

    def _wave_r(theta: float, R: float) -> float:
        # The resonance ribbon: layered sines, a voice print
        # in a circle.
        return R * (0.56
                    + 0.075 * math.sin(3 * theta + 0.7)
                    + 0.05 * math.sin(5 * theta + 2.1)
                    + 0.028 * math.sin(9 * theta + 4.0))

    def _render(sz: int) -> PIL.Image.Image:
        S = sz * 4                     # draw at 4x then downscale
        img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = PIL.ImageDraw.Draw(img)
        cx = cy = S // 2

        # Filled background circle.
        R = S // 2 - 2
        d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(*DARK, 255))

        # Outer border ring.
        lw = max(2, S // 40)
        d.ellipse([cx - R, cy - R, cx + R, cy + R],
                  outline=(*CYAN, 220), width=lw)

        # Equalizer spike ring.
        r_in = R * 0.80
        for i, v in enumerate(_spikes):
            a = 2 * math.pi * i / len(_spikes) - math.pi / 2
            r_out = r_in + R * 0.13 * v
            d.line([cx + r_in * math.cos(a), cy + r_in * math.sin(a),
                    cx + r_out * math.cos(a), cy + r_out * math.sin(a)],
                   fill=(*DIM, 200), width=max(1, S // 90))

        # Waveform ribbon + its dim mirrored echo.
        pts = []
        for i in range(361):
            a = 2 * math.pi * i / 360 - math.pi / 2
            r = _wave_r(a, R)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        d.line(pts, fill=(*CYAN, 255), width=max(2, S // 30),
               joint="curve")
        pts2 = []
        for i in range(361):
            a = 2 * math.pi * i / 360 - math.pi / 2
            r = R * 0.56 - (_wave_r(a, R) - R * 0.56) * 0.55 - R * 0.045
            pts2.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        d.line(pts2, fill=(*DIM, 170), width=max(1, S // 60),
               joint="curve")

        # Comet heads riding the ribbon.
        glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
        gd = PIL.ImageDraw.Draw(glow_layer)
        for frac in (0.08, 0.44, 0.75):
            a = 2 * math.pi * frac - math.pi / 2
            r = _wave_r(a, R)
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            gr = R * 0.075
            gd.ellipse([x - gr, y - gr, x + gr, y + gr],
                       fill=(*CYAN, 120))
        glow_layer = glow_layer.filter(
            PIL.ImageFilter.GaussianBlur(S // 22))
        img = PIL.Image.alpha_composite(img, glow_layer)
        d = PIL.ImageDraw.Draw(img)
        for frac in (0.08, 0.44, 0.75):
            a = 2 * math.pi * frac - math.pi / 2
            r = _wave_r(a, R)
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            cr = max(1.5, R * 0.028)
            d.ellipse([x - cr, y - cr, x + cr, y + cr],
                      fill=(*BRIGHT, 255))

        # Core glow, inner ring, bright core.
        glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
        gd = PIL.ImageDraw.Draw(glow_layer)
        Rc = int(R * 0.13)
        gd.ellipse([cx - Rc * 2, cy - Rc * 2, cx + Rc * 2, cy + Rc * 2],
                   fill=(*CYAN, 110))
        glow_layer = glow_layer.filter(
            PIL.ImageFilter.GaussianBlur(S // 14))
        img = PIL.Image.alpha_composite(img, glow_layer)
        d = PIL.ImageDraw.Draw(img)
        Ri = int(R * 0.20)
        d.ellipse([cx - Ri, cy - Ri, cx + Ri, cy + Ri],
                  outline=(*CYAN, 255), width=max(2, lw))
        d.ellipse([cx - Rc, cy - Rc, cx + Rc, cy + Rc],
                  fill=(*WHITE, 255))

        # Downscale to target size.
        return img.resize((sz, sz), PIL.Image.LANCZOS)

    try:
        sizes = [256, 128, 64, 48, 32, 16]
        frames = [_render(s) for s in sizes]
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(
            out,
            format="ICO",
            append_images=frames[1:],
            sizes=[(s, s) for s in sizes],
        )
        return True
    except Exception:
        return False
