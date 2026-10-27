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


def build_customize_dialog(parent, current_accent, on_preview, on_commit):
    """The Customize dialog (not exec'd — the caller decides).

    on_preview(hex) fires while picking (live, unpersisted);
    on_commit(hex_or_None) fires on Apply (hex) or Reset (None);
    closing without Apply restores the starting accent via on_preview.
    """
    from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                                 QVBoxLayout)
    from aria.dashboard import STYLE
    from aria.hue_wheel import DEFAULT_ACCENT, HueWheel

    start = current_accent or DEFAULT_ACCENT
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

    row = QHBoxLayout()
    apply_btn = QPushButton('Apply')
    apply_btn.setObjectName('primary')
    apply_btn.clicked.connect(lambda: (on_commit(wheel.color()),
                                       dlg.accept()))
    row.addWidget(apply_btn)
    reset_btn = QPushButton('Reset to default')
    reset_btn.clicked.connect(lambda: (on_commit(None), dlg.accept()))
    row.addWidget(reset_btn)
    cancel_btn = QPushButton('Cancel')
    cancel_btn.clicked.connect(lambda: (on_preview(current_accent),
                                        dlg.reject()))
    row.addWidget(cancel_btn)
    lay.addLayout(row)

    dlg._wheel = wheel
    dlg._hex_label = hex_label
    return dlg
