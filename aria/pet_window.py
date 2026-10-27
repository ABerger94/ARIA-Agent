# Pet window adapted from Mark-LVI-ARIA's PetWindow (ui.py).
# Source: https://github.com/ABerger94/Mark-LVI-ARIA/blob/61a47b0/ui.py
# Original MARK LVI: Copyright FatihMakes; ARIA fork modifications: Alek Berger.
# Licensed under CC BY-NC 4.0; see LICENSES/Mark-LVI.txt and LICENSES/NOTICE.md.
# Changes: the source's PetCanvas (a miniature of its HUD canvas) is not
# ported — ARIA-Agent already has its centerpieces as widgets, so the
# pet hosts the ResonanceOrb or the HoloFace directly. The window
# behaviours are the source's: frameless / on-top / no-focus, click
# raises the main window, double-click exits pet mode, drag parks her
# anywhere and the spot persists.
"""Desktop pet: a small floating centerpiece window (Port 7, last).

The pet is a companion to the Command Center, not a replacement for
it: it floats frameless and always-on-top, never steals keyboard
focus, and never quits the app when closed. A QTimer animates the
hosted centerpiece ONLY while the pet is visible, fed by a provider
callable returning (state, level, muted) — the same triple the
dashboard's render loop computes — so the pet shows what ARIA is
doing (idle / listening / speaking) without owning any audio state.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMenu, QVBoxLayout, QWidget

PET_SIZE = (288, 288)
_PET_MARGIN = 4              # transparent breathing room around the face
# Scale factors chosen so EVERY painted element fits the 280px
# face slot: the orb's widest transient (emission rings reach
# 1.27x the core radius) peaks at 0.35 * 1.27 = 0.44 of the face —
# inside the half-width with margin; the holo aura spans 1.95x
# its head radius, so 0.23 keeps the aura inside too. At the old
# 240x260 the faces' 280px minimum silently overflowed the window
# (anchored top-left), which is what cut the orb's right side.
_PET_ORB_SCALE = 0.35
_PET_HOLO_SCALE = 0.23
_DRAG_THRESHOLD = 4          # px of movement before a press is a drag


def clamp_pet_pos(x, y, w, h, screen_w, screen_h):
    """Keep the whole pet on screen (the source's clamp, verbatim
    semantics): at least the full window inside the available area."""
    x = max(0, min(int(x), max(0, screen_w - w)))
    y = max(0, min(int(y), max(0, screen_h - h)))
    return x, y


class PetWindow(QWidget):
    """Floating mini centerpiece. provider() -> (state, level, muted);
    on_raise() brings the main window forward; on_exit() leaves pet
    mode (defaults to on_raise)."""

    def __init__(self, parent=None, provider=None, on_raise=None,
                 on_exit=None, form=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,
                          True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        # Translucent: the desktop shows through everywhere the
        # centerpiece itself does not paint. The faces are created
        # transparent-aware for the same reason (their painters
        # otherwise fill an opaque HUD background).
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,
                          True)
        self.setAutoFillBackground(False)
        self.setStyleSheet('background: transparent;')
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(*PET_SIZE)
        self._provider = provider or (lambda: ('idle', 0.0, False))
        self._on_raise = on_raise or (lambda: None)
        self._on_exit = on_exit or self._on_raise
        self._press_pos = None
        self._dragging = False
        self._menu = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(_PET_MARGIN, _PET_MARGIN,
                               _PET_MARGIN, _PET_MARGIN)
        lay.setSpacing(0)
        self._face = None
        self._form = None
        self.set_form(form or self._saved_form())
        self._timer = QTimer(self)
        self._timer.setInterval(33)          # ~30 fps while visible
        self._timer.timeout.connect(self._tick)

    # ── form (which centerpiece lives in the pet) ───────────────────
    @staticmethod
    def _saved_form():
        try:
            from aria import ui_prefs
            return ui_prefs.get_centerpiece()
        except Exception:
            return 'orb'

    def set_form(self, form):
        """Swap the hosted centerpiece: 'holo' (character) or 'orb'."""
        form = 'holo' if form == 'holo' else 'orb'
        if form == self._form and self._face is not None:
            return
        if self._face is not None:
            self.layout().removeWidget(self._face)
            self._face.setParent(None)
            self._face.deleteLater()
        if form == 'holo':
            try:
                from aria.holo_face import HoloFace
                self._face = HoloFace(
                    self, transparent_background=True,
                    radius_scale=_PET_HOLO_SCALE)
            except Exception:
                from aria.resonance import ResonanceOrb
                self._face = ResonanceOrb(
                    self, transparent_background=True,
                    core_scale=_PET_ORB_SCALE)
                form = 'orb'
        else:
            from aria.resonance import ResonanceOrb
            self._face = ResonanceOrb(
                self, transparent_background=True,
                core_scale=_PET_ORB_SCALE)
        self._form = form
        self.layout().addWidget(self._face)

    @property
    def form(self):
        return self._form

    @property
    def face(self):
        return self._face

    # ── showing / parking ───────────────────────────────────────────
    def show_at_saved_pos(self):
        pos = None
        try:
            from aria import ui_prefs
            pos = ui_prefs.get_pet_pos()
        except Exception:
            pos = None
        scr = QApplication.primaryScreen()
        geo = scr.availableGeometry() if scr is not None else None
        if geo is not None:
            if pos is None:
                x = geo.width() - self.width() - 24
                y = geo.height() - self.height() - 24
            else:
                x, y = pos[0] - geo.x(), pos[1] - geo.y()
            x, y = clamp_pet_pos(x, y, self.width(), self.height(),
                                 geo.width(), geo.height())
            self.move(geo.x() + int(x), geo.y() + int(y))
        elif pos is not None:
            self.move(int(pos[0]), int(pos[1]))
        self.show()

    def _save_pos(self):
        try:
            from aria import ui_prefs
            ui_prefs.set_pet_pos([self.x(), self.y()])
        except Exception:
            pass

    def showEvent(self, e):
        super().showEvent(e)
        self._timer.start()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    # ── animation ───────────────────────────────────────────────────
    def _tick(self):
        if self._face is None:
            return
        try:
            state, level, muted = self._provider()
        except Exception:
            state, level, muted = 'idle', 0.0, False
        self._face.advance(state, level, muted)

    # ── interactions ────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_pos = e.globalPosition().toPoint()
            self._dragging = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press_pos is not None:
            delta = e.globalPosition().toPoint() - self._press_pos
            if (not self._dragging
                    and (abs(delta.x()) + abs(delta.y())) > _DRAG_THRESHOLD):
                self._dragging = True
            if self._dragging:
                self.move(self.pos() + delta)
                self._press_pos = e.globalPosition().toPoint()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            was_drag = self._dragging
            self._press_pos = None
            self._dragging = False
            if was_drag:
                self._save_pos()
            else:
                # Plain click: raise the main window; the pet stays.
                self._on_raise()
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        # Double click exits pet mode: pet hides, main window returns.
        self.hide()
        self._on_exit()
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        self._menu = menu
        act_open = menu.addAction('Open ARIA')
        act_open.triggered.connect(self._on_raise)
        self._act_form_holo = menu.addAction('Pet form: Character')
        self._act_form_holo.triggered.connect(lambda: self.set_form('holo'))
        self._act_form_orb = menu.addAction('Pet form: Waveform orb')
        self._act_form_orb.triggered.connect(lambda: self.set_form('orb'))
        act_hide = menu.addAction('Hide pet')
        act_hide.triggered.connect(self.hide)
        menu.exec(e.globalPos())
