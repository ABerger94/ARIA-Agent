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

FULL TOKENS (Alek, 2026-11-04): the hero-derived family read
as "one blanket colour" to Alek — a palette is not a full
palette if only the accent changes. Each palette now ALSO
carries a complete token set (bg / surface / elevated /
border / text / dim / accent / accent2 / on_accent), designed
as dashboard mockups and approved by Alek as-is before the
build. dashboard.style_for_palette consumes the tokens
DIRECTLY — no hue derivation — so every surface carries the
palette, and the light palettes (Earthy Minimal, Apple
Modern) genuinely flip the window light. Token values are
anchored on the cards: accent == hero everywhere; accent2 ==
tone2 except Apple Modern, whose mockup gradient runs to
its near-black Primary instead of the grey tone2.

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
     "hero": "#007aff", "tone2": "#aaaaaa",
     "tokens": {"bg": "#f5f5f7", "surface": "#ffffff",
                "elevated": "#ececf0", "border": "#d9d9e0",
                "text": "#1d1d1f", "dim": "#7a7a80",
                "accent": "#007aff", "accent2": "#1d1d1f",
                "on_accent": "#ffffff"}},
    {"id": "vibrant_sunset", "name": "Vibrant Sunset",
     "primary": "#4d3a4d", "secondary": "#be5ca9",
     "tertiary": "#d59cc5", "accent": None,
     "background": "#eadada",
     "hero": "#be5ca9", "tone2": "#d59cc5",
     "tokens": {"bg": "#382a38", "surface": "#4d3a4d",
                "elevated": "#5e485e", "border": "#715671",
                "text": "#eadada", "dim": "#d59cc5",
                "accent": "#be5ca9", "accent2": "#d59cc5",
                "on_accent": "#ffffff"}},
    {"id": "ocean_breeze", "name": "Ocean Breeze",
     "primary": "#0b3d91", "secondary": "#3ba7f2",
     "tertiary": "#7fe7d6", "accent": None,
     "background": "#e8f6ff",
     "hero": "#3ba7f2", "tone2": "#7fe7d6",
     "tokens": {"bg": "#07295f", "surface": "#0b3d91",
                "elevated": "#114fa8", "border": "#1d63b8",
                "text": "#e8f6ff", "dim": "#9ccbf5",
                "accent": "#3ba7f2", "accent2": "#7fe7d6",
                "on_accent": "#06263f"}},
    {"id": "earthy_minimal", "name": "Earthy Minimal",
     "primary": "#2e3a2f", "secondary": "#6b7f5b",
     "tertiary": "#d9c9b2", "accent": "#c96f4f",
     "background": "#f8f6ee",
     "hero": "#c96f4f", "tone2": "#6b7f5b",
     "tokens": {"bg": "#f8f6ee", "surface": "#ffffff",
                "elevated": "#f1ebdb", "border": "#d9c9b2",
                "text": "#2e3a2f", "dim": "#6b7f5b",
                "accent": "#c96f4f", "accent2": "#6b7f5b",
                "on_accent": "#ffffff"}},
    {"id": "midnight_neon", "name": "Midnight Neon",
     "primary": "#0f172a", "secondary": "#7c3aed",
     "tertiary": "#a78bfa", "accent": "#22d3ee",
     "background": "#ede9fe",
     "hero": "#22d3ee", "tone2": "#7c3aed",
     "tokens": {"bg": "#0f172a", "surface": "#182642",
                "elevated": "#20304f", "border": "#2e4470",
                "text": "#ede9fe", "dim": "#a78bfa",
                "accent": "#22d3ee", "accent2": "#7c3aed",
                "on_accent": "#06202b"}},
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


def tokens_of(palette_id):
    """The palette's full UI token set (2026-11-04), or None.

    Returns a copy — callers may not mutate the registry."""
    pal = _BY_ID.get(palette_id)
    if pal is None or not pal.get("tokens"):
        return None
    return dict(pal["tokens"])


def swatch_hexes(palette):
    """The palette's role hexes in strip order, skipping the
    roles its card did not name (None)."""
    return [palette[role] for role in _ROLE_ORDER
            if palette.get(role)]


def _name_tokens(text):
    """The normalized token set of a palette id / display name
    (or of a free-form query): lowercase, underscores become
    spaces, split on whitespace."""
    return set(str(text or "").lower().replace("_", " ").split())


def resolve_palette_name(query):
    """Resolve a free-form theme request ('earthy mineral',
    'Ocean', 'MIDNIGHT_NEON') to a palette.

    Returns a (status, match) pair:
      ("hit", palette_id)      — one palette, confidently;
      ("ambiguous", [ids...])  — several fit equally; the
                                 caller must ask, not guess;
      ("unknown", [])          — nothing shares a single token.

    Most specific match first: (1) exact match on the
    normalized id or display name; (2) token-subset — every
    query token appears in the palette's id+name tokens;
    (3) score fallback — count the query tokens present in
    each palette's token set; a UNIQUE palette with the
    highest score >= 1 wins, a tie is ambiguous. The score
    step is what rescues near-miss names: 'earthy mineral'
    shares 'earthy' with Earthy Minimal alone (score 1 vs 0
    everywhere else), so the wrong second word cannot sink
    the match — while pure gibberish overlaps nothing and
    stays unknown."""
    q = " ".join(str(query or "").lower().replace("_", " ").split())
    if not q:
        return ("unknown", [])
    qtokens = set(q.split())
    token_sets = {}
    for pal in PALETTES:
        token_sets[pal["id"]] = _name_tokens(pal["id"]) \
            | _name_tokens(pal["name"])
        if q == pal["id"].replace("_", " ") \
                or q == pal["name"].lower():
            return ("hit", pal["id"])
    subset = [pid for pid, toks in token_sets.items()
              if qtokens <= toks]
    if len(subset) == 1:
        return ("hit", subset[0])
    if len(subset) > 1:
        return ("ambiguous", subset)
    scores = {pid: len(qtokens & toks)
              for pid, toks in token_sets.items()}
    best = max(scores.values())
    if best < 1:
        return ("unknown", [])
    winners = [pid for pid, s in scores.items() if s == best]
    if len(winners) == 1:
        return ("hit", winners[0])
    return ("ambiguous", winners)


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
