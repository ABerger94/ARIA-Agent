"""Light palettes must not wash out the Activity tab (ChatGPT x
ARIA loop pass 25, pass 22 runner-up).

ACTIVITY_COLORS are fixed pale tints; the text-area background
follows the palette tokens, so on Apple Modern (#f5f5f7) and
Earthy Minimal (#f8f6ee) an error body of #ffd9d9 was nearly
invisible. activity_colors now remaps the message bodies for
light palettes: the severity colour mixed toward the palette's
own text colour — dark, still severity-tinted. Level tags keep
their saturated colours; dark palettes are byte-identical.

Run per-file (own process), like every suite in this repo.
Qt is not needed: dashboard.py imports no Qt at module level.
"""
import os
import sys
import types

pkg = types.ModuleType("aria")
pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "aria")]
sys.modules["aria"] = pkg

from aria.dashboard import (  # noqa: E402
    ACTIVITY_COLORS, _hex_is_light, activity_colors, activity_html)

EVENTS = [('10:01:00', 'error', 'Boom', {}),
          ('10:01:01', 'warn', 'Careful', {}),
          ('10:01:02', 'ok', 'Done', {}),
          ('10:01:03', 'info', 'Routine', {})]
PALE = ('#ffd9d9', '#ffedc9', '#d3ffec', '#c2d2de')


def test_dark_palette_keeps_pale_tints():
    assert activity_colors("midnight_neon") == ACTIVITY_COLORS
    html = activity_html(EVENTS, palette_id="midnight_neon")
    for tint in PALE:
        assert tint in html, tint


def test_light_palettes_drop_pale_tints_for_dark_bodies():
    for pid in ("apple_modern", "earthy_minimal"):
        colors = activity_colors(pid)
        assert set(colors) == set(ACTIVITY_COLORS)
        # Level tags unchanged (bold, saturated); bodies dark.
        for kind, (lvl, msg) in colors.items():
            assert lvl == ACTIVITY_COLORS[kind][0], (pid, kind)
            assert msg != ACTIVITY_COLORS[kind][1], (pid, kind)
            assert not _hex_is_light(msg), (pid, kind, msg)
        # Severity stays distinct: four different body colours.
        bodies = [msg for _lvl, msg in colors.values()]
        assert len(set(bodies)) == 4, (pid, bodies)
        html = activity_html(EVENTS, palette_id=pid)
        for tint in PALE:
            assert tint not in html, (pid, tint)


def test_unknown_palette_keeps_dark_defaults():
    assert activity_colors("no-such-palette") == ACTIVITY_COLORS


def test_bare_call_follows_the_set_palette(monkeypatch):
    import aria.ui_prefs as _up
    monkeypatch.setattr(_up, "get_palette", lambda: "apple_modern")
    html = activity_html(EVENTS)
    for tint in PALE:
        assert tint not in html, tint
    monkeypatch.setattr(_up, "get_palette", lambda: "midnight_neon")
    html = activity_html(EVENTS)
    for tint in PALE:
        assert tint in html, tint
