"""Confirm banner adapted from Mark-LVI-ARIA's ConfirmBanner.

Source: https://github.com/ABerger94/Mark-LVI-ARIA/blob/61a47b0/ui.py
Original MARK LVI: Copyright FatihMakes; ARIA fork modifications: Alek Berger.
Adapted rendering methods retain CC BY-NC 4.0 terms. See LICENSES/Mark-LVI.txt.
Changes: isolated widget, ARIA-AGENT Command Center palette/fonts, and the
answer travels to aria/approval.py (via aria/dashboard.py) instead of
core/confirm.py. The banner is the interface asking, and the answer goes
from a human click to the approval store without the model in the loop.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)


class C:
    """Command Center palette (aria/dashboard.py STYLE) + the LVI warning
    accent. The gate keeps its orange: it is not decoration, it is the one
    surface in the window that can authorise an irreversible action."""
    BG = 'rgba(20, 10, 2, 250)'
    ACC = '#ff6b00'
    TEXT = '#dce6ef'
    TEXT_MED = '#829aaf'
    BORDER = '#1b3042'
    BORDER_B = '#54d9e8'
    DARK = '#080d14'
    FONT = 'Consolas'


class _OverlayBase(QWidget):
    """Base for floating panels placed by hand over the window.

    They are children of the window but sit in no layout, so Qt never
    invalidates the region they occupy when they hide: the window keeps
    painting around them and their last frame stays on screen as a ghost.
    Repaint exactly what we were covering, before we stop covering it.
    """

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class ConfirmBanner(_OverlayBase):
    """The gate in front of an action that cannot be taken back.

    Title and detail are shown WHOLE: neither is truncated, ever. Together
    they are the whole text of what is being approved, and a confirmation
    the user cannot fully read is not a confirmation — so the text scrolls,
    the header and buttons stay put, and every line stays reachable.
    Nothing blocks while the banner is up: the assistant keeps talking,
    and the pending approval simply waits (aria/approval.py expires it
    after 10 minutes).
    """

    answered = pyqtSignal(bool)
    _OW = 760

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: {C.BG};
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        # Width follows the window: wide enough to actually read in,
        # never wider than the window itself. Height is capped the same
        # way (see the scroll area below). Legibility pass (Alek,
        # 2026-10-27: "so small and cramped. It's illegible."): the
        # LVI-inherited 600px cap and 9-10pt type are gone — the
        # banner now takes a real share of the window (55%, floored
        # at 680, capped at 980) with 11-13pt type throughout.
        _pw = parent.width() if parent is not None else 0
        _ph = parent.height() if parent is not None else 0
        self.setFixedWidth(min(980, max(680, int(_pw * 0.55)), _pw - 32)
                           if _pw else self._OW)
        if _ph:
            self.setMaximumHeight(max(240, _ph - 24))
        self._parent_h = _ph

        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 20, 26, 20)
        lay.setSpacing(10)

        hdr = QLabel('⚠  CONFIRM')
        hdr.setFont(QFont(C.FONT, 13, QFont.Weight.Bold))
        hdr.setStyleSheet(f'color: {C.ACC}; background: transparent;')
        lay.addWidget(hdr)

        body = QWidget()
        body.setStyleSheet('background: transparent;')
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 8, 0)
        body_lay.setSpacing(8)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont(C.FONT, 12, QFont.Weight.Bold))
        ttl.setStyleSheet(f'color: {C.TEXT}; background: transparent;')
        self._title_label = ttl
        body_lay.addWidget(ttl)

        self._detail_label = None
        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont(C.FONT, 11))
            # The detail IS the thing being approved (often a
            # command) — it renders in the full text colour, not
            # the dimmed meta colour (legibility pass, 2026-10-27).
            dtl.setStyleSheet(
                f'color: {C.TEXT}; background: transparent;')
            self._detail_label = dtl
            body_lay.addWidget(dtl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            'QScrollArea { background: transparent; }'
            f'QScrollBar:vertical {{ background: {C.DARK}; width: 9px; '
            f'margin: 0; border-radius: 4px; }}'
            f'QScrollBar::handle:vertical {{ background: {C.BORDER_B}; '
            f'border-radius: 4px; min-height: 30px; }}'
            'QScrollBar::add-line, QScrollBar::sub-line { height: 0; }'
            'QScrollBar::add-page, QScrollBar::sub-page '
            '{ background: none; }')
        scroll.setWidget(body)
        _cap = (self._parent_h - 210) if self._parent_h else 420
        scroll.setMaximumHeight(max(140, min(640, _cap)))
        self._detail_scroll = scroll
        lay.addWidget(scroll, 1)

        row = QHBoxLayout()
        row.setSpacing(10)

        yes = QPushButton('▸  CONFIRM')
        yes.setFixedHeight(42)
        yes.setMinimumWidth(150)
        yes.setFont(QFont(C.FONT, 11, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        self._confirm_button = yes
        row.addWidget(yes)

        no = QPushButton('CANCEL')
        no.setFixedHeight(42)
        no.setMinimumWidth(150)
        no.setFont(QFont(C.FONT, 11))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        self._cancel_button = no
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading,
        # the safe answer wins.
        no.setDefault(True)
        no.setFocus()
        self.adjustSize()
