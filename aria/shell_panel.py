"""Shell Home — the ARIA OS home screen panel (2026-10-30).

The front door of Shell Mode, restyled in V2 to feel like an OS
home rather than a settings page: a clock + greeting header with
ARIA's orb beside it, a **tile grid** of his real Start Menu apps
(icon + name), a right rail of live cards — **Briefing**,
**Now Playing**, **Status** — fed only by real seams already in
the codebase (``aria.briefing.latest_briefing``,
``aria.smtc.now_playing_any``, ``aria.config`` /
``aria.approval``), and the command bar as a bottom dock pill.
Type (or later, say) what you want in the dock; an unambiguous
app name launches it, everything else goes to ARIA as a normal
turn through the dashboard's existing submit path, approval
gates and all. The **Exit to Windows** button is always visible,
bottom right: one click and the shell steps aside — Shell Mode
never replaces explorer.exe, so Windows was never gone, just
covered.

Cards never fabricate: a seam that has nothing (no briefing
composed yet, no media session, an unreadable setting) yields
an honest empty state or an omitted line, via the pure
formatters in aria/shell_home.py.

All routing logic lives in aria/shell_home.py and enumeration in
aria/app_launcher.py (both pure); this file is only the paint.
Callbacks are injected: on_launch(app), on_command(text),
on_exit() — the dashboard wires them; tests and screenshots wire
recorders. Card providers are injected the same way and default
to the real seams, lazily and fail-soft.
"""
from __future__ import annotations

import sys
from datetime import datetime

from aria import app_launcher, shell_home

try:
    from PyQt6.QtCore import QFileInfo, QSize, Qt, QTimer
    from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
    from PyQt6.QtWidgets import (
        QCheckBox,
        QFileIconProvider,
        QFrame,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
except Exception:  # pragma: no cover - Qt absent (headless tools)
    QWidget = object

_STYLE = """
QWidget#shellHome { background: #0b1622; }
QLabel#shellClock { color: #eaf6ff; font-size: 56px; font-weight: 600; }
QLabel#shellGreeting { color: #57c7d4; font-size: 22px; }
QLabel#shellDate { color: #8fb8c9; font-size: 14px; }
QLabel#shellCount { color: #8fb8c9; font-size: 12px; }
QLineEdit#shellSearch { background: #101f30; color: #eaf6ff;
            border: 1px solid #1b3042; border-radius: 6px;
            padding: 9px 12px; font-size: 15px; }
QListWidget#shellAppList { background: #0e1c2c; color: #dceef7;
              border: 1px solid #1b3042; border-radius: 10px;
              font-size: 13px; }
QListWidget#shellAppList::item { padding: 8px 4px; }
QListWidget#shellAppList::item:selected { background: #17435a;
              border-radius: 8px; }
QFrame#shellCard { background: #0e1c2c; border: 1px solid #1b3042;
              border-radius: 10px; }
QLabel#shellCardTitle { color: #57c7d4; font-size: 12px;
              font-weight: 700; }
QLabel#shellCardBody { color: #dceef7; font-size: 13px; }
QLabel#shellCardBodyEmpty { color: #8fb8c9; font-size: 13px;
              font-style: italic; }
QFrame#shellDock { background: #101f30; border: 1px solid #2b5873;
              border-radius: 22px; }
QLineEdit#shellCommand { background: transparent; color: #eaf6ff;
            border: none; padding: 10px 6px; font-size: 15px; }
QPushButton#shellExit { background: #12303f; color: #eaf6ff;
    border: 1px solid #2b5873; border-radius: 6px;
    padding: 10px 18px; font-size: 14px; }
QPushButton#shellExit:hover { background: #17435a; }
"""


class OrbDisc(QWidget):
    """ARIA's presence on the home: a simple cyan orb disc drawn
    in Qt (the brief's sanctioned lightweight stand-in until a
    heavier orb widget earns a seam here)."""

    def __init__(self, parent=None, diameter=84):
        super().__init__(parent)
        self._d = diameter
        self.setFixedSize(diameter, diameter)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self._d
        # Outer ring.
        p.setPen(QColor("#2b5873"))
        p.setBrush(Qt.GlobalColor.transparent)
        p.drawEllipse(2, 2, d - 4, d - 4)
        # Glowing core.
        core = int(d * 0.52)
        off = (d - core) // 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#57c7d4"))
        p.drawEllipse(off, off, core, core)
        # Bright heart.
        heart = int(d * 0.22)
        hoff = (d - heart) // 2
        p.setBrush(QColor("#bdf3fa"))
        p.drawEllipse(hoff, hoff, heart, heart)
        p.end()


def _letter_icon(tile, size=48):
    """Paint the fallback letter tile as an icon."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(tile["color"]))
    p.drawRoundedRect(2, 2, size - 4, size - 4, 10, 10)
    p.setPen(QColor("#0b1622"))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.5))
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, tile["letter"])
    p.end()
    return QIcon(pm)


# -- default card providers: the real seams, lazy + fail-soft ---

def _default_briefing():
    try:
        from aria import briefing
        return briefing.latest_briefing()
    except Exception:
        return None


def _default_now_playing():
    try:
        from aria import smtc
        return smtc.now_playing_any()
    except Exception:
        return None


def _default_status():
    out = {}
    try:
        from aria import config
        safe = config.get_setting("safe_mode", None)
        if safe is not None:
            out["safe_mode"] = bool(safe)
        voice = config.get_setting("voice_enabled", None)
        if voice is not None:
            out["voice_enabled"] = bool(voice)
    except Exception:
        pass
    try:
        from aria import approval
        out["approval_mode"] = approval.get_mode()
    except Exception:
        pass
    return out or None


class ShellPanel(QWidget):
    """The Shell Home screen. See the module docstring."""

    def __init__(self, parent=None, apps=None, apps_provider=None,
                 on_launch=None, on_command=None, on_exit=None,
                 now_provider=None, shell_mode=None,
                 on_shell_mode=None, briefing_provider=None,
                 now_playing_provider=None, status_provider=None):
        super().__init__(parent)
        self.setObjectName("shellHome")
        self.setStyleSheet(_STYLE)
        self._apps_provider = apps_provider
        self._apps = list(apps) if apps is not None else []
        self._on_launch = on_launch
        self._on_command = on_command
        self._on_exit = on_exit
        self._on_shell_mode = on_shell_mode
        self._now_provider = now_provider or datetime.now
        self._briefing_provider = briefing_provider or _default_briefing
        self._now_playing_provider = (now_playing_provider
                                      or _default_now_playing)
        self._status_provider = status_provider or _default_status
        self._icon_provider = None

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 24)
        root.setSpacing(14)

        # -- header: orb + clock/greeting/date ------------------
        header = QHBoxLayout()
        self.orb = OrbDisc(self, diameter=84)
        header.addWidget(self.orb)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.clock_label = QLabel("--:--")
        self.clock_label.setObjectName("shellClock")
        titles.addWidget(self.clock_label)
        self.greeting_label = QLabel("")
        self.greeting_label.setObjectName("shellGreeting")
        titles.addWidget(self.greeting_label)
        self.date_label = QLabel("")
        self.date_label.setObjectName("shellDate")
        titles.addWidget(self.date_label)
        header.addLayout(titles)
        header.addStretch(1)
        root.addLayout(header)

        # -- main: apps column + right rail ----------------------
        main = QHBoxLayout()
        main.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps…")
        self.search.setObjectName("shellSearch")
        self.search.textChanged.connect(self._refilter)
        left.addWidget(self.search)

        self.app_list = QListWidget()
        self.app_list.setObjectName("shellAppList")
        self.app_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.app_list.setIconSize(QSize(48, 48))
        self.app_list.setGridSize(QSize(136, 108))
        self.app_list.setWordWrap(True)
        self.app_list.setResizeMode(
            QListWidget.ResizeMode.Adjust)
        self.app_list.setMovement(QListWidget.Movement.Static)
        self.app_list.setUniformItemSizes(True)
        self.app_list.itemActivated.connect(self._activate_item)
        self.app_list.itemDoubleClicked.connect(self._activate_item)
        left.addWidget(self.app_list, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("shellCount")
        left.addWidget(self.count_label)
        main.addLayout(left, 3)

        rail = QVBoxLayout()
        rail.setSpacing(10)
        self.briefing_frame, self.briefing_body = self._make_card(
            "BRIEFING")
        rail.addWidget(self.briefing_frame)
        self.playing_frame, self.playing_body = self._make_card(
            "NOW PLAYING")
        rail.addWidget(self.playing_frame)
        self.status_frame, self.status_body = self._make_card(
            "STATUS")
        rail.addWidget(self.status_frame)
        rail.addStretch(1)

        if shell_mode is None:
            try:
                from aria import ui_prefs
                shell_mode = ui_prefs.get_shell_mode()
            except Exception:
                shell_mode = False
        self.shell_mode_box = QCheckBox(
            "Start ARIA in Shell Mode (boot straight here)")
        self.shell_mode_box.setObjectName("shellModeBox")
        self.shell_mode_box.setStyleSheet("color: #8fb8c9;")
        self.shell_mode_box.setChecked(bool(shell_mode))
        self.shell_mode_box.toggled.connect(self._shell_mode_toggled)
        rail.addWidget(self.shell_mode_box)
        main.addLayout(rail, 2)
        root.addLayout(main, 1)

        # -- bottom dock: command pill + Exit to Windows ---------
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        self.dock = QFrame()
        self.dock.setObjectName("shellDock")
        dock_lay = QHBoxLayout(self.dock)
        dock_lay.setContentsMargins(16, 2, 16, 2)
        self.dock_orb = OrbDisc(self.dock, diameter=18)
        dock_lay.addWidget(self.dock_orb)
        self.command = QLineEdit()
        self.command.setPlaceholderText(
            "Tell ARIA something, or type an app name to launch it…")
        self.command.setObjectName("shellCommand")
        self.command.returnPressed.connect(self._submit_command)
        dock_lay.addWidget(self.command, 1)
        bottom.addWidget(self.dock, 1)
        self.exit_btn = QPushButton(shell_home.EXIT_LABEL)
        self.exit_btn.setObjectName("shellExit")
        self.exit_btn.setToolTip(
            "Leave Shell Mode and return to the Windows desktop")
        self.exit_btn.clicked.connect(self._exit)
        bottom.addWidget(self.exit_btn)
        root.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(20000)
        if self._apps_provider is not None and apps is None:
            self.refresh_apps()
        self.refresh_clock()
        self.refresh_cards()
        self._refilter()

    # -- construction helpers -----------------------------------
    def _make_card(self, title):
        frame = QFrame()
        frame.setObjectName("shellCard")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(4)
        head = QLabel(title)
        head.setObjectName("shellCardTitle")
        lay.addWidget(head)
        body = QLabel("")
        body.setObjectName("shellCardBody")
        body.setWordWrap(True)
        lay.addWidget(body)
        return frame, body

    def _set_card(self, body_label, card):
        body_label.setText("\n".join(card["lines"]))
        body_label.setObjectName(
            "shellCardBodyEmpty" if card["empty"] else "shellCardBody")
        body_label.style().unpolish(body_label)
        body_label.style().polish(body_label)

    def _icon_for(self, app):
        tile = shell_home.tile_model(app)
        if sys.platform == "win32" and tile["path"]:
            try:
                if self._icon_provider is None:
                    self._icon_provider = QFileIconProvider()
                icon = self._icon_provider.icon(QFileInfo(tile["path"]))
                if icon is not None and not icon.isNull():
                    return icon
            except Exception:
                pass
        return _letter_icon(tile)

    # -- data -------------------------------------------------
    def refresh_apps(self):
        """Re-enumerate through the provider (if any) and repaint."""
        if self._apps_provider is not None:
            try:
                self._apps = list(self._apps_provider() or [])
            except Exception:
                self._apps = []
        self._refilter()

    def refresh_clock(self):
        model = shell_home.home_model(self._apps,
                                      now=self._now_provider())
        self.clock_label.setText(model["clock"])
        self.greeting_label.setText(model["greeting"])
        self.date_label.setText(model["date_line"])
        self.count_label.setText(model["count_line"])

    def refresh_cards(self):
        """Repaint the rail cards from their providers (fail-soft:
        a broken seam reads as that card's empty state)."""
        try:
            report = self._briefing_provider()
        except Exception:
            report = None
        self._set_card(self.briefing_body,
                       shell_home.briefing_card(report))
        try:
            info = self._now_playing_provider()
        except Exception:
            info = None
        self._set_card(self.playing_body,
                       shell_home.now_playing_card(info))
        try:
            status = self._status_provider()
        except Exception:
            status = None
        args = dict(status) if isinstance(status, dict) else {}
        args.setdefault("shell_mode", self.shell_mode_box.isChecked())
        self._set_card(self.status_body, shell_home.status_card(**args))

    def _tick(self):
        self.refresh_clock()
        self.refresh_cards()

    # -- interactions ------------------------------------------
    def _refilter(self):
        query = self.search.text() if hasattr(self, "search") else ""
        results = app_launcher.search_apps(self._apps, query,
                                           limit=200)
        self.app_list.clear()
        for app in results:
            item = QListWidgetItem(self._icon_for(app), app["name"])
            item.setData(Qt.ItemDataRole.UserRole, app)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.app_list.addItem(item)
        self.refresh_clock()

    def _activate_item(self, item):
        app = item.data(Qt.ItemDataRole.UserRole)
        if app is not None:
            self._launch(app)

    def _launch(self, app):
        if self._on_launch is not None:
            self._on_launch(app)
        else:
            app_launcher.launch_app(app)

    def _submit_command(self):
        intent = shell_home.route_command(self.command.text(),
                                          self._apps)
        if intent["kind"] == "launch":
            self.command.clear()
            self._launch(intent["app"])
        elif intent["kind"] == "chat":
            text = intent["text"]
            self.command.clear()
            if self._on_command is not None:
                self._on_command(text)

    def _exit(self):
        if self._on_exit is not None:
            self._on_exit()

    def _shell_mode_toggled(self, enabled):
        self.refresh_cards()
        if self._on_shell_mode is not None:
            self._on_shell_mode(bool(enabled))
            return
        try:
            from aria import ui_prefs
            ui_prefs.set_shell_mode(bool(enabled))
        except Exception:
            pass
