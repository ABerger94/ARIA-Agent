"""Named UI colour palettes (Alek, 2026-11-03).

The five palettes come from a set of UI palette cards Alek
picked for ARIA — hexes transcribed exactly from the cards
he sent (source: @amira_uiuxdesign palette carousel). A
palette is a set of ROLE colours; ARIA's theming engine
(aria/hue_wheel.shift_palette, via dashboard.style_for_accent)
derives the whole family from ONE accent, so each palette
also names a HERO — the colour that drives the family — and
a TONE2 second tone. The palette's PRIMARY additionally
tints the window's dark panel backgrounds
(dashboard.style_for_palette), so a palette is more than a
single accent pick: Ocean Breeze's panels go deep navy,
Earthy Minimal's deep green-charcoal.

Persistence lives in aria/ui_prefs (the palette id and its
hero written together, so every existing accent consumer —
chat colours, Shell Home, the painted widgets — follows a
palette pick without knowing palettes exist).

Midnight Neon is the DEFAULT palette (Alek's pick,
2026-11-03): what Reset restores and what a fresh install
resolves to when nothing is set.
"""
from __future__ import annotations

import colorsys

#: Card order preserved. Roles a card did not name are None —
#: the transcription stays faithful to the cards; hero/tone2
#: are ARIA's mapping ONTO the cards (hero = the palette's
#: Accent where the card names one, else its vivid Secondary;
#: tone2 = the supporting tone).
PALETTES = [
    {"id": "apple_modern", "name": "Apple Modern",
     "primary": "#1d1d1f", "secondary": None,
     "tertiary": "#aaaaaa", "accent": "#007aff",
     "background": "#f5f5f7",
     "hero": "#007aff", "tone2": "#aaaaaa"},
    {"id": "vibrant_sunset", "name": "Vibrant Sunset",
     "primary": "#4d3a4d", "secondary": "#be5ca9",
     "tertiary": "#d59cc5", "accent": None,
     "background": "#eadada",
     "hero": "#be5ca9", "tone2": "#d59cc5"},
    {"id": "ocean_breeze", "name": "Ocean Breeze",
     "primary": "#0b3d91", "secondary": "#3ba7f2",
     "tertiary": "#7fe7d6", "accent": None,
     "background": "#e8f6ff",
     "hero": "#3ba7f2", "tone2": "#7fe7d6"},
    {"id": "earthy_minimal", "name": "Earthy Minimal",
     "primary": "#2e3a2f", "secondary": "#6b7f5b",
     "tertiary": "#d9c9b2", "accent": "#c96f4f",
     "background": "#f8f6ee",
     "hero": "#c96f4f", "tone2": "#6b7f5b"},
    {"id": "midnight_neon", "name": "Midnight Neon",
     "primary": "#0f172a", "secondary": "#7c3aed",
     "tertiary": "#a78bfa", "accent": "#22d3ee",
     "background": "#ede9fe",
     "hero": "#22d3ee", "tone2": "#7c3aed"},
]

#: Alek's pick (2026-11-03): Midnight Neon is the default.
DEFAULT_PALETTE_ID = "midnight_neon"

_BY_ID = {p["id"]: p for p in PALETTES}

#: The accent a fresh install / reset resolves to.
DEFAULT_ACCENT = _BY_ID[DEFAULT_PALETTE_ID]["hero"]

#: Role order for swatch strips (dark to light reads naturally).
_ROLE_ORDER = ("primary", "secondary", "tertiary", "accent",
               "background")


def get_palette(palette_id):
    """The palette dict for *palette_id*, or None when the id
    is unknown. Returns a copy — callers may not mutate the
    registry."""
    pal = _BY_ID.get(palette_id)
    return dict(pal) if pal is not None else None


def is_valid_palette_id(palette_id) -> bool:
    return palette_id in _BY_ID


def palette_ids():
    return [p["id"] for p in PALETTES]


def hero_of(palette_id):
    """The palette's hero (family-driving) hex, or None."""
    pal = _BY_ID.get(palette_id)
    return pal["hero"] if pal is not None else None


def swatch_hexes(palette):
    """The palette's role hexes in strip order, skipping the
    roles its card did not name (None)."""
    return [palette[role] for role in _ROLE_ORDER
            if palette.get(role)]


def panel_shade(primary_hex, lightness):
    """A panel background derived from a palette PRIMARY: the
    primary's hue and saturation at the given HLS lightness
    (0..1) — the designed panel lightnesses stay, the hue
    family becomes the palette's. Returns '#rrggbb'."""
    r = int(primary_hex[1:3], 16) / 255
    g = int(primary_hex[3:5], 16) / 255
    b = int(primary_hex[5:7], 16) / 255
    h, _l, s = colorsys.rgb_to_hls(r, g, b)
    r2, g2, b2 = colorsys.hls_to_rgb(h, lightness, s)
    return "#%02x%02x%02x" % (round(r2 * 255), round(g2 * 255),
                              round(b2 * 255))
