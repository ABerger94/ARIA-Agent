"""Customize panel adapted from Mark-LVI-ARIA's CustomizeOverlay.

Source: https://github.com/ABerger94/Mark-LVI-ARIA/blob/61a47b0/ui.py
Original MARK LVI: Copyright FatihMakes; ARIA fork modifications: Alek Berger.
Adapted panel retains CC BY-NC 4.0 terms. See LICENSES/Mark-LVI.txt.
Changes: colour only — the source's overlay also edits the assistant
and user names, which in ARIA-AGENT belong to the persona system, not
a theme dialog. Application is injected (on_preview / on_commit) so
the dashboard owns what an accent touches: dragging previews the
painted surfaces live; Apply persists (aria/ui_prefs.py) and re-themes
the window stylesheet; Cancel restores the starting accent.

Palettes (2026-11-03, Alek's picks — aria/palettes.py): a row of
named palette buttons under the single-colour presets. Clicking one
previews its HERO like any preset pick and marks it pending; Apply
fires on_palette(id) BEFORE on_commit(hero) so the dashboard's
apply path already sees the stored palette (panel tint included).
Touching the wheel or a single-colour preset afterwards clears the
pending palette — a hand-tuned colour is a custom accent, not a
palette. Reset restores the DEFAULT palette (Midnight Neon).
"""
from __future__ import annotations

PRESETS = [
    ('Cyan', '#00d4ff'),
    ('Teal', '#54d9e8'),
    ('Green', '#00ff88'),
    ('Amber', '#ffcc00'),
    ('Magenta', '#ff3366'),
    ('Violet', '#8b5cf6'),
]


def _palette_button(palette, on_click):
    """One palette button: the name over a gradient strip of the
    palette's own swatches, so the card's colours are what he
    sees before he picks it."""
    from PyQt6.QtWidgets import QPushButton
    from aria import palettes as _palettes
    hexes = _palettes.swatch_hexes(palette)
    stops = ', '.join(
        f'stop:{i / max(1, len(hexes) - 1):.2f} {h}'
        for i, h in enumerate(hexes))
    btn = QPushButton(palette['name'])
    btn.setStyleSheet(
        'QPushButton { background: qlineargradient('
        f'x1:0, y1:0, x2:1, y2:0, {stops}); '
        'color: #ffffff; font-weight: bold; '
        'border: 1px solid rgba(255,255,255,0.35); '
        'border-radius: 4px; padding: 6px 4px; }')
    btn.clicked.connect(lambda checked=False: on_click())
    return btn


def _add_palette_row(lay, pending, pick_palette):
    """The 'PALETTES' label + one button per palette.
    pick_palette(palette) handles the click (wheel + preview +
    pending bookkeeping lives with the builder)."""
    from PyQt6.QtWidgets import QHBoxLayout, QLabel
    from aria import palettes as _palettes
    lay.addWidget(QLabel('PALETTES — full colour sets '
                         '(panels tint from the Primary)'))
    row = QHBoxLayout()
    for palette in _palettes.PALETTES:
        row.addWidget(_palette_button(
            palette, lambda p=palette: pick_palette(p)))
    lay.addLayout(row)


def build_customize_dialog(parent, current_accent, on_preview, on_commit,
                           on_palette=None):
    """The Customize dialog (not exec'd — the caller decides).

    on_preview(hex) fires while picking (live, unpersisted);
    on_commit(hex) fires on Apply; on_palette(id), when given,
    fires on a palette Apply (before on_commit) and on Reset
    (with the default palette id). Closing without Apply
    restores the starting accent via on_preview.
    """
    from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                                 QVBoxLayout)
    from aria import palettes as _palettes
    from aria.dashboard import STYLE
    from aria.hue_wheel import DEFAULT_ACCENT, HueWheel

    start = current_accent or DEFAULT_ACCENT
    pending = {'palette': None}
    dlg = QDialog(parent)
    dlg.setWindowTitle('Customize')
    dlg.setStyleSheet(
        parent.styleSheet() if parent is not None and parent.styleSheet()
        else STYLE)
    lay = QVBoxLayout(dlg)

    title = QLabel('CUSTOMIZE — UI ACCENT')
    title.setObjectName('title')
    lay.addWidget(title)
    lay.addWidget(QLabel(
        'One accent re-derives the whole colour family (hue shift — '
        'brightness and contrast ratios stay as designed).'))

    wheel = HueWheel(start)
    lay.addWidget(wheel)
    hex_label = QLabel(wheel.color())
    lay.addWidget(hex_label)

    def picked(hex_str):
        pending['palette'] = None
        hex_label.setText(hex_str)
        on_preview(hex_str)

    wheel.hue_picked.connect(picked)

    presets = QHBoxLayout()
    for name, hex_value in PRESETS:
        btn = QPushButton(name)
        btn.clicked.connect(
            lambda checked=False, h=hex_value: (wheel.set_color(h),
                                                picked(h)))
        presets.addWidget(btn)
    lay.addLayout(presets)

    def pick_palette(palette):
        wheel.set_color(palette['hero'])
        hex_label.setText(palette['hero'])
        on_preview(palette['hero'])
        pending['palette'] = palette['id']

    _add_palette_row(lay, pending, pick_palette)

    def apply_now():
        if pending['palette']:
            # Commit the palette's hero EXACTLY — the wheel
            # quantizes to a pure hue, which would drift a
            # palette colour (#3ba7f2 -> #0096ff).
            if on_palette is not None:
                on_palette(pending['palette'])
            on_commit(_palettes.hero_of(pending['palette']))
        else:
            on_commit(wheel.color())
        dlg.accept()

    def reset_now():
        # Reset = the default palette (Midnight Neon, Alek
        # 2026-11-03), not the old bare-cyan default.
        if on_palette is not None:
            on_palette(_palettes.DEFAULT_PALETTE_ID)
        on_commit(_palettes.DEFAULT_ACCENT)
        dlg.accept()

    row = QHBoxLayout()
    apply_btn = QPushButton('Apply')
    apply_btn.setObjectName('primary')
    apply_btn.clicked.connect(apply_now)
    row.addWidget(apply_btn)
    reset_btn = QPushButton('Reset to default')
    reset_btn.clicked.connect(reset_now)
    row.addWidget(reset_btn)
    cancel_btn = QPushButton('Cancel')
    cancel_btn.clicked.connect(lambda: (on_preview(current_accent),
                                        dlg.reject()))
    row.addWidget(cancel_btn)
    lay.addLayout(row)

    dlg._wheel = wheel
    dlg._hex_label = hex_label
    dlg._pending_palette = pending
    return dlg


def build_customize_widget(parent, current_accent, on_preview, on_commit,
                           on_close, on_palette=None):
    """The Customize controls as a plain widget for the
    dashboard's in-window overlay (Mark-LVI floated its Customize
    OVER the HUD; the Settings dialog above remains for the
    Settings window). Same contract as the dialog: preview while
    picking, commit on Apply/Reset, restore on Cancel — closing
    goes through on_close instead of accept/reject."""
    from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton,
                                 QVBoxLayout, QWidget)
    from aria import palettes as _palettes
    from aria.hue_wheel import DEFAULT_ACCENT, HueWheel

    start = current_accent or DEFAULT_ACCENT
    pending = {'palette': None}
    w = QWidget(parent)
    lay = QVBoxLayout(w)
    lay.addWidget(QLabel(
        'One accent re-derives the whole colour family (hue shift — '
        'brightness and contrast ratios stay as designed).'))
    wheel = HueWheel(start)
    lay.addWidget(wheel)
    hex_label = QLabel(wheel.color())
    lay.addWidget(hex_label)

    def picked(hex_str):
        pending['palette'] = None
        hex_label.setText(hex_str)
        on_preview(hex_str)

    wheel.hue_picked.connect(picked)
    presets = QHBoxLayout()
    for name, hex_value in PRESETS:
        btn = QPushButton(name)
        btn.clicked.connect(
            lambda checked=False, h=hex_value: (wheel.set_color(h),
                                                picked(h)))
        presets.addWidget(btn)
    lay.addLayout(presets)

    def pick_palette(palette):
        wheel.set_color(palette['hero'])
        hex_label.setText(palette['hero'])
        on_preview(palette['hero'])
        pending['palette'] = palette['id']

    _add_palette_row(lay, pending, pick_palette)

    def apply_now():
        if pending['palette']:
            if on_palette is not None:
                on_palette(pending['palette'])
            on_commit(_palettes.hero_of(pending['palette']))
        else:
            on_commit(wheel.color())
        on_close()

    def reset_now():
        if on_palette is not None:
            on_palette(_palettes.DEFAULT_PALETTE_ID)
        on_commit(_palettes.DEFAULT_ACCENT)
        on_close()

    row = QHBoxLayout()
    apply_btn = QPushButton('Apply')
    apply_btn.setObjectName('primary')
    apply_btn.clicked.connect(apply_now)
    row.addWidget(apply_btn)
    reset_btn = QPushButton('Reset to default')
    reset_btn.clicked.connect(reset_now)
    row.addWidget(reset_btn)
    cancel_btn = QPushButton('Cancel')
    cancel_btn.clicked.connect(lambda: (on_preview(current_accent),
                                        on_close()))
    row.addWidget(cancel_btn)
    lay.addLayout(row)
    w._wheel = wheel
    w._hex_label = hex_label
    w._pending_palette = pending
    return w
