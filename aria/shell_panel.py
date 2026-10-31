"""Shell Home — the ARIA OS home screen panel (2026-10-30).

The front door of Shell Mode: a clock + greeting header with
ARIA's orb beside it, a **tile grid** of his real Start Menu
apps (icon + name), a right rail, and the command bar as a
bottom dock pill.

V3 (2026-10-30, Alek's witnessed verdict + approved redesign):
the dashboard gives Shell Home the FULL window (both side
columns step aside) and the panel paints at reading size; V3
also fixed the witnessed double-launch (one launch signal path
+ a same-app relaunch guard).

V4 (2026-10-30, Alek's direct order after witnessing V3): the
rail held Briefing / Now Playing / Status cards and the
Briefing body still rendered as a short scroll window of
section texts — he ordered the other two cards GONE and the
Briefing card extended to fill the rail, carrying **the same
text as the Workspace Briefing tab**: ``report_text`` of the
latest briefing plus the guarded research-section append,
composed by the pure ``shell_home.briefing_full_text`` helper
and rendered in a read-only text view that fills the card at
12pt-equivalent type. The rail is now: Briefing card + the
nothing else — the Shell Mode boot toggle lives in
Settings → General now (V4 addendum). (The pure
now_playing/status formatters remain in shell_home.py, unused
by this panel, per the V4 brief's no-churn call.) V4 addendum:
the shell's colours follow the dashboard accent Alek sets in
Customize — the stylesheet's accent family hue-shifts via
``aria.hue_wheel.shift_palette`` (the dashboard's mechanism),
the orb discs repaint to match, ``set_accent`` applies changes
live, and the dashboard re-resolves the accent every time
Shell Home opens.

Type (or later, say) what you want in the dock; an unambiguous
app name launches it, everything else goes to ARIA as a normal
turn through the dashboard's existing submit path, approval
gates and all. The **Dashboard** button is always visible,
bottom right: one click and the shell steps aside — Shell Mode
never replaces explorer.exe, so Windows was never gone, just
covered.

The card never fabricates: no briefing composed yet yields
report_text's own honest placeholder line.

All routing logic lives in aria/shell_home.py and enumeration in
aria/app_launcher.py (both pure); this file is only the paint.
Callbacks are injected: on_launch(app), on_command(text),
on_exit() — the dashboard wires them; tests and screenshots wire
recorders. Card providers are injected the same way and default
to the real seams, lazily and fail-soft.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime

from aria import app_launcher, shell_home

try:
    from PyQt6.QtCore import QFileInfo, QSize, Qt, QTimer
    from PyQt6.QtGui import (QColor, QFont, QIcon, QPainter, QPixmap,
                             QTextOption)
    from PyQt6.QtWidgets import (
        QFileIconProvider,
        QFrame,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QPlainTextEdit,
        QPushButton,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )
except Exception:  # pragma: no cover - Qt absent (headless tools)
    QWidget = object

#: Same-app relaunch guard (V3): a second launch of the SAME app
#: inside this window is treated as signal bounce, not intent —
#: the belt-and-braces half of the double-launch fix. Legitimate
#: re-opens come seconds apart; the window stays far below that.
LAUNCH_GUARD_SECONDS = 1.5

_STYLE = """
QWidget#shellHome { background: #0b1622; }
QLabel#shellClock { color: #eaf6ff; font-size: 56px; font-weight: 600; }
QLabel#shellGreeting { color: #57c7d4; font-size: 22px; }
QLabel#shellDate { color: #8fb8c9; font-size: 15px; }
QLabel#shellCount { color: #8fb8c9; font-size: 13px; }
QLineEdit#shellSearch { background: #101f30; color: #eaf6ff;
            border: 1px solid #1b3042; border-radius: 6px;
            padding: 9px 12px; font-size: 15px; }
QListWidget#shellAppList { background: #0e1c2c; color: #dceef7;
              border: 1px solid #1b3042; border-radius: 10px;
              font-size: 14px; }
QListWidget#shellAppList::item { padding: 8px 4px; }
QListWidget#shellAppList::item:selected { background: #17435a;
              border-radius: 8px; }
QFrame#shellCard { background: #0e1c2c; border: 1px solid #1b3042;
              border-radius: 10px; }
QLabel#shellCardTitle { color: #57c7d4; font-size: 14px;
              font-weight: 700; }
QPlainTextEdit#shellBriefingText { background: #0e1c2c;
              color: #dceef7; border: none; font-size: 16px; }
QScrollBar:vertical { background: #0e1c2c; width: 10px; }
QScrollBar::handle:vertical { background: #2b5873;
              border-radius: 5px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
              height: 0; }
QFrame#shellDock { background: #101f30; border: 1px solid #2b5873;
              border-radius: 22px; }
QLineEdit#shellCommand { background: transparent; color: #eaf6ff;
            border: none; padding: 10px 6px; font-size: 15px; }
QPushButton#shellExit { background: #12303f; color: #eaf6ff;
    border: 1px solid #2b5873; border-radius: 6px;
    padding: 10px 18px; font-size: 14px; }
QPushButton#shellExit:hover { background: #17435a; }
"""


#: The shell's stock accent family (V4 addendum): every accent
#: hue in the stylesheet and the orb's paint, hue-shifted as one
#: family by aria.hue_wheel.shift_palette — the dashboard's own
#: mechanism — whenever Alek's Customize accent is in force.
#: Neutral dark surfaces and body text are NOT in the family.
_ACCENT_BASE = "#57c7d4"
_ACCENT_LINKED = ("#57c7d4", "#bdf3fa", "#2b5873", "#17435a",
                  "#12303f", "#1b3042")
_ORB_STOCK = ("#2b5873", "#57c7d4", "#bdf3fa")  # ring, core, heart


def _valid_accent(accent):
    """Normalize a '#rrggbb' accent, or None when it is not one."""
    try:
        s = str(accent or "").strip().lower()
        if len(s) == 7 and s.startswith("#"):
            int(s[1:], 16)
            return s
    except Exception:
        pass
    return None


def _shift_map(accent):
    """{stock hex: shifted hex} for the shell's accent family,
    or None when *accent* is unset/invalid (stock palette)."""
    accent = _valid_accent(accent)
    if not accent:
        return None
    try:
        from aria.hue_wheel import shift_palette
        return shift_palette({h: h for h in _ACCENT_LINKED},
                             _ACCENT_BASE, accent)
    except Exception:
        return None


def shell_style(accent=None):
    """The panel stylesheet for *accent*: the stock sheet with
    its accent family hue-shifted to the accent (dashboard
    parity). Unset/invalid accent returns the stock sheet
    unchanged — the default path can never lose its theme."""
    shifted = _shift_map(accent)
    if not shifted:
        return _STYLE
    import re
    return re.sub("|".join(re.escape(h) for h in _ACCENT_LINKED),
                  lambda m: shifted[m.group(0)], _STYLE)


def _orb_colors(accent=None):
    """(ring, core, heart) hexes for the orb under *accent*."""
    shifted = _shift_map(accent)
    if not shifted:
        return _ORB_STOCK
    return tuple(shifted[h] for h in _ORB_STOCK)


def _resolve_accent():
    """The accent currently SET, in the dashboard's own order
    (ui_prefs Customize first, then settings 'ui_accent').
    Reimplemented fail-soft here — shell_panel must never import
    the dashboard (it imports us). None = nothing set = stock."""
    try:
        from aria import ui_prefs
        found = _valid_accent(ui_prefs.get_accent())
        if found:
            return found
    except Exception:
        pass
    try:
        from aria import config
        return _valid_accent(config.get_setting("ui_accent", None))
    except Exception:
        return None


class OrbDisc(QWidget):
    """ARIA's presence on the home: a simple orb disc drawn in
    Qt (the brief's sanctioned lightweight stand-in until a
    heavier orb widget earns a seam here). Its colours follow
    the shell's accent family via set_colors."""

    def __init__(self, parent=None, diameter=84, colors=None):
        super().__init__(parent)
        self._d = diameter
        self.setFixedSize(diameter, diameter)
        ring, core, heart = colors or _ORB_STOCK
        self._ring = QColor(ring)
        self._core_color = QColor(core)
        self._heart = QColor(heart)

    def set_colors(self, ring, core, heart):
        self._ring = QColor(ring)
        self._core_color = QColor(core)
        self._heart = QColor(heart)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self._d
        # Outer ring.
        p.setPen(self._ring)
        p.setBrush(Qt.GlobalColor.transparent)
        p.drawEllipse(2, 2, d - 4, d - 4)
        # Glowing core.
        core = int(d * 0.52)
        off = (d - core) // 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._core_color)
        p.drawEllipse(off, off, core, core)
        # Bright heart.
        heart = int(d * 0.22)
        hoff = (d - heart) // 2
        p.setBrush(self._heart)
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


def _default_research():
    """The research section for the Briefing card, guarded
    exactly like the dashboard's Briefing-tab load: research can
    never break the render, so any failure is an empty string."""
    try:
        from aria.research import research_section_text
        return research_section_text() or ""
    except Exception:
        return ""


class ShellPanel(QWidget):
    """The Shell Home screen. See the module docstring."""

    def __init__(self, parent=None, apps=None, apps_provider=None,
                 on_launch=None, on_command=None, on_exit=None,
                 now_provider=None, briefing_provider=None,
                 research_provider=None, accent=None,
                 time_provider=None):
        super().__init__(parent)
        self.setObjectName("shellHome")
        # Accent (V4 addendum): an explicit accent wins; otherwise
        # resolve the one currently set (Customize / Settings),
        # exactly like the dashboard does at boot. None anywhere
        # means the stock cyan family, unchanged.
        self._accent = (_valid_accent(accent) if accent is not None
                        else _resolve_accent())
        self.setStyleSheet(shell_style(self._accent))
        self._apps_provider = apps_provider
        self._apps = list(apps) if apps is not None else []
        self._on_launch = on_launch
        self._on_command = on_command
        self._on_exit = on_exit
        self._now_provider = now_provider or datetime.now
        # Monotonic clock for the relaunch guard — injectable so
        # the guard window is testable without real waiting.
        self._time_provider = time_provider or time.monotonic
        self._last_launch_key = None
        self._last_launch_at = 0.0
        self.briefing_text = None  # set by the Briefing card
        self._briefing_provider = briefing_provider or _default_briefing
        self._research_provider = research_provider or _default_research
        self._icon_provider = None

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 24)
        root.setSpacing(14)

        # -- header: orb + clock/greeting/date ------------------
        header = QHBoxLayout()
        self.orb = OrbDisc(self, diameter=84,
                           colors=_orb_colors(self._accent))
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
        # V3: cell height carries a TWO-line name — IconMode
        # elides a long name that cannot fit its cell, and the
        # squeezed V2 render showed names sliced to one line.
        self.app_list.setGridSize(QSize(120, 122))
        self.app_list.setWordWrap(True)
        self.app_list.setResizeMode(
            QListWidget.ResizeMode.Adjust)
        self.app_list.setMovement(QListWidget.Movement.Static)
        self.app_list.setUniformItemSizes(True)
        # ONE launch signal path (V3 double-launch fix): V2 also
        # connected itemDoubleClicked here, and Qt fires BOTH
        # signals for a single double-click — every double-click
        # launched the app twice on Alek's machine. itemActivated
        # alone already covers double-click AND Enter.
        self.app_list.itemActivated.connect(self._activate_item)
        left.addWidget(self.app_list, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("shellCount")
        left.addWidget(self.count_label)
        main.addLayout(left, 2)

        rail = QVBoxLayout()
        rail.setSpacing(10)
        # V4: the Briefing card IS the rail — it takes all the
        # vertical space the three V2/V3 cards used, and its body
        # is the Briefing tab's full text in a view that fills
        # the card. Now Playing and Status are gone entirely.
        self.briefing_frame, self.briefing_body = \
            self._make_briefing_card()
        rail.addWidget(self.briefing_frame, 1)
        # The rail is Briefing ONLY (V4 addendum): the Shell Mode
        # boot toggle moved to Settings → General, its one home —
        # no checkbox here anymore.
        main.addLayout(rail, 1)
        root.addLayout(main, 1)

        # -- bottom dock: command pill + Dashboard button ---------
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        self.dock = QFrame()
        self.dock.setObjectName("shellDock")
        dock_lay = QHBoxLayout(self.dock)
        dock_lay.setContentsMargins(16, 2, 16, 2)
        self.dock_orb = OrbDisc(self.dock, diameter=18,
                                colors=_orb_colors(self._accent))
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
    def _make_briefing_card(self):
        """The rail's one card (V4): title + a read-only text
        view that FILLS the card. Same widget family as the
        Workspace Briefing tab (a plain-text view), word wrap on,
        card-coloured background via its stylesheet rule — the
        view owns its surface, so the V3 white-slab failure mode
        (a label inheriting the light app palette) cannot recur.
        A scrollbar appears only when a briefing outgrows even
        this full-rail card; a typical one fits whole."""
        frame = QFrame()
        frame.setObjectName("shellCard")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(4)
        head = QLabel("BRIEFING")
        head.setObjectName("shellCardTitle")
        lay.addWidget(head)
        body = QPlainTextEdit()
        body.setObjectName("shellBriefingText")
        body.setReadOnly(True)
        body.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        body.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        lay.addWidget(body, 1)
        frame.setSizePolicy(QSizePolicy.Policy.Preferred,
                            QSizePolicy.Policy.Expanding)
        self.briefing_text = body
        return frame, body

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

    # -- accent -------------------------------------------------
    def set_accent(self, accent):
        """Follow the dashboard's accent live (V4 addendum):
        rebuild the stylesheet from the accent family and repaint
        both orb discs. Invalid accents are ignored — a bad
        colour can never cost the shell its theme. Called by the
        dashboard's apply_accent (Customize) and on Shell Home
        open, so a colour changed while the shell was closed
        shows the moment it opens, no restart."""
        accent = _valid_accent(accent)
        if not accent:
            return
        self._accent = accent
        self.setStyleSheet(shell_style(accent))
        colors = _orb_colors(accent)
        self.orb.set_colors(*colors)
        self.dock_orb.set_colors(*colors)

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
        """Repaint the rail's one card (V4): the Briefing tab's
        exact text — report_text of the latest briefing plus the
        guarded research append, composed by the pure helper.
        Fail-soft: a broken briefing seam reads as the None
        report (report_text's placeholder), and the research
        seam is guarded inside its provider."""
        try:
            report = self._briefing_provider()
        except Exception:
            report = None
        try:
            research = self._research_provider() or ""
        except Exception:
            research = ""
        self.briefing_body.setPlainText(
            shell_home.briefing_full_text(report, research))

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
        # Same-app relaunch guard (V3): one physical gesture can
        # still reach this method twice through paths no signal
        # audit can fully foresee (an OS-level double activation,
        # a command-bar Enter bouncing) — a repeat of the SAME
        # app inside the guard window is bounce, not intent, and
        # is dropped. A different app, or the same app after the
        # window, launches normally.
        key = (str((app or {}).get("name", "")),
               str((app or {}).get("path", "")))
        now = self._time_provider()
        if key == self._last_launch_key and \
                now - self._last_launch_at < LAUNCH_GUARD_SECONDS:
            return
        self._last_launch_key = key
        self._last_launch_at = now
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
