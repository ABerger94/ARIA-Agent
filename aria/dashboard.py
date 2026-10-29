"""Native Qt command center for ARIA's existing agent and desktop services.

Qt stays lazy so the classic host remains available. The orb adapter retains its own attribution.
"""
from __future__ import annotations

import html
import os
import threading
import time
from datetime import datetime

STYLE = """
QWidget { background: #080d14; color: #dce6ef; font: 12px 'Segoe UI'; }
QFrame#card { background: #0d1520; border: 1px solid #1b3042; border-radius: 10px; }
QFrame#visionPip { background: rgba(0, 6, 10, 242); border: 1px solid #54d9e8; border-radius: 6px; }
QLabel#pipTitle { color: #54d9e8; font: bold 9px 'Consolas'; letter-spacing: 1px; }
QPushButton#pipClose { background: transparent; border: none; padding: 0; color: #ffffff; font-weight: bold; }
QLabel { background: transparent; border: none; }
QLabel#title { color: #54d9e8; font: bold 11px 'Consolas'; letter-spacing: 2px; }
QLabel#brand { color: #77e7ef; font: bold 28px 'Consolas'; letter-spacing: 4px; }
QPushButton { background: #111e2c; border: 1px solid #274257; border-radius: 6px; padding: 9px 12px; }
QPushButton:hover { background: #193044; border-color: #54d9e8; }
QPushButton:pressed { background: #24516a; }
QPushButton#primary { background: #174452; border-color: #319aab; color: #a5f5fa; }
QTextBrowser, QLineEdit, QListWidget { background: #0a111b; border: 1px solid #1b3042; border-radius: 6px; padding: 10px; selection-background-color: #24516a; }
QTabWidget::pane { border: 1px solid #1b3042; background: #0d1520; }
QTabBar::tab { background: #0d1520; color: #829aaf; padding: 12px 14px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #77e7ef; border-bottom: 2px solid #54d9e8; }
QProgressBar { border: none; background: #172434; border-radius: 3px; height: 6px; }
QProgressBar::chunk { background: #42c9da; border-radius: 3px; }
QSlider::groove:horizontal { height: 4px; background: #21374b; }
QSlider::handle:horizontal { background: #66dbe7; width: 12px; margin: -5px 0; border-radius: 6px; }
"""

# The stylesheet's colour family (Port 5, Customize): every hex here is
# hue-linked — one accent re-derives all of them, S/V ratios preserved
# (aria/hue_wheel.shift_palette). #dce6ef (body text) is deliberately
# NOT linked: text stays readable under any accent.
_STYLE_LINKED = ('#080d14', '#0d1520', '#0a111b', '#1b3042', '#274257',
                 '#54d9e8', '#77e7ef', '#111e2c', '#193044', '#24516a',
                 '#174452', '#319aab', '#a5f5fa', '#42c9da', '#172434',
                 '#21374b', '#66dbe7', '#829aaf')
_STYLE_BASE = '#54d9e8'


def style_for_accent(accent):
    """STYLE with its colour family hue-shifted to `accent`.

    None or an invalid accent returns STYLE unchanged — a bad stored
    preference can never cost the window its theme."""
    if not accent:
        return STYLE
    from aria.hue_wheel import shift_palette
    shifted = shift_palette({h: h for h in _STYLE_LINKED},
                            _STYLE_BASE, accent)
    if shifted is None:
        return STYLE
    import re
    return re.sub('|'.join(re.escape(h) for h in _STYLE_LINKED),
                  lambda m: shifted[m.group(0)], STYLE)


# Conversation colours (Alek, 2026-10-27, final say): ARIA's
# responses render in the SYSTEM colour — whatever accent is
# currently set (Customize wheel / Settings preset), never a
# frozen cyan — and the user's text stays WHITE (the amber /
# pale-yellow user side was his "all text yellow" complaint).
# System lines drop to a muted blue-grey you can read past.
# Activity levels earn their red / amber / green; routine info
# stays muted.
DEFAULT_ACCENT = '#54d9e8'
CHAT_COLORS = {'aria': ('#54d9e8', '#54d9e8'),
               'user': ('#ffffff', '#f4f7fa'),
               'sys': ('#8ea3b6', '#aebfd0')}


def _valid_accent(accent):
    """Normalize a '#rrggbb' accent, or None when it is not one."""
    try:
        s = str(accent or '').strip().lower()
        if len(s) == 7 and s.startswith('#'):
            int(s[1:], 16)
            return s
    except Exception:
        pass
    return None


def _current_accent():
    """The accent currently SET, in the dashboard's own boot
    order: ui_prefs (Customize) first, then the settings file's
    'ui_accent' (Settings presets). None = nothing set."""
    try:
        from aria import ui_prefs
        found = _valid_accent(ui_prefs.get_accent())
        if found:
            return found
    except Exception:
        pass
    try:
        from aria import config
        return _valid_accent(config.get_setting('ui_accent', None))
    except Exception:
        return None


def chat_colors(accent=None):
    """The Conversation tab's colours for a given accent
    (default: whatever accent is currently set). ARIA's name AND
    text are the accent itself, at full strength — the first cut
    mixed her text 82% of the way toward white, which read as
    white text whatever colour was set (Alek, 2026-10-27); the
    name is told apart by bold alone. The user is always white;
    system muted."""
    resolved = _valid_accent(accent) or _current_accent() \
        or DEFAULT_ACCENT
    return {'aria': (resolved, resolved),
            'user': CHAT_COLORS['user'],
            'sys': CHAT_COLORS['sys']}
ACTIVITY_COLORS = {'error': ('#ff6b6b', '#ffd9d9'),
                   'warn': ('#ffb84d', '#ffedc9'),
                   'ok': ('#2ee6a8', '#d3ffec'),
                   'info': ('#8ea3b6', '#c2d2de')}


def _activity_kind(level):
    l = str(level).casefold()
    if any(k in l for k in ('error', 'err', 'fail', 'critical')):
        return 'error'
    if 'warn' in l:
        return 'warn'
    if any(k in l for k in ('ok', 'success', 'done')):
        return 'ok'
    return 'info'


def activity_html(events):
    """Render activity events [(ts, level, message, meta)] as
    coloured HTML, LVI-style: the level tag carries the colour
    (red error, amber warn, green ok, muted info), the message in
    a matching soft tint, timestamps dimmest. Escaped — a log
    line is never markup."""
    import html as _html
    parts = []
    for ts, level, message, *_rest in events:
        lvl_col, msg_col = ACTIVITY_COLORS[_activity_kind(level)]
        body = _html.escape(str(message)).replace('\n', '<br/>')
        parts.append(
            f'<p style="margin:0 0 8px 0">'
            f'<span style="color:#5f7285">{_html.escape(str(ts))}</span> '
            f'<b style="color:{lvl_col}">{_html.escape(str(level).upper())}</b><br/>'
            f'<span style="color:{msg_col}">{body}</span></p>')
    return ''.join(parts)


def chat_html(entries, accent=None):
    """Render chat entries [(ts, sender, text)] as coloured HTML:
    ARIA in the system colour (the accent currently set; the
    window passes its live accent so a Customize change repaints
    the log too), the user in white, system lines muted,
    timestamps dim. Text is escaped — chat content is not
    markup."""
    import html as _html
    colors = chat_colors(accent)
    parts = []
    for ts, sender, text in entries:
        # Production logs her as 'A.R.I.A.' (brain.log_conversation)
        # — match on letters only, or the dots defeat the substring
        # test and her entries fall through to the white user pair
        # (the bug behind "her text is still white", 2026-10-27).
        _s = ''.join(ch for ch in str(sender).casefold() if ch.isalpha())
        who = ('aria' if 'aria' in _s
               else 'sys' if 'system' in _s or _s == 'sys'
               else 'user')
        name_col, text_col = colors[who]
        body = _html.escape(str(text)).replace('\n', '<br/>')
        parts.append(
            f'<p style="margin:0 0 10px 0">'
            f'<span style="color:#7d8fa3">{_html.escape(str(ts))}</span> '
            f'<b style="color:{name_col}">{_html.escape(str(sender))}</b><br/>'
            f'<span style="color:{text_col}">{body}</span></p>')
    return ''.join(parts)


def _apply_painted_accent(accent):
    """Push an accent into the painted widgets' palettes (orb, bars).

    Each module guards itself; a failure here is cosmetic and must
    never break the window."""
    import importlib
    for mod_name in ('aria.resonance', 'aria.metric_bar', 'aria.holo_face',
                     'aria.neural_field', 'aria.orb3d'):
        try:
            importlib.import_module(mod_name).apply_accent(accent)
        except Exception:
            pass


def _services_status_text(subsystems) -> str:
    """The header status line's text. The label was created with
    'Connecting to services' and NOTHING ever updated it — it sat
    at the top of the dashboard forever (Alek, 2026-10-27). Until
    the first subsystem report lands, 'Connecting' is the honest
    text; after that the label counts the armed services from the
    same report the left column renders."""
    subs = list(subsystems or [])
    if not subs:
        return 'Connecting to services'
    ok_n = sum(1 for _name, _status, ok in subs if ok)
    if ok_n == len(subs):
        return 'All services connected'
    return f'{ok_n} of {len(subs)} services connected'


def create_dashboard(callbacks):
    from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
    from PyQt6.QtGui import (QFont, QImage, QPixmap, QColor, QShortcut,
                             QKeySequence, QIcon, QPainter, QPen)
    from PyQt6.QtWidgets import (QMainWindow, QWidget, QFrame, QLabel, QPushButton,
                                QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget,
                                QTextBrowser, QLineEdit, QSlider, QGridLayout,
                                QFileDialog, QDialog, QApplication, QListWidget, QListWidgetItem,
                                QScrollArea, QSizePolicy)
    from aria import frame, hud, config, applock
    from aria.resonance import ResonanceOrb
    from aria.neural_field import NeuralField
    from aria.constellation import ConstellationWidget
    from aria.orb3d import Orb3D
    from aria.metric_bar import MetricBar, StatusPlaque
    from aria.video_panel import VideoPanel, HAVE_VIDEO as _HAVE_VIDEO
    try:
        from aria import ui_prefs as _ui_prefs
        _accent0 = _ui_prefs.get_accent()
        _centerpiece0 = _ui_prefs.get_centerpiece()
    except Exception:
        _accent0 = None
        _centerpiece0 = 'orb'
    _apply_painted_accent(_accent0)
    try:
        from aria.holo_face import HoloFace
    except Exception:
        HoloFace = None

    def _x_icon(size=28):
        """A white × as PAINTED PIXELS (Alek, 2026-10-27).

        Two text-glyph attempts failed on his Windows build —
        the buttons showed as bare outlined squares. Root cause
        found in the app stylesheet: the global QPushButton
        rule carries padding 9px/12px, which on a 22px button
        leaves ~0px for the glyph; the letter was clipped to
        nothing no matter its colour. A QPainter-drawn icon has
        no font, no glyph, and (with the buttons' padding
        zeroed) nothing left to clip it."""
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor('#ffffff'))
        pen.setWidth(max(3, size // 7))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        m = size * 0.26
        p.drawLine(int(m), int(m), int(size - m), int(size - m))
        p.drawLine(int(size - m), int(m), int(m), int(size - m))
        p.end()
        return QIcon(pm)

    class VisionPip(QFrame):
        """On-demand picture-in-picture, Mark-LVI style (Alek,
        2026-10-27): when he asks what she sees, the exact frame
        the vision tool analyzed pops up in the center card's
        bottom-right corner — 'VISUAL INPUT' header, a close
        button, gone again after 10 seconds (LVI used 6; the
        extra beats cover the vision call and speech lag, so it
        is still up while she answers). NOT the ambient
        camera tile he removed that morning: nothing captures
        for this widget and it never appears unprompted; vision
        tools publish a frame only when he has asked her to look
        (aria.vision.publish_vision_preview)."""

        _W = 244

        def __init__(self, parent):
            super().__init__(parent)
            self.setObjectName('visionPip')
            self.setFixedWidth(self._W)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(6, 5, 6, 6)
            lay.setSpacing(4)
            hdr = QHBoxLayout()
            self.title = QLabel('◈ VISUAL INPUT')
            self.title.setObjectName('pipTitle')
            hdr.addWidget(self.title)
            hdr.addStretch()
            close = QPushButton('')
            close.setIcon(_x_icon())
            close.setIconSize(QSize(10, 10))
            close.setObjectName('pipClose')
            close.setFixedSize(16, 16)
            close.clicked.connect(self.hide)
            hdr.addWidget(close)
            lay.addLayout(hdr)
            self.img = QLabel()
            self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(self.img)
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self.hide)
            self.hide()

        def show_frame(self, jpeg, label=''):
            px = QPixmap()
            px.loadFromData(jpeg)
            if px.isNull():
                return
            scaled = px.scaled(self._W - 12, 160,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            self.img.setPixmap(scaled)
            self.img.setFixedSize(scaled.size())
            self.title.setText('◈ VISUAL INPUT' +
                               (f' — {label}' if label else ''))
            self.adjustSize()
            self.place()
            self.show()
            self.raise_()
            self._timer.start(10000)

        def place(self):
            """Bottom-right corner of the parent (the center card),
            12px in from its edges."""
            parent = self.parent()
            if parent is None:
                return
            self.move(max(0, parent.width() - self.width() - 12),
                      max(0, parent.height() - self.height() - 12))

    class Dashboard(QMainWindow):
        panel_result = pyqtSignal(str, object)
        email_result = pyqtSignal(int, object)
        news_result = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self.setWindowTitle('A.R.I.A. | Command Center')
            self.resize(1440, 900)
            self.setMinimumSize(1080, 720)
            _init_accent = _accent0
            if not _init_accent:
                try:
                    _init_accent = config.get_setting("ui_accent", "#54d9e8")
                except Exception:
                    _init_accent = "#54d9e8"
            self.apply_accent(_init_accent)
            self.setAcceptDrops(True)
            self._loading = set()
            self._email_request = 0
            self._last_chat = None
            self._last_activity = None
            self._last_refresh = 0.0
            self._confirm_banner = None
            self._confirm_token = None
            self._confirm_busy = False
            root = QWidget(); self.setCentralWidget(root); self._root = root
            layout = QVBoxLayout(root); layout.setContentsMargins(20, 16, 20, 16); layout.setSpacing(16)
            head = QHBoxLayout()
            brand = QLabel('A.R.I.A.'); brand.setObjectName('brand'); head.addWidget(brand)
            head.addWidget(QLabel('ADAPTIVE ROBOTIC INTELLIGENCE AGENT')); head.addStretch()
            self.status = QPushButton('Connecting to services'); self.status.setFixedWidth(200); self.status.setFlat(True); self.status.setCursor(Qt.CursorShape.PointingHandCursor); self.status.setToolTip('Click for service details'); self.status.setStyleSheet('QPushButton { text-align: right; border: none; background: transparent; } QPushButton:hover { text-decoration: underline; }'); self.status.clicked.connect(self.show_services_dialog); head.addWidget(self.status)
            self.clock = QLabel(); self.clock.setFixedWidth(250)
            self.clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            head.addWidget(self.clock)
            self.settings_button = QPushButton('Settings'); self.settings_button.clicked.connect(self.settings); head.addWidget(self.settings_button)
            layout.addLayout(head)
            body = QSplitter(Qt.Orientation.Horizontal); layout.addWidget(body, 1)
            left, ll = self.card('SYSTEM MONITOR'); left.setMinimumWidth(220); left.setMaximumWidth(290)
            self.left_card = left
            # The column must FIT the card with no scrolling (Alek,
            # 2026-10-27, third report — supersedes the same day's
            # scroll-area design, which itself replaced the layout
            # that OVERLAPPED its children when the stack's minimum
            # height exceeded the card). The stack earns the room
            # honestly: the three plaques share one row, the bars
            # are slimmer (MetricBar 30px), subsystem rows are
            # compact (12px), the volume label rides beside its
            # slider, and spacings are tight. The geometry test pins
            # the result at the minimum window size: every widget
            # inside the card, no overlap, no scroll area.
            ll.setContentsMargins(12, 12, 12, 12); ll.setSpacing(8)
            _ll_inner = QVBoxLayout()
            _ll_inner.setContentsMargins(0, 0, 0, 0)
            _ll_inner.setSpacing(5)
            ll.addLayout(_ll_inner, 1)
            ll = _ll_inner
            _plaques = QHBoxLayout(); _plaques.setSpacing(5)
            self.plaque_engine = StatusPlaque('ENGINE'); _plaques.addWidget(self.plaque_engine)
            self.plaque_state = StatusPlaque('STATE'); _plaques.addWidget(self.plaque_state)
            self.plaque_tasks = StatusPlaque('TASKS'); _plaques.addWidget(self.plaque_tasks)
            ll.addLayout(_plaques)
            self.cpu = MetricBar('CPU'); ll.addWidget(self.cpu)
            self.mem = MetricBar('MEM'); ll.addWidget(self.mem)
            self.net = MetricBar('NET'); ll.addWidget(self.net)
            self.power = QLabel(); ll.addWidget(self.power)
            sub_title = QLabel('SUBSYSTEMS'); sub_title.setObjectName('title'); ll.addWidget(sub_title)
            # One row per subsystem (Alek, 2026-10-27: the old
            # single wrapped QLabel was crushed by the column's
            # fixed contents — bottom subsystems were clipped and
            # never showed). Rows never wrap or clip; the compact
            # column above is sized so all eight fit the card.
            self.subsystems = QWidget()
            self._subsystems_layout = QVBoxLayout(self.subsystems)
            self._subsystems_layout.setContentsMargins(0, 0, 0, 0)
            self._subsystems_layout.setSpacing(2)
            self._subsystems_layout.addStretch()
            self._subsystem_rows = []
            ll.addWidget(self.subsystems)
            self.now_playing_title = QLabel('NOW PLAYING'); self.now_playing_title.setObjectName('title'); ll.addWidget(self.now_playing_title)
            self.track = QLabel('Nothing playing'); self.track.setWordWrap(True)
            # Two lines, ever (Alek, 2026-10-27: a long Spotify title
            # wrapped unbounded, the column overflowed, and the NOW
            # PLAYING text overran the subsystems). The refresh elides
            # to fit these two lines; the full title is the tooltip.
            self.track.setMaximumHeight(
                self.track.fontMetrics().lineSpacing() * 2 + 6)
            ll.addWidget(self.track)
            # Real progress, only when a source reports exact
            # position/duration (the HUD video panel does; SMTC
            # positions go stale, so no bar is fabricated for
            # those). Hidden unless media_data() carries a duration.
            from PyQt6.QtWidgets import QProgressBar
            self.media_progress = QProgressBar()
            self.media_progress.setRange(0, 1000)
            self.media_progress.setTextVisible(False)
            self.media_progress.setFixedHeight(4)
            self.media_progress.hide()
            ll.addWidget(self.media_progress)
            media = QHBoxLayout()
            # Explicit Play AND Pause (Alek, 2026-10-27): one
            # toggle button labelled "Play" that paused a playing
            # track was a bug, not a control. And the panel is not
            # Spotify-only anymore: these dispatch to WHATEVER is
            # playing (hud._dispatch_media_action — HUD panel,
            # else the active SMTC session, else media keys).
            self.media_buttons = {}
            # Symbols, not words (Alek, 2026-10-28) — but NOT the
            # emoji-style glyphs: ⏮/⏸/⏭ render in colour emoji
            # presentation on his Windows build. These are plain
            # geometric text glyphs instead (monochrome in Segoe
            # UI Symbol; ❚❚ already rides the PAUSED header as
            # text). Pause stays the heavy ❚❚ — the medium ❙❙
            # swap looked worse (Alek, 2026-10-28) — and the
            # triangle buttons go bold + a point larger instead,
            # to carry weight closer to the pause bars. Tooltips
            # keep the words for clarity.
            for name, action, tip, style in [
                    ('◀◀', 'previous', 'Previous',
                     'font-size: 18px; font-weight: bold;'),
                    ('▶', 'play', 'Play',
                     'font-size: 18px; font-weight: bold;'),
                    ('❚❚', 'pause', 'Pause', ''),
                    ('▶▶', 'next', 'Next',
                     'font-size: 18px; font-weight: bold;')]:
                b = QPushButton(name); b.setToolTip(tip)
                b.setStyleSheet(style)
                if action == 'pause':
                    # The ❚ glyph's cell is wide: two bars render
                    # 5px each with an 8px gap at 15px — wider
                    # apart than a pause icon should be (Alek:
                    # "They're far apart"). Negative letter
                    # spacing pulls the pair together: -5px
                    # spacing -> 3px gap, measured by render probe
                    # 2026-10-28. QFont, not the stylesheet, since
                    # QSS has no letter-spacing.
                    _pf = QFont(b.font())
                    _pf.setPixelSize(15)
                    _pf.setLetterSpacing(
                        QFont.SpacingType.AbsoluteSpacing, -5)
                    b.setFont(_pf)
                # All four the pause button's skinny profile
                # (Alek's screenshot, 2026-10-28: the tracked
                # pause button shrank narrower + shorter than the
                # 18px-bold three — "make them all skinny like
                # that pause button"). MAX width, not fixed: a
                # hard 64px made the row 256px wide, and where
                # the column offers less (~218px at 1280x800)
                # the layout squeezed the buttons INTO each
                # other — measured 12-13px overlaps. Capped +
                # expanding: they share the row evenly, never
                # overlap, and stop growing at 64.
                b.setFixedHeight(30)
                b.setMaximumWidth(64)
                b.setMinimumWidth(40)
                b.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Fixed)
                b.clicked.connect(lambda checked=False, a=action: hud._dispatch_media_action(a)); media.addWidget(b)
                self.media_buttons[action] = b
            ll.addLayout(media)
            # AUDIO (Alek, 2026-10-28, revised same evening): ONE
            # slider + a switch button, like the panel used to
            # have — the button flips the slider between COMPUTER
            # and PLAYER (whatever app is playing; the button
            # names it). The backend player session follows the
            # active app (aria/comhost.py), so the PLAYER side is
            # no longer Spotify-only. The freed row's height goes
            # to the album-art tile below.
            self._vol_target = 'device'
            _vol = QHBoxLayout()
            self.vol_target_btn = QPushButton('COMPUTER')
            self.vol_target_btn.setFixedWidth(104)
            _vol.addWidget(self.vol_target_btn)
            self.volume = QSlider(Qt.Orientation.Horizontal)
            self.volume.setRange(0, 100)
            self.volume.sliderMoved.connect(
                lambda value: hud.set_volume_for(self._vol_target,
                                                 value / 100))
            _vol.addWidget(self.volume, 1)
            ll.addLayout(_vol)

            def _toggle_vol_target():
                self._vol_target = ('player'
                                    if self._vol_target == 'device'
                                    else 'device')
                self._sync_vol_button()
                if not self.volume.isSliderDown():
                    self.volume.setValue(int(
                        hud.get_volume_for(self._vol_target) * 100))
            self._toggle_vol_target = _toggle_vol_target

            def _sync_vol_button():
                if self._vol_target == 'device':
                    self.vol_target_btn.setText('COMPUTER')
                else:
                    _app = str(getattr(self, '_player_app_now', '')
                               or '').upper()
                    self.vol_target_btn.setText(_app or 'PLAYER')
            self._sync_vol_button = _sync_vol_button
            self.vol_target_btn.clicked.connect(_toggle_vol_target)
            # REMOTE CONTROL used to sit here; it lives in Settings
            # (Connections tab, 'Open Remote Control') now (Alek,
            # 2026-10-27). open_remote() stays for Settings' use.
            # ALBUM ART (Alek, 2026-10-28: "maximize the space for
            # album artwork") — the artwork takes ALL the column's
            # leftover height (it replaces the old bottom stretch),
            # scaled to fit while keeping its square aspect. The
            # bytes arrive via frame.media_data() <- aria/smtc.py's
            # thumbnail read; no art (or nothing playing) leaves a
            # quiet placeholder tile, never a broken-image box.
            self.art = QLabel('♪')
            self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.art.setObjectName('art')
            self.art.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
            self.art.setMinimumHeight(110)
            self.art.setStyleSheet(
                'background: #14171d; color: #39415a;'
                ' font-size: 72px; border-radius: 10px;')
            ll.addWidget(self.art, 1)
            self._art_key = None
            self._art_pixmap = None
            self._art_scaled_size = None
            body.addWidget(left)
            center, cl = self.card('ARIA / LIVE')
            self._center_card = center
            # The neuron field (Mark-LVI's HudCanvas layer, ported
            # to aria/neural_field.py — Alek, 2026-10-27) is the
            # centre CARD's background, not just the stack's:
            # extended 07:58 at his word to run the whole card —
            # top to bottom, behind the title, the centrepiece,
            # the IDLE state line and the hold-to-
            # talk buttons. Sizing is synced in render_once /
            # resizeEvent (_sync_neural_geometry).
            # The BLACK backdrop the centrepiece has always sat
            # on (#080d14) is a layer in its own right, spanning
            # the SAME space as the neurons — the whole card —
            # UNDER the field (Alek, 2026-10-27: the stack-sized
            # black background sat OVER the neurons; it must
            # span what the neurons span, behind them).
            self.center_bg = QWidget(center)
            self.center_bg.setStyleSheet('background: #080d14;')
            self.center_bg.lower()
            self.neural = NeuralField(center)
            self.center_stack = QWidget()
            # The app stylesheet paints every QWidget's background;
            # the stack MUST be transparent or it slabs over the
            # card-wide neural field (first cut of the extension
            # rendered exactly that black box — Alek: 'Nothing
            # changed', 2026-10-27).
            self.center_stack.setStyleSheet('background: transparent;')
            _stack = QGridLayout(self.center_stack)
            _stack.setContentsMargins(0, 0, 0, 0)
            self.face = ResonanceOrb(transparent_background=True); _stack.addWidget(self.face, 0, 0)
            self.video = VideoPanel() if _HAVE_VIDEO else None
            if self.video is not None:
                self.video.hide(); _stack.addWidget(self.video, 0, 0)
                self.video.closed.connect(self._on_video_closed)
                # Gap fill 6: let the media_control tool reach the
                # built-in player (via its command signal).
                try:
                    from aria import media_control as _mc
                    _mc.register_panel(self.video)
                except Exception:
                    pass
            self.holo = HoloFace(transparent_background=True) if HoloFace is not None else None
            self._centerpiece = _centerpiece0
            if self._centerpiece == 'holo' and self.holo is None:
                self._centerpiece = 'orb'
            if self.holo is not None:
                self.holo.hide(); _stack.addWidget(self.holo, 0, 0)
            self.mem_map_center = ConstellationWidget()
            self.mem_map_center.hide(); _stack.addWidget(self.mem_map_center, 0, 0)
            # The map's ✕ (Alek, 2026-10-27): while the map is
            # the centerpiece it carries a close button in its
            # top-right corner that hands the center back to
            # the normal centerpiece — the orb. A child of the
            # map widget, so it exists exactly when the map does.
            self.map_close = QPushButton('', self.mem_map_center)
            self.map_close.setIcon(_x_icon())
            self.map_close.setIconSize(QSize(15, 15))
            self.map_close.setFixedSize(26, 26)
            self.map_close.setToolTip('Close memory map')
            self.map_close.setStyleSheet(
                'background: rgba(10, 20, 30, 190); padding: 0;'
                'border: 1px solid #1b3042; border-radius: 4px;')
            self.map_close.clicked.connect(
                lambda: self.set_centerpiece('orb'))
            # The 3D orb centerpiece (Alek, 2026-10-27): the
            # waveform on a spinnable ball. The flat orb above
            # stays untouched; this is a separate widget fed by
            # the same advance() contract.
            self.orb3d = Orb3D(transparent_background=True)
            self.orb3d.hide(); _stack.addWidget(self.orb3d, 0, 0)
            cl.addWidget(self.center_stack, 1)
            self._last_orb_publish = 0.0
            self.state = QLabel('IDLE'); self.state.setAlignment(Qt.AlignmentFlag.AlignCenter); self.state.setObjectName('brand'); cl.addWidget(self.state)
            # No live speech subtitle under the state line
            # (Alek, 2026-10-27): the words she is saying used to
            # pop up here — removed at his word; the Conversation
            # log carries her text. hud.SUBTITLE_TEXT still
            # exists for the phone frame's strip.
            controls = QHBoxLayout()
            self.mic = QPushButton('Hold to talk'); self.mic.pressed.connect(callbacks.ptt_press); self.mic.released.connect(callbacks.ptt_release); controls.addWidget(self.mic)
            self.voice = QPushButton('Voice on'); self.voice.clicked.connect(frame.toggle_voice); controls.addWidget(self.voice)
            stop = QPushButton('Stop speaking'); stop.clicked.connect(callbacks.interrupt); controls.addWidget(stop); cl.addLayout(controls)
            # Whisper and centerpiece buttons used to live here
            # (Alek, 2026-10-27): whisper is the Voice / audio
            # Settings checkbox, the centerpiece is Settings →
            # Customize — the dashboard keeps neither button.
            body.addWidget(center)
            right, rl = self.card('WORKSPACE'); right.setMinimumWidth(340)
            self.tabs = QTabWidget(); self.views = {}
            for name in ('Conversation', 'Inbox', 'Calendar', 'Tasks', 'Activity', 'Briefing', 'News', 'Memory'):
                page = QWidget(); pl = QVBoxLayout(page)
                view = QTextBrowser(); view.setOpenExternalLinks(False); self.views[name] = view
                if name == 'Inbox':
                    toolbar = QHBoxLayout()
                    self.mail_summary = QLabel('Your inbox'); toolbar.addWidget(self.mail_summary, 1)
                    self.mail_refresh = QPushButton('Refresh'); self.mail_refresh.clicked.connect(lambda: self.load_panel('Inbox')); toolbar.addWidget(self.mail_refresh)
                    pl.addLayout(toolbar)
                    self.mail_search = QLineEdit(); self.mail_search.setPlaceholderText('Filter sender or subject…'); pl.addWidget(self.mail_search)
                    self.mail_search.textChanged.connect(self.filter_mail)
                    self.mail_list = QListWidget(); self.mail_list.setMinimumHeight(160)
                    self.mail_list.currentItemChanged.connect(self.open_mail)
                    mail_split = QSplitter(Qt.Orientation.Vertical); mail_split.addWidget(self.mail_list); mail_split.addWidget(view); mail_split.setSizes([320, 260]); pl.addWidget(mail_split, 1)
                    view.setPlainText('Refresh your inbox, then select a message to read it. Messages stay unread until you mark them read in your mail app.')
                    self.tabs.addTab(page, name)
                    continue
                if name == 'Briefing':
                    controls = QHBoxLayout()
                    refresh = QPushButton('Refresh briefing'); refresh.clicked.connect(lambda: self.load_panel('Briefing')); controls.addWidget(refresh)
                    self.brief_speak = QPushButton('Read aloud'); self.brief_speak.clicked.connect(self.read_briefing); controls.addWidget(self.brief_speak)
                    self.brief_speak.setEnabled(False); pl.addLayout(controls)
                    view.setPlainText('Your daily briefing will appear here after startup. Select Refresh to generate one.')
                if name == 'Calendar':
                    btn = QPushButton('Refresh ' + name.lower()); btn.clicked.connect(lambda checked=False, n=name: self.load_panel(n)); pl.addWidget(btn)
                    view.setPlainText('Select Refresh to load your connected ' + name.lower() + '.')
                if name == 'News':
                    btn = QPushButton('Refresh headlines'); btn.clicked.connect(lambda: self.load_news(force=True)); pl.addWidget(btn)
                    view.setOpenExternalLinks(True)
                    view.setPlainText('Open the News tab to load top headlines.')
                if name == 'Memory':
                    # What she remembers (Alek, 2026-10-27, his
                    # pick over the LVI overlay): a real tab, two
                    # views of the same store — the fact List
                    # (rows built by _rebuild_memory_tab) and the
                    # constellation Map (drag to spin it like a
                    # ball; 'View in center' sends it to the
                    # centerpiece). No QTextBrowser view here.
                    toolbar = QHBoxLayout()
                    self.mem_list_btn = QPushButton('List'); self.mem_list_btn.setCheckable(True); self.mem_list_btn.setChecked(True); self.mem_list_btn.clicked.connect(lambda: self._set_memory_mode('list')); toolbar.addWidget(self.mem_list_btn)
                    self.mem_map_btn = QPushButton('Map'); self.mem_map_btn.setCheckable(True); self.mem_map_btn.clicked.connect(lambda: self._set_memory_mode('map')); toolbar.addWidget(self.mem_map_btn)
                    self.mem_count = QLabel(''); toolbar.addWidget(self.mem_count, 1)
                    mem_refresh = QPushButton('Refresh'); mem_refresh.clicked.connect(self._rebuild_memory_tab); toolbar.addWidget(mem_refresh)
                    pl.addLayout(toolbar)
                    self.mem_scroll = QScrollArea(); self.mem_scroll.setWidgetResizable(True)
                    self.mem_rows = QWidget(); self.mem_rows.setStyleSheet('background: transparent;')
                    self._mem_layout = QVBoxLayout(self.mem_rows); self._mem_layout.setContentsMargins(6, 6, 6, 6); self._mem_layout.setSpacing(4)
                    self.mem_scroll.setWidget(self.mem_rows); pl.addWidget(self.mem_scroll, 1)
                    self.mem_map_box = QWidget(); self.mem_map_box.setStyleSheet('background: transparent;')
                    mbox = QVBoxLayout(self.mem_map_box); mbox.setContentsMargins(0, 0, 0, 0)
                    self.mem_map_tab = ConstellationWidget(); mbox.addWidget(self.mem_map_tab, 1)
                    detail = QHBoxLayout()
                    self.mem_detail = QLabel('Drag to spin the ball. Hover a star to read it; click one to select it for forgetting.'); self.mem_detail.setWordWrap(True); detail.addWidget(self.mem_detail, 1)
                    self.mem_forget_btn = QPushButton('Forget this fact'); self.mem_forget_btn.setEnabled(False); self.mem_forget_btn.clicked.connect(self._forget_selected_map_fact); detail.addWidget(self.mem_forget_btn)
                    mbox.addLayout(detail)
                    center_row = QHBoxLayout(); center_row.addStretch()
                    view_center = QPushButton('View in center'); view_center.clicked.connect(lambda: self.set_centerpiece('map')); center_row.addWidget(view_center); center_row.addStretch()
                    mbox.addLayout(center_row)
                    pl.addWidget(self.mem_map_box, 1)
                    self.mem_map_box.hide()
                    self.mem_map_tab.fact_selected.connect(self._on_map_fact_selected)
                    self.tabs.addTab(page, name)
                    self._rebuild_memory_tab()
                    continue
                pl.addWidget(view); self.tabs.addTab(page, name)
            rl.addWidget(self.tabs, 1)
            self._news_loaded_at = 0.0; self._news_loading = False
            self.news_result.connect(self._on_news_result)
            self.tabs.currentChanged.connect(self._on_tab_changed)
            row = QHBoxLayout()
            # Providers / Audio / Customize / Pet used to sit here too;
            # they live in Settings now (Alek, 2026-10-27) — the
            # open_* / toggle_pet methods stay for Settings' use.
            for text, fn in [('Commands', self.commands), ('Add file', self.browse_file)]:
                b = QPushButton(text); b.clicked.connect(fn); row.addWidget(b)
            rl.addLayout(row); body.addWidget(right); body.setSizes([250, 560, 550])
            command = QHBoxLayout()
            self.entry = QLineEdit(); self.entry.setPlaceholderText('Talk to ARIA or give her a task…'); self.entry.returnPressed.connect(self.submit)
            command.addWidget(self.entry, 1)
            send = QPushButton('Send directive'); send.setObjectName('primary'); send.clicked.connect(self.submit); command.addWidget(send)
            layout.addLayout(command)
            self.panel_result.connect(self.panel_finished)
            self.email_result.connect(self.mail_finished)
            self.entry.setFocus()
            self._show_centerpiece()
            self._settings_shortcut = QShortcut(QKeySequence('Ctrl+,'), self)
            self._settings_shortcut.activated.connect(self.settings)
            self._commands_shortcut = QShortcut(QKeySequence('Ctrl+K'), self)
            self._commands_shortcut.activated.connect(self.commands)
            self._fullscreen_shortcut = QShortcut(QKeySequence('F11'), self)
            self._fullscreen_shortcut.activated.connect(self._toggle_fullscreen)
            # Esc — the stop-talking quick key (Alek, 2026-10-27):
            # fires the same interrupt main wires the Stop
            # control to (speech.interrupt_speech). Window-wide,
            # typing included: when she's talking over you, Esc
            # is the fastest hand. (Leaving fullscreen stays on
            # F11 — Esc has one job here.)
            self._stop_shortcut = QShortcut(QKeySequence('Esc'), self)
            self._stop_shortcut.activated.connect(self._stop_talking)
            self._pip = VisionPip(self._center_card)
            self._pip_seen_ts = 0.0
            # App lock overlay (Alek, 2026-10-27): covers the whole
            # window with a gray-out panel while aria.applock is
            # locked. A 300ms watch syncs visibility with the shared
            # lock state — main's wake-word loop flips the same state
            # on a voice unlock, so no cross-thread signaling is
            # needed. The overlay is a child of the WINDOW (not of
            # root) so disabling root can't disable the unlock panel.
            self._lock_state = False
            self._pet_was_visible = False
            self._lock_overlay = QFrame(self)
            self._lock_overlay.setObjectName('lockOverlay')
            self._lock_overlay.setStyleSheet('QFrame#lockOverlay { background: rgba(24, 28, 34, 0.88); }')
            _lock_lay = QVBoxLayout(self._lock_overlay)
            _lock_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _lock_panel = QFrame(); _lock_panel.setObjectName('lockPanel')
            _lock_panel.setStyleSheet('QFrame#lockPanel { background: #171b21; border: 1px solid #3a4350; border-radius: 10px; }')
            _lock_panel.setFixedWidth(420)
            _panel_lay = QVBoxLayout(_lock_panel); _panel_lay.setContentsMargins(36, 30, 36, 30); _panel_lay.setSpacing(14)
            _lock_title = QLabel('A.R.I.A. IS LOCKED'); _lock_title.setObjectName('title'); _lock_title.setAlignment(Qt.AlignmentFlag.AlignCenter); _panel_lay.addWidget(_lock_title)
            self._lock_status = QLabel('Enter your password — or say “Hey Aria” and then your password.'); self._lock_status.setWordWrap(True); self._lock_status.setAlignment(Qt.AlignmentFlag.AlignCenter); _panel_lay.addWidget(self._lock_status)
            self._lock_field = QLineEdit(); self._lock_field.setEchoMode(QLineEdit.EchoMode.Password); self._lock_field.setPlaceholderText('Lock password'); self._lock_field.returnPressed.connect(self._try_lock_unlock); _panel_lay.addWidget(self._lock_field)
            _unlock_btn = QPushButton('Unlock'); _unlock_btn.setObjectName('primary'); _unlock_btn.clicked.connect(self._try_lock_unlock); _panel_lay.addWidget(_unlock_btn)
            _lock_lay.addWidget(_lock_panel)
            self._lock_overlay.hide()
            self._lock_timer = QTimer(self); self._lock_timer.timeout.connect(self._lock_watch); self._lock_timer.start(300)

        def _poll_vision_pip(self):
            """Show the PiP when a vision tool has published a new
            frame (he asked her to look at something). Cheap dict
            read; a stale or absent frame leaves the PiP hidden."""
            try:
                from aria import vision as _vision_mod
                pv = _vision_mod.vision_preview()
            except Exception:
                return
            if (pv.get('jpeg') and pv.get('ts', 0) > self._pip_seen_ts):
                self._pip_seen_ts = pv['ts']
                self._pip.show_frame(pv['jpeg'], pv.get('label', ''))

        def lock_app(self):
            """Settings' Lock button lands here: flip the shared
            lock state; _lock_watch does the visible work."""
            applock.lock()
            self._lock_watch()

        def unlock_app(self):
            applock.set_locked(False)
            self._lock_watch()

        def _lock_watch(self):
            """Sync the overlay with aria.applock (300ms timer).
            Edges only: on lock, hide the pet and disable the whole
            content root behind the gray-out; on unlock, restore."""
            try:
                locked = applock.is_locked()
            except Exception:
                locked = False
            if locked == self._lock_state:
                if locked:
                    self._lock_overlay.setGeometry(self.rect())
                return
            self._lock_state = locked
            pet = getattr(self, '_pet', None)
            if locked:
                self._pet_was_visible = bool(pet is not None and pet.isVisible())
                if pet is not None:
                    try: pet.hide()
                    except Exception: pass
                try: self._root.setEnabled(False)
                except Exception: pass
                self._lock_field.clear()
                self._lock_status.setText('Enter your password — or say “Hey Aria” and then your password.')
                self._lock_overlay.setGeometry(self.rect())
                self._lock_overlay.show(); self._lock_overlay.raise_()
                self._lock_field.setFocus()
            else:
                self._lock_overlay.hide()
                try: self._root.setEnabled(True)
                except Exception: pass
                if self._pet_was_visible and pet is not None:
                    try: pet.show_at_saved_pos()
                    except Exception: pass
                self._pet_was_visible = False

        def _try_lock_unlock(self):
            if applock.unlock_with(self._lock_field.text()):
                self._lock_watch()
            else:
                self._lock_status.setText('Wrong password — ARIA is still locked.')
                self._lock_field.clear(); self._lock_field.setFocus()

        def _stop_talking(self):
            """Esc: stop her mid-sentence (callbacks.interrupt —
            main wires it to speech.interrupt_speech)."""
            try:
                callbacks.interrupt()
            except Exception as exc:
                config.log_silent('dashboard.esc_stop', exc)

        def _toggle_fullscreen(self):
            """F11, Mark-LVI style (Alek, 2026-10-27): true full
            screen on the first press; the second press returns
            to her normal state — the maximized full window, not
            a floating one."""
            if self.isFullScreen():
                self.showMaximized()
            else:
                self.showFullScreen()

        def card(self, title):
            card = QFrame(); card.setObjectName('card'); lay = QVBoxLayout(card); lay.setContentsMargins(16, 16, 16, 16); lay.setSpacing(14)
            label = QLabel(title); label.setObjectName('title'); lay.addWidget(label)
            return card, lay

        def submit(self):
            text = self.entry.text().strip()
            if text:
                self.entry.clear(); self.tabs.setCurrentIndex(0); callbacks.submit(text)

        def browse_file(self):
            path, _ = QFileDialog.getOpenFileName(self, 'Send a file to ARIA')
            if path:
                frame.receive_file(path)

        def _sync_neural_geometry(self):
            # The neural field is the centre card's background:
            # keep it exactly card-sized (top to the buttons) —
            # and its black backdrop layer with it, same space.
            try:
                rect = self._center_card.rect()
                if self.neural.size() != rect.size():
                    self.neural.setGeometry(rect)
                if self.center_bg.size() != rect.size():
                    self.center_bg.setGeometry(rect)
            except Exception:
                pass

        def resizeEvent(self, event):
            super().resizeEvent(event)
            if getattr(self, '_lock_overlay', None) is not None and self._lock_overlay.isVisible():
                self._lock_overlay.setGeometry(self.rect())
            if hasattr(self, 'neural'):
                self._sync_neural_geometry()
            if getattr(self, '_pip', None) is not None and self._pip.isVisible():
                self._pip.place()
            if getattr(self, 'mem_map_center', None) is not None \
                    and self.mem_map_center.isVisible():
                self._place_map_close()

        def dragEnterEvent(self, event):
            if event.mimeData().hasUrls(): event.acceptProposedAction()

        def dropEvent(self, event):
            from aria.qthost import handle_dropped_urls
            handle_dropped_urls(event.mimeData().urls()); event.acceptProposedAction()

        def load_panel(self, name):
            if name in self._loading: return
            self._loading.add(name)
            if name == 'Inbox':
                self._email_request += 1
                self.mail_summary.setText('Refreshing inbox…'); self.mail_refresh.setEnabled(False)
            else:
                self.views[name].setPlainText('Loading…')
            def load():
                try:
                    if name == 'Briefing':
                        from aria.briefing import build_briefing
                        result = build_briefing(force=True)
                    elif name == 'Inbox':
                        from aria.mailview import fetch_messages
                        result = fetch_messages(limit=30)
                    else:
                        from aria.tools import builtins
                        result = builtins.tool_check_calendar(days=7)
                except Exception as exc:
                    result = 'Could not load ' + name.lower() + ': ' + str(exc)
                self.panel_result.emit(name, result if isinstance(result, dict) else config.redact(str(result)))
            threading.Thread(target=load, daemon=True).start()

        def panel_finished(self, name, result):
            self._loading.discard(name)
            if name == 'Briefing' and isinstance(result, dict):
                from aria.briefing import report_text
                text = report_text(result)
                try:
                    # Research briefs ride along under the briefing
                    # (Alek, 2026-10-28). Guarded: research can never
                    # break the briefing render.
                    from aria.research import research_section_text
                    section = research_section_text()
                    if section:
                        text += "\n" + section
                except Exception:
                    pass
                self.views[name].setPlainText(text)
                self.brief_speak.setEnabled(True)
                return
            if name != 'Inbox' or not isinstance(result, dict):
                self.views[name].setPlainText(str(result))
                if name == 'Inbox': self.mail_refresh.setEnabled(True)
                return
            self.mail_refresh.setEnabled(True)
            if not result.get('ok'):
                self.mail_summary.setText('Inbox unavailable')
                self.views[name].setPlainText(result.get('error', 'Could not load inbox.'))
                return
            self._email_request += 1
            self.mail_list.clear()
            rows = result.get('messages', [])
            for row in rows:
                date = self.mail_date(row.get('date', ''))
                unread = 'UNREAD  ' if row.get('unread') else ''
                item = QListWidgetItem(f"{unread}{row['sender']}  ·  {date}\n{row['subject']}")
                item.setData(Qt.ItemDataRole.UserRole, row)
                item.setSizeHint(QSize(0, 70)); self.mail_list.addItem(item)
            self.mail_summary.setText(f"{len(rows)} messages · {sum(bool(r.get('unread')) for r in rows)} unread in this list")
            self.views[name].setPlainText('Select a message to read it.' if rows else 'Your inbox is empty.')
            self.filter_mail(self.mail_search.text())

        def read_briefing(self):
            from aria.briefing import latest_briefing
            report = latest_briefing()
            if report: callbacks.speak(report['text'])

        def mail_date(self, value):
            try:
                return datetime.fromisoformat(value).astimezone().strftime('%b %d, %I:%M %p')
            except (ValueError, TypeError):
                return ''

        def filter_mail(self, query):
            for i in range(self.mail_list.count()):
                item = self.mail_list.item(i)
                item.setHidden(query.casefold() not in item.text().casefold())

        def open_mail(self, current, previous=None):
            self._email_request += 1
            if current is None: return
            row = current.data(Qt.ItemDataRole.UserRole)
            request_id = self._email_request
            self.views['Inbox'].setPlainText('Opening message…')
            def load():
                try:
                    from aria.mailview import fetch_message
                    result = fetch_message(row['uid'])
                except Exception as exc:
                    result = {'ok': False, 'error': str(exc)}
                self.email_result.emit(request_id, result)
            threading.Thread(target=load, daemon=True).start()

        def mail_finished(self, request_id, result):
            if request_id != self._email_request: return
            if not result.get('ok'):
                self.views['Inbox'].setPlainText(result.get('error', 'Could not open message.')); return
            row = result['message']
            text = (f"{row['subject']}\n\n{row['sender']} <{row['address']}>\n"
                    f"{self.mail_date(row['date'])}\n\n{row['body']}")
            if row.get('truncated'): text += '\n\n[Large message: preview truncated]'
            self.views['Inbox'].setPlainText(text)

        def apply_accent(self, color):
            # One accent path for both the Settings dialog (main) and
            # the Customize wheel (Port 5): the stylesheet's whole
            # colour family hue-shifts via style_for_accent, and the
            # painted widgets (orb, bars, holo face) follow through
            # _apply_painted_accent. Persistence stays with the
            # caller (config 'ui_accent' / ui_prefs respectively).
            accent = QColor(str(color))
            if not accent.isValid(): accent = QColor('#54d9e8')
            self.setStyleSheet(style_for_accent(accent.name()))
            _apply_painted_accent(accent.name())
            for _mw in (getattr(self, 'mem_map_tab', None),
                        getattr(self, 'mem_map_center', None)):
                if _mw is not None:
                    _mw.set_accent(accent.name())
            # The Conversation log follows the accent (Alek: ARIA's
            # responses in the system colour, whatever is set) —
            # remember it for render_once and force a repaint even
            # when the chat text itself has not changed.
            self._chat_accent = accent.name()
            if hasattr(self, '_last_chat'):
                self._last_chat = None

        def draft_command(self, text):
            self.entry.setText(text); self.entry.setFocus()

        def run_command(self, text):
            # Clicking a command RUNS it (Alek, 2026-10-27) — the
            # catalog used to only draft the example into the
            # command bar, so a click looked like nothing happened.
            # Same path as typing it and pressing Enter.
            text = (text or '').strip()
            if text:
                self.tabs.setCurrentIndex(0); callbacks.submit(text)

        def commands(self):
            from aria.ui_panels import create_commands_dialog
            create_commands_dialog(self, self.run_command).exec()

        def settings(self):
            from aria.ui_panels import create_settings_dialog
            create_settings_dialog(self, callbacks).exec()

        def show_services_dialog(self):
            """The header status text is clickable (Alek, 2026-10-27):
            show every service in the current report — connected
            ones with a green dot and their live status, the rest
            with a red dot and whatever state they're in — instead
            of just the 'N of M' count. Data is re-read on click so
            the dialog is never staler than the click itself."""
            try:
                subs = list(frame.strip_data().get('subsystems') or [])
            except Exception:
                subs = list(getattr(self, '_last_subsystems', []) or [])
            dialog = QDialog(self); dialog.setWindowTitle('Services'); dialog.setMinimumWidth(430)
            lay = QVBoxLayout(dialog)
            title = QLabel('SERVICES'); title.setObjectName('title'); lay.addWidget(title)
            if not subs:
                lay.addWidget(QLabel('No service report yet — still connecting.'))
            for name, status, ok in subs:
                row = QHBoxLayout()
                dot = QLabel('●')
                dot.setStyleSheet(f"color: {'#35d465' if ok else '#e5484d'};")
                row.addWidget(dot)
                row.addWidget(QLabel(str(name)), 1)
                state = QLabel(str(status))
                state.setStyleSheet('color: #9fb4c8;')
                row.addWidget(state)
                lay.addLayout(row)
            close_btn = QPushButton('Close'); close_btn.clicked.connect(dialog.close); lay.addWidget(close_btn)
            self._services_dialog = dialog
            dialog.show()
            return dialog

        def open_remote(self):
            from aria import bridge
            dialog = QDialog(self); dialog.setWindowTitle('Connect your phone'); dialog.resize(430, 520)
            lay = QVBoxLayout(dialog); label = QLabel('REMOTE CONTROL'); label.setObjectName('title'); lay.addWidget(label)
            info = QLabel('Connect your phone and laptop to the same Wi-Fi.\nScan this code with your phone camera.'); info.setWordWrap(True); lay.addWidget(info)
            qr = QLabel(); qr.setAlignment(Qt.AlignmentFlag.AlignCenter); lay.addWidget(qr)
            url = QLineEdit(); url.setReadOnly(True); lay.addWidget(url)
            note = QLabel(); note.setWordWrap(True); lay.addWidget(note)
            connected = QLabel('Waiting for your phone…'); lay.addWidget(connected)
            def refresh():
                try:
                    if bridge._BRIDGE_SERVER is None:
                        raise RuntimeError('The phone bridge is not running yet. Check the Activity tab for startup errors.')
                    link = bridge.create_pairing_url()
                    import qrcode
                    image = qrcode.make(link).convert('RGB')
                    raw = image.tobytes(); qimg = QImage(raw, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888).copy()
                    qr.setPixmap(QPixmap.fromImage(qimg).scaled(280, 280, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
                    url.setText(link)
                    note.setText('Scan to connect automatically. No token entry. This code expires after 5 minutes. Your phone stays paired for 30 days.\nOn first connection, your browser may ask you to trust the laptop’s local HTTPS certificate. If the page cannot load, check Windows Firewall and Wi-Fi client isolation.')
                except Exception as exc:
                    qr.clear(); url.clear(); note.setText(str(exc))
            refresh()
            row = QHBoxLayout(); retry = QPushButton('Refresh QR'); retry.clicked.connect(refresh); row.addWidget(retry)
            copy = QPushButton('Copy connection link'); copy.clicked.connect(lambda: QApplication.clipboard().setText(url.text())); row.addWidget(copy); lay.addLayout(row)
            forget = QPushButton('Disconnect remembered phones')
            def revoke():
                try:
                    bridge.revoke_phone_sessions(); refresh()
                    connected.setText('Phones disconnected. Scan the new code to reconnect.')
                except OSError as exc:
                    connected.setText('Could not disconnect phones: ' + str(exc))
            forget.clicked.connect(revoke); lay.addWidget(forget)
            def connection_tick():
                status = bridge.phone_connection_status()
                connected.setText('Phone connected' if status['recent_connection'] else
                                  f"{status['paired_devices']} remembered phone(s) · waiting for connection")
            timer = QTimer(dialog); timer.timeout.connect(connection_tick); timer.start(1000)
            dialog.exec()

        def open_providers(self):
            from aria.providers_panel import build_providers_dialog
            build_providers_dialog(self).exec()

        def open_audio(self):
            from aria.audio_panel import build_audio_dialog
            build_audio_dialog(self).exec()

        def _on_tab_changed(self, index):
            if self.tabs.tabText(index) == 'News':
                self.load_news()
            if self.tabs.tabText(index) == 'Memory':
                self._rebuild_memory_tab()

        def _rebuild_memory_tab(self):
            """Memory tab rows (Alek, 2026-10-27, Mark-LVI
            parity): every fact she has stored, newest first —
            **key** — value, category · date, and a ✕ per row
            that forgets exactly that fact. Rebuilt when the
            tab is opened, on Refresh, and after a forget: her
            store changes while she runs, so rows never cache."""
            try:
                from aria import memory as _mem
                rows = _mem.memory_rows_for_ui()
            except Exception:
                rows = []
            self.mem_count.setText(
                f"{len(rows)} stored facts — newest first. They "
                "live in her memory database on this machine.")
            lay = self._mem_layout
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.hide(); w.deleteLater()
                    continue
                sub = item.layout()
                if sub is not None:
                    while sub.count():
                        sw = sub.takeAt(0).widget()
                        if sw is not None:
                            sw.hide(); sw.deleteLater()
                    sub.deleteLater()
            if not rows:
                lay.addWidget(QLabel('Nothing stored yet.'))
            for r in rows:
                row = QHBoxLayout(); row.setSpacing(6)
                txt = QLabel(f"<b>{html.escape(str(r['key']))}</b> — "
                             f"{html.escape(str(r['value']))}")
                txt.setTextFormat(Qt.TextFormat.RichText)
                txt.setWordWrap(True)
                row.addWidget(txt, 1)
                meta = QLabel(f"{r['category']} · {r['updated']}")
                meta.setStyleSheet('color: #829aaf;')
                row.addWidget(meta)
                rm = QPushButton(); rm.setFixedSize(22, 22)
                rm.setIcon(_x_icon()); rm.setIconSize(QSize(13, 13))
                rm.setToolTip('Forget this')
                # Zero padding (Alek, 2026-10-27): the global
                # button padding (9px/12px) left no room for any
                # glyph on a 22px button — he saw bare outlines.
                rm.setStyleSheet('padding: 0;')
                rm.setToolTip('Forget this')
                rm.clicked.connect(
                    lambda checked=False, k=r['key']: self._forget_memory(k))
                row.addWidget(rm)
                lay.addLayout(row)
            lay.addStretch()
            self._feed_memory_maps()

        def _forget_memory(self, key):
            """The Memory tab's per-row ✕: forget exactly that
            fact, then show the store as it now stands."""
            try:
                from aria import memory as _mem
                _mem.memory_forget_key(key)
            except Exception:
                pass
            self._rebuild_memory_tab()

        def _feed_memory_maps(self):
            """Push her current memory map (rows + embedding
            vectors) into both constellation widgets — the
            Memory tab's Map view and the centerpiece."""
            try:
                from aria import memory as _mem
                rows = _mem.memory_map_points()
            except Exception:
                rows = []
            self._mem_map_rows = rows
            for w in (getattr(self, 'mem_map_tab', None),
                      getattr(self, 'mem_map_center', None)):
                if w is not None:
                    w.set_facts(rows)

        def _set_memory_mode(self, mode):
            """The Memory tab's List | Map toggle."""
            map_mode = mode == 'map'
            self.mem_list_btn.setChecked(not map_mode)
            self.mem_map_btn.setChecked(map_mode)
            self.mem_scroll.setVisible(not map_mode)
            self.mem_map_box.setVisible(map_mode)
            if map_mode:
                self._feed_memory_maps()

        def _on_map_fact_selected(self, key):
            """A star was clicked in the tab's Map view: fill
            the detail row (and arm its forget button), or
            reset it when the click landed on empty space."""
            row = next((r for r in getattr(self, '_mem_map_rows', [])
                        if r.get('key') == key), None)
            if row is None:
                self.mem_detail.setText(
                    'Drag to spin the ball. Hover a star to read it; click one to select it for forgetting.')
                self.mem_forget_btn.setEnabled(False)
                return
            self.mem_detail.setText(
                f"{row['key']} — {row['value']}  "
                f"({row['category']} · {row['updated']})")
            self.mem_forget_btn.setEnabled(True)

        def _forget_selected_map_fact(self):
            key = self.mem_map_tab.selected_key()
            if key:
                self._forget_memory(key)

        def load_news(self, force=False):
            """Fetch top headlines off-thread into the News tab.

            Lazy: opening the tab loads once, then only when stale
            (>30 min) or Refresh forces it. Failure renders the feed's
            honest empty state, never stale headlines presented as new."""
            if self._news_loading:
                return
            if (not force and self._news_loaded_at
                    and time.monotonic() - self._news_loaded_at < 1800):
                return
            self._news_loading = True
            self.views['News'].setPlainText('Loading headlines…')

            def work():
                try:
                    from aria.news_feed import (fetch_grouped,
                                                format_grouped_html)
                    self.news_result.emit(
                        format_grouped_html(fetch_grouped()))
                except Exception as exc:
                    config.log_silent('dashboard.news', exc)
                    self.news_result.emit(
                        "<p style='color:#829aaf'>Could not load "
                        "headlines.</p>")

            threading.Thread(target=work, daemon=True).start()

        def _on_news_result(self, html_text):
            self._news_loading = False
            # A failed load must not count as fresh: the next tab visit
            # retries instead of sitting on the error for 30 minutes.
            self._news_loaded_at = (time.monotonic()
                                    if 'Could not load' not in html_text
                                    else 0.0)
            self.views['News'].setHtml(html_text)

        def _set_subsystems(self, rows):
            """Refresh the SYSTEM MONITOR subsystem rows: one row per
            (name, status, ok) — coloured dot (green ok, red on
            ERROR, amber otherwise), name left, status right, full
            text on the tooltip. Rows are reused across refreshes
            (rebuilt only when the count changes) so the 1 Hz refresh
            doesn't churn widgets."""
            rows = list(rows or [])
            if len(rows) != len(self._subsystem_rows):
                for dot, name_lbl, status_lbl, row_w in self._subsystem_rows:
                    self._subsystems_layout.removeWidget(row_w)
                    row_w.deleteLater()
                self._subsystem_rows = []
                for _ in rows:
                    row_w = QWidget()
                    hl = QHBoxLayout(row_w)
                    hl.setContentsMargins(0, 0, 0, 0)
                    hl.setSpacing(6)
                    dot = QLabel('●')
                    name_lbl = QLabel(); name_lbl.setStyleSheet('font-size: 12px;')
                    status_lbl = QLabel(); status_lbl.setStyleSheet('font-size: 12px;')
                    status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
                    hl.addWidget(dot)
                    hl.addWidget(name_lbl, 1)
                    hl.addWidget(status_lbl)
                    self._subsystems_layout.insertWidget(
                        self._subsystems_layout.count() - 1, row_w)
                    self._subsystem_rows.append(
                        (dot, name_lbl, status_lbl, row_w))
            for (dot, name_lbl, status_lbl, row_w), (name, status, ok) \
                    in zip(self._subsystem_rows, rows):
                name_lbl.setText(str(name))
                status_lbl.setText(str(status))
                if ok:
                    color = '#2ee6a8'
                elif 'ERROR' in str(status).upper():
                    color = '#ff6b6b'
                else:
                    color = '#ffb84d'
                dot.setStyleSheet(f'color: {color}; font-size: 10px;')
                status_lbl.setStyleSheet(f'color: {color};')
                row_w.setToolTip(f'{name} — {status}')

        def toggle_pet(self):
            """Pet mode REPLACES the dashboard (Alek, 2026-10-27):
            entering pet mode hides this window, leaving pet mode
            (toggle again, or the pet's own menu) brings it back.
            The pet is created lazily, mirrors the dashboard's
            voice state, and follows the saved centerpiece as its
            starting form."""
            if getattr(self, '_pet', None) is None:
                from aria.pet_window import PetWindow
                self._pet = PetWindow(
                    None, provider=self._voice_state,
                    on_raise=self._raise_from_pet,
                    on_exit=self._raise_from_pet)
            if self._pet.isVisible():
                self._pet.hide()
                self.show()
            else:
                # Pet mode means ONLY the pet (Alek, 2026-10-27):
                # close any open dialog first — Settings has its
                # own Show/hide-pet button on the Pet tab, and the
                # settings window used to stay open behind the
                # hidden dashboard. Services / phone-pairing
                # dialogs are QDialog children too; same sweep.
                for dialog in self.findChildren(QDialog):
                    if dialog.isVisible():
                        dialog.close()
                self._pet.show_at_saved_pos()
                self.hide()

        def _raise_from_pet(self):
            pet = getattr(self, '_pet', None)
            if pet is not None:
                pet.hide()
            self.show()
            self.raise_()
            self.activateWindow()

        def closeEvent(self, event):
            pet = getattr(self, '_pet', None)
            if pet is not None:
                pet.hide()
            super().closeEvent(event)

        def open_customize(self):
            from aria import ui_prefs
            from aria.customize_panel import build_customize_dialog
            start = ui_prefs.get_accent()

            def preview(hex_):
                _apply_painted_accent(hex_)
                # Live-preview the chat log's ARIA colour too; a
                # Cancel restores the starting accent through this
                # same preview path, so the log follows it back.
                self._chat_accent = hex_
                if hasattr(self, '_last_chat'):
                    self._last_chat = None

            def commit(hex_):
                ui_prefs.set_accent(hex_)
                self.apply_accent(hex_)

            build_customize_dialog(self, start, preview, commit).exec()

        def set_centerpiece(self, name):
            """Apply a centerpiece choice from Settings → Customize
            (the dashboard's own Centerpiece button is gone, Alek
            2026-10-27): persist it and swap the visible face.
            'holo' without a holo widget falls back to the orb,
            matching the boot-time rule. 'map' is the memory
            constellation (Alek, 2026-10-27), fed fresh facts
            on every swap to it."""
            self._centerpiece = name if name in ('holo', 'map', 'orb3d') else 'orb'
            if self._centerpiece == 'holo' and self.holo is None:
                self._centerpiece = 'orb'
            try:
                from aria import ui_prefs
                ui_prefs.set_centerpiece(self._centerpiece)
            except Exception:
                pass
            if self._centerpiece == 'map':
                self._feed_memory_maps()
            self._show_centerpiece()

        def _show_centerpiece(self):
            """Make the stored centerpiece the visible one (unless a
            video owns the center card right now)."""
            if self.video is not None and self.video.isVisible():
                return
            self.face.hide()
            if self.holo is not None:
                self.holo.hide()
            self.mem_map_center.hide()
            self.orb3d.hide()
            if self._centerpiece == 'holo' and self.holo is not None:
                self.holo.show()
            elif self._centerpiece == 'map':
                self.mem_map_center.show()
                self._place_map_close()
            elif self._centerpiece == 'orb3d':
                self.orb3d.show()
            else:
                self.face.show()

        def _place_map_close(self):
            """Keep the memory map's ✕ in its top-right corner
            as the center card resizes."""
            host = getattr(self, 'mem_map_center', None)
            btn = getattr(self, 'map_close', None)
            if host is None or btn is None:
                return
            btn.move(max(2, host.width() - btn.width() - 8), 8)
            btn.raise_()

        def show_video(self, source, title='', muted=False):
            """Play a file/direct URL in the center card, orb swapped out.

            Returns False (and changes nothing) when QtMultimedia is
            unavailable or the source is unusable — the caller reports
            that honestly instead of the window pretending to play.
            """
            if self.video is None:
                return False
            if not self.video.show_video(source, title, muted):
                return False
            self.face.hide()
            if self.holo is not None:
                self.holo.hide()
            self.mem_map_center.hide()
            self.video.show()
            return True

        def stop_video(self):
            if self.video is not None:
                self.video.stop_video()
            self._on_video_closed()

        def _on_video_closed(self):
            if self.video is not None:
                self.video.stop_video()
                self.video.hide()
            self._show_centerpiece()

        def _composite_center_image(self, center):
            """The centre card as the phone should see it: neural
            field + centrepiece composited over the card colour
            into an OPAQUE image. (Alek, 2026-10-27 — the orb
            'looks like shit' on the mobile bridge: the old code
            converted the centrepiece's grab straight to BGR888,
            DROPPING its alpha — every soft glow pixel the
            transparent-background orb paints semi-transparent
            became full-strength cyan, flattening the waveform
            into a solid disc.)

            Built from per-widget grab()s drawn source-over —
            the capture path that has always worked on the
            laptop. The first version used QWidget.render() into
            a QPainter; on Alek's machine that produced NO frames
            at all (witnessed 07:17: the bridge orb vanished), so
            render() is not used here."""
            from PyQt6.QtGui import QColor, QPainter, QPixmap
            size = self.center_stack.size()
            base = QPixmap(size)
            base.fill(QColor('#0d1520'))
            p = QPainter(base)
            if self.neural.isVisible():
                # The field now spans the whole centre card; the
                # phone still gets just the stack's region of it.
                from PyQt6.QtCore import QPoint, QRect
                _origin = self.center_stack.mapTo(self._center_card,
                                                  QPoint(0, 0))
                p.drawPixmap(0, 0, self.neural.grab(
                    QRect(_origin, self.center_stack.size())))
            p.drawPixmap(0, 0, center.grab())
            p.end()
            return base.toImage().convertToFormat(
                QImage.Format.Format_BGR888)

        def _poll_approvals(self):
            """Show the confirm banner for the oldest pending approval.

            Polls aria/approval.py's store on the refresh cadence. The
            banner's answer goes straight to approve_token / deny_token —
            the same execution path the voice tools use — so a click and
            a spoken "yes" can never diverge. Fail-open: a broken poll
            logs silently and the voice flow still works.
            """
            try:
                from aria import approval as _approval
                entries = _approval.pending_entries()
            except Exception as exc:
                config.log_silent('dashboard.approvals', exc)
                return
            if self._confirm_busy:
                return
            if self._confirm_banner is not None:
                if any(e['token'] == self._confirm_token for e in entries):
                    self._place_confirm_banner()
                    return
                # Resolved elsewhere (voice approve/deny) or expired.
                self._hide_confirm_banner()
            if not entries:
                return
            entry = entries[0]
            try:
                from aria.confirm_banner import ConfirmBanner
                banner = ConfirmBanner(
                    f"Approve {entry['tool']}?",
                    f"{entry['desc']}\n\n"
                    f"token {entry['token']} · waiting {entry['age_s']}s · "
                    f"expires 10 min after the request",
                    self)
            except Exception as exc:
                config.log_silent('dashboard.confirm_banner', exc)
                return
            banner.answered.connect(self._on_confirm_answered)
            self._confirm_banner = banner
            self._confirm_token = entry['token']
            self._place_confirm_banner()
            banner.show()

        def _place_confirm_banner(self):
            banner = self._confirm_banner
            if banner is None:
                return
            banner.move(max(16, (self.width() - banner.width()) // 2), 84)
            banner.raise_()

        def _hide_confirm_banner(self):
            banner, self._confirm_banner = self._confirm_banner, None
            self._confirm_token = None
            if banner is not None:
                banner.hide()
                banner.deleteLater()

        def _on_confirm_answered(self, ok):
            token = self._confirm_token
            self._hide_confirm_banner()
            if token is None:
                return
            from aria import approval as _approval
            if not ok:
                _approval.deny_token(token)
                return
            # approve_token EXECUTES the stashed call, which can take
            # seconds — never on the UI thread.
            self._confirm_busy = True

            def work():
                try:
                    _approval.approve_token(token)
                except Exception as exc:
                    config.log_silent('dashboard.approve', exc)
                finally:
                    self._confirm_busy = False

            threading.Thread(target=work, daemon=True).start()

        def _voice_state(self):
            """(state, level, muted) — the render loop's triple, shared
            with the desktop pet so both show the same ARIA."""
            from aria import audio_levels, speech
            state = str(hud.CURRENT_STATE)
            mic = getattr(hud, 'MIC_LEVEL', 0.0) if time.time() - getattr(hud, 'MIC_LEVEL_AT', 0.0) < .3 else 0.0
            level = audio_levels.current() if state == 'speaking' else mic
            return state, level, not getattr(speech, 'VOICE_ENABLED', True)

        def render_once(self):
            if not callbacks.is_running(): self.close(); return
            callbacks.housekeeping()
            state, level, _muted = self._voice_state()
            if self.holo is not None and self.holo.isVisible():
                _vis = None
                if state == 'speaking':
                    from aria import audio_levels as _al
                    _vis = _al.viseme()
                self.holo.advance(state, level, _muted, viseme=_vis)
            elif getattr(self, 'orb3d', None) is not None \
                    and self.orb3d.isVisible():
                self.orb3d.advance(state, level, _muted)
            else:
                self.face.advance(state, level, _muted)
            self._sync_neural_geometry()
            self.neural.setVisible(self.video is None
                                   or not self.video.isVisible())
            if self.neural.isVisible():
                self.neural.advance(state, _muted)
            # Publish the active centerpiece to the phone; the old pixel
            # renderer is not run in the native dashboard. Limit JPEG
            # production to 8 Hz. While a video owns the center card both
            # centerpieces are hidden — do not publish a frame nobody
            # can see.
            _center = self.face if self.face.isVisible() else (
                self.holo if self.holo is not None
                and self.holo.isVisible() else (
                    self.orb3d if getattr(self, 'orb3d', None) is not None
                    and self.orb3d.isVisible() else None))
            if _center is not None and time.monotonic() - self._last_orb_publish >= .125:
                self._last_orb_publish = time.monotonic()
                try:
                    import numpy as np
                    from aria.vision import publish_face_frame
                    image = None
                    try:
                        image = self._composite_center_image(_center)
                    except Exception as exc:
                        # An ugly orb beats NO orb: fall back to
                        # the plain grab rather than publishing
                        # nothing (the 07:17 vanishing).
                        if not getattr(self, '_orb_composite_warned',
                                       False):
                            self._orb_composite_warned = True
                            config.log_silent(
                                'dashboard.orb_composite', exc)
                    if image is None:
                        image = _center.grab().toImage().convertToFormat(
                            QImage.Format.Format_BGR888)
                    ptr = image.bits(); ptr.setsize(image.sizeInBytes())
                    pixels = np.frombuffer(ptr, dtype=np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width() * 3].reshape(image.height(), image.width(), 3).copy()
                    publish_face_frame(pixels, (0, 0, image.width(), image.height()))
                except Exception as exc:
                    config.log_silent('dashboard.orb_publish', exc)
            if time.monotonic() - self._last_refresh < 0.8: return
            self._last_refresh = time.monotonic()
            from aria.briefing import latest_briefing, report_text
            briefing = latest_briefing()
            if briefing and briefing['generated_at'] != getattr(self, '_brief_stamp', None):
                self._brief_stamp = briefing['generated_at']
                self.views['Briefing'].setPlainText(report_text(briefing)); self.brief_speak.setEnabled(True)
            data = frame.strip_data()
            # The header clock (Alek, 2026-10-27): BIGGER, time
            # and date in the theme accent — the | between them
            # stays white, per his spec.
            _accent = _current_accent() or DEFAULT_ACCENT
            _now = datetime.now()
            self.clock.setText(
                f'<span style="font-size: 15pt; font-weight: 600; '
                f'color: {_accent};">{_now:%H:%M}</span>'
                f'<span style="font-size: 15pt; color: #ffffff;">'
                f' | </span>'
                f'<span style="font-size: 15pt; font-weight: 600; '
                f'color: {_accent};">{_now:%a %d %b}</span>')
            self.state.setText(str(hud.CURRENT_STATE).upper())
            self.cpu.set_value(int(data['cpu']), f"{int(data['cpu'])}%")
            self.mem.set_value(int(data['mem']), f"{int(data['mem'])}%")
            net_kbs = float(data.get('net_down_kbs', 0) or 0) + float(data.get('net_up_kbs', 0) or 0)
            net_text = f'{net_kbs:.0f} KB/s' if net_kbs < 1024 else f'{net_kbs / 1024:.1f} MB/s'
            self.net.set_value(min(100.0, net_kbs / 102.4), net_text)
            self.power.setText(f"POWER  {data['battery']}   /   UPTIME {frame._fmt_uptime(data['uptime_s'])}")
            self.plaque_engine.set_value(data['active_provider'] or 'Waiting')
            self.plaque_state.set_value(str(hud.CURRENT_STATE).upper())
            self.plaque_tasks.set_value(f"{data.get('task_active', 0)} run / {data.get('task_pending', 0)} wait")
            self._set_subsystems(data['subsystems'])
            self._last_subsystems = list(data.get('subsystems') or [])
            self.status.setText(_services_status_text(data.get('subsystems')))
            self.voice.setText('Voice on' if data['voice_enabled'] else 'Voice off')
            self._player_app_now = str(data.get('player_app') or '')
            self._sync_vol_button()
            _sel = (data.get('volume_player')
                    if self._vol_target == 'player'
                    else data.get('volume_device',
                                  data.get('volume', 0)))
            if not self.volume.isSliderDown():
                self.volume.setValue(int((_sel or 0) * 100))
            music = frame.media_data(); _track_full = music.get('title') or music.get('track') or 'Nothing playing'
            self.track.setToolTip(_track_full)
            # NOW PLAYING panel: track on one line, artist under it
            # — still inside the label's two-line cap. Telemetry
            # is SMTC-first (aria/smtc.py): real track/artist and
            # real playing-vs-paused status; the window title is
            # the fallback. The header carries the state: green
            # NOW PLAYING, amber PAUSED, plain when Spotify is
            # closed. Album art (wired 2026-10-28) renders in the
            # big tile below the audio rows; no progress bar is
            # fabricated.
            _fm = self.track.fontMetrics()
            _w = max(self.track.width(), 180)
            _trk = music.get('track') or ''
            _app = music.get('app') or ''
            # Second line: the artist when there is one; otherwise
            # the app it is playing in (a movie's "artist" is its
            # player). Spotify keeps the clean two-line look.
            _second = music.get('artist') or (
                _app if _app and _app != 'Spotify' else '')
            if _trk and _second:
                self.track.setText(
                    _fm.elidedText(_trk, Qt.TextElideMode.ElideRight, _w)
                    + '\n' + _fm.elidedText(_second, Qt.TextElideMode.ElideRight, _w))
            elif _trk:
                self.track.setText(_fm.elidedText(
                    _trk, Qt.TextElideMode.ElideRight, _w * 2))
            else:
                self.track.setText(_fm.elidedText(
                    _track_full, Qt.TextElideMode.ElideRight, _w * 2))
            _suffix = f' · {_app.upper()}' if _app and _app != 'Spotify' else ''
            if music.get('playing'):
                self.now_playing_title.setText(f'● NOW PLAYING{_suffix}')
                self.now_playing_title.setStyleSheet('color: #2ee6a8;')
            elif music.get('status') == 'paused':
                self.now_playing_title.setText(f'❚❚ PAUSED{_suffix}')
                self.now_playing_title.setStyleSheet('color: #ffb84d;')
            elif music.get('running'):
                self.now_playing_title.setText(f'● NOW PLAYING{_suffix}')
                self.now_playing_title.setStyleSheet('color: #ffb84d;')
            else:
                self.now_playing_title.setText('NOW PLAYING')
                self.now_playing_title.setStyleSheet('')
            _dur = music.get('duration_ms') or 0
            if _dur > 0:
                self.media_progress.setValue(
                    int(1000 * min(1.0, (music.get('position_ms') or 0) / _dur)))
                self.media_progress.show()
            else:
                self.media_progress.hide()
            # Album art tile: reload only when the track's art key
            # changes; rescale only when the tile's size changes —
            # the 1 s refresh must not re-decode JPEGs for fun.
            _akey = music.get('art_key')
            if _akey != self._art_key:
                self._art_key = _akey
                self._art_pixmap = None
                self._art_scaled_size = None
                _raw = music.get('art')
                if _raw:
                    _pm = QPixmap()
                    if _pm.loadFromData(_raw):
                        self._art_pixmap = _pm
                if self._art_pixmap is None:
                    self.art.setPixmap(QPixmap())
                    self.art.setText('♪')
            if self._art_pixmap is not None:
                _asz = self.art.size()
                if _asz.width() > 10 and _asz != self._art_scaled_size:
                    self._art_scaled_size = _asz
                    self.art.setText('')
                    self.art.setPixmap(self._art_pixmap.scaled(
                        _asz, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
            # No caption under the artwork (Alek, 2026-10-28:
            # "I don't need the text under the album artwork") —
            # the tile takes the freed height too.
            _entries = frame.chat_entries(100)
            chat = '\n\n'.join(f'{ts}  {sender}\n{text}' for ts, sender, text in _entries)
            if chat != self._last_chat:
                self._last_chat = chat
                if _entries:
                    self.views['Conversation'].setHtml(chat_html(
                        _entries,
                        accent=getattr(self, '_chat_accent', None)))
                else:
                    self.views['Conversation'].setPlainText('Your conversation with ARIA appears here.')
                self.views['Conversation'].verticalScrollBar().setValue(self.views['Conversation'].verticalScrollBar().maximum())
            self._poll_vision_pip()
            _events = frame.activity_events(100)
            activity = '\n\n'.join(f'{ts}  {level}\n{message}' for ts, level, message, meta in _events)
            if activity != self._last_activity:
                self._last_activity = activity
                self.views['Activity'].setHtml(activity_html(_events))
            # Fleet lives INSIDE the Tasks tab (Alek, 2026-10-27:
            # no new tab) — runtime state only, above the existing
            # delegated/scheduled lists.
            try:
                from aria import fleet as _fleet_mod
                fleet_text = _fleet_mod.render_text(_fleet_mod.snapshot())
            except Exception:
                fleet_text = ''
            try:
                from aria.agent.taskqueue import get_queue
                from aria.scheduler import sched_list
                tasks = get_queue().get_all_statuses()
                text = '\n\n'.join(f"{t.get('status', '?').upper()}  {t.get('goal', '')}\n{t.get('error') or t.get('result') or ''}" for t in tasks)
                scheduled = '\n'.join(str(row) for row in sched_list())
                body = (text or 'No delegated tasks.') + '\n\nSCHEDULED\n' + (scheduled or 'No scheduled tasks.')
                self.views['Tasks'].setPlainText(
                    (fleet_text + '\n\n———\n\n' + body) if fleet_text else body)
            except Exception as exc:
                self.views['Tasks'].setPlainText(
                    (fleet_text + '\n\n' if fleet_text else '')
                    + 'Task status unavailable: ' + str(exc))
            self._poll_approvals()

        def closeEvent(self, event):
            callbacks.ptt_release(); callbacks.request_stop(); super().closeEvent(event)

    return Dashboard()


def dashboard_enabled():
    """Explicit rollback without changing the supported UI host setting."""
    return os.environ.get('ARIA_UI_LAYOUT', 'dashboard').strip().lower() != 'classic'
