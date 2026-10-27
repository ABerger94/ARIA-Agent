"""Native Qt command center for ARIA's existing agent and desktop services.

Qt stays lazy so the classic host remains available. The orb adapter retains its own attribution.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime

STYLE = """
QWidget { background: #080d14; color: #dce6ef; font: 12px 'Segoe UI'; }
QFrame#card { background: #0d1520; border: 1px solid #1b3042; border-radius: 10px; }
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


# Conversation colours (Alek, 2026-10-27): her words and his in
# different colours in the Conversation tab, not all-white text.
CHAT_COLORS = {'aria': ('#54d9e8', '#c9f4fa'), 'user': ('#ffb84d', '#ffe3b3')}


def chat_html(entries):
    """Render chat entries [(ts, sender, text)] as coloured HTML:
    ARIA in the accent cyan family, the user in warm amber,
    timestamps dim. Text is escaped — chat content is not markup."""
    import html as _html
    parts = []
    for ts, sender, text in entries:
        who = 'aria' if 'aria' in str(sender).casefold() else 'user'
        name_col, text_col = CHAT_COLORS[who]
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
                     'aria.neural_field'):
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
    from PyQt6.QtGui import QImage, QPixmap, QColor, QShortcut, QKeySequence
    from PyQt6.QtWidgets import (QMainWindow, QWidget, QFrame, QLabel, QPushButton,
                                QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget,
                                QTextBrowser, QLineEdit, QSlider, QGridLayout,
                                QFileDialog, QDialog, QApplication, QListWidget, QListWidgetItem)
    from aria import frame, hud, config
    from aria.resonance import ResonanceOrb
    from aria.neural_field import NeuralField
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
            root = QWidget(); self.setCentralWidget(root)
            layout = QVBoxLayout(root); layout.setContentsMargins(20, 16, 20, 16); layout.setSpacing(16)
            head = QHBoxLayout()
            brand = QLabel('A.R.I.A.'); brand.setObjectName('brand'); head.addWidget(brand)
            head.addWidget(QLabel('ADAPTIVE ROBOTIC INTELLIGENCE AGENT')); head.addStretch()
            self.status = QPushButton('Connecting to services'); self.status.setFixedWidth(200); self.status.setFlat(True); self.status.setCursor(Qt.CursorShape.PointingHandCursor); self.status.setToolTip('Click for service details'); self.status.setStyleSheet('QPushButton { text-align: right; border: none; background: transparent; } QPushButton:hover { text-decoration: underline; }'); self.status.clicked.connect(self.show_services_dialog); head.addWidget(self.status)
            self.clock = QLabel(); self.clock.setFixedWidth(190); head.addWidget(self.clock)
            self.settings_button = QPushButton('Settings'); self.settings_button.clicked.connect(self.settings); head.addWidget(self.settings_button)
            self.customize_button = QPushButton('Customize'); self.customize_button.clicked.connect(self.open_customize_overlay); head.addWidget(self.customize_button)
            self.extensions_button = QPushButton('Extensions'); self.extensions_button.clicked.connect(self.open_extensions_overlay); head.addWidget(self.extensions_button)
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
            self.track = QLabel('Spotify is not running'); self.track.setWordWrap(True)
            # Two lines, ever (Alek, 2026-10-27: a long Spotify title
            # wrapped unbounded, the column overflowed, and the NOW
            # PLAYING text overran the subsystems). The refresh elides
            # to fit these two lines; the full title is the tooltip.
            self.track.setMaximumHeight(
                self.track.fontMetrics().lineSpacing() * 2 + 6)
            ll.addWidget(self.track)
            media = QHBoxLayout()
            for name, action in [('Prev', 'previous'), ('Play', 'play_pause'), ('Next', 'next')]:
                b = QPushButton(name); b.clicked.connect(lambda checked=False, a=action: hud._dispatch_spotify_action(a)); media.addWidget(b)
            ll.addLayout(media)
            _vol = QHBoxLayout(); _vol.addWidget(QLabel('SYSTEM VOLUME'))
            self.volume = QSlider(Qt.Orientation.Horizontal); self.volume.setRange(0, 100)
            self.volume.sliderMoved.connect(lambda value: hud.set_hud_volume(value / 100)); _vol.addWidget(self.volume, 1)
            ll.addLayout(_vol)
            # REMOTE CONTROL used to sit here; it lives in Settings
            # (Connections tab, 'Open Remote Control') now (Alek,
            # 2026-10-27). open_remote() stays for Settings' use.
            ll.addStretch()
            body.addWidget(left)
            center, cl = self.card('ARIA / LIVE')
            # The neuron field (Mark-LVI's HudCanvas layer, ported
            # to aria/neural_field.py — Alek, 2026-10-27) sits in a
            # grid stack UNDER the centrepiece; the orb / holo head
            # run with transparent backgrounds over it, exactly as
            # they already do on the desktop pet.
            self.center_stack = QWidget()
            _stack = QGridLayout(self.center_stack)
            _stack.setContentsMargins(0, 0, 0, 0)
            self.neural = NeuralField(); _stack.addWidget(self.neural, 0, 0)
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
            self._centerpiece = _centerpiece0 if self.holo is not None else 'orb'
            if self.holo is not None:
                self.holo.hide(); _stack.addWidget(self.holo, 0, 0)
            cl.addWidget(self.center_stack, 1)
            self._last_orb_publish = 0.0
            self._overlay = None
            self._overlay_card = None
            self.state = QLabel('IDLE'); self.state.setAlignment(Qt.AlignmentFlag.AlignCenter); self.state.setObjectName('brand'); cl.addWidget(self.state)
            self.subtitle = QLabel(); self.subtitle.setTextFormat(Qt.TextFormat.PlainText); self.subtitle.setWordWrap(True); self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter); cl.addWidget(self.subtitle)
            controls = QHBoxLayout()
            self.mic = QPushButton('Hold to talk'); self.mic.pressed.connect(callbacks.ptt_press); self.mic.released.connect(callbacks.ptt_release); controls.addWidget(self.mic)
            self.voice = QPushButton('Voice on'); self.voice.clicked.connect(frame.toggle_voice); controls.addWidget(self.voice)
            stop = QPushButton('Stop speaking'); stop.clicked.connect(callbacks.interrupt); controls.addWidget(stop); cl.addLayout(controls)
            self.whisper = QPushButton('Whisper off'); self.whisper.clicked.connect(callbacks.toggle_whisper); cl.addWidget(self.whisper)
            self.centerpiece_btn = QPushButton('Centerpiece: Orb'); self.centerpiece_btn.clicked.connect(self._toggle_centerpiece); cl.addWidget(self.centerpiece_btn)
            if self.holo is None: self.centerpiece_btn.setEnabled(False)
            body.addWidget(center)
            right, rl = self.card('WORKSPACE'); right.setMinimumWidth(340)
            self.tabs = QTabWidget(); self.views = {}
            for name in ('Conversation', 'Inbox', 'Calendar', 'Tasks', 'Activity', 'Briefing', 'News'):
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
                self.views[name].setPlainText(report_text(result))
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

        def draft_command(self, text):
            self.entry.setText(text); self.entry.setFocus()

        def commands(self):
            from aria.ui_panels import create_commands_dialog
            create_commands_dialog(self, self.draft_command).exec()

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
                    from aria.news_feed import (fetch_headlines,
                                                format_headlines_html)
                    self.news_result.emit(
                        format_headlines_html(fetch_headlines()))
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

            def commit(hex_):
                ui_prefs.set_accent(hex_)
                _apply_painted_accent(hex_)
                self.setStyleSheet(style_for_accent(hex_))

            build_customize_dialog(self, start, preview, commit).exec()

        # ── in-window overlays (Mark-LVI floated its panels over
        # the HUD; Alek, 2026-10-27 — 'I want it all') ────────────
        def _show_overlay(self, title, content):
            self._close_overlay()
            overlay = QWidget(self)
            overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            overlay.setStyleSheet('background: rgba(2, 8, 14, 190);')
            lay = QVBoxLayout(overlay)
            lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card = QFrame()
            card.setObjectName('card')
            cl = QVBoxLayout(card)
            head = QHBoxLayout()
            t = QLabel(title)
            t.setObjectName('title')
            head.addWidget(t, 1)
            x = QPushButton('✕')
            x.setFixedWidth(40)
            x.clicked.connect(self._close_overlay)
            head.addWidget(x)
            cl.addLayout(head)
            cl.addWidget(content)
            lay.addWidget(card)
            overlay.setGeometry(self.rect())
            overlay.show()
            overlay.raise_()
            overlay.installEventFilter(self)
            self._overlay = overlay
            self._overlay_card = card
            return overlay

        def _close_overlay(self):
            ov = getattr(self, '_overlay', None)
            if ov is not None:
                ov.hide()
                ov.deleteLater()
                self._overlay = None
                self._overlay_card = None

        def eventFilter(self, obj, event):
            if obj is getattr(self, '_overlay', None) \
                    and event.type() == event.Type.MouseButtonPress:
                self._close_overlay()
                return True
            return super().eventFilter(obj, event)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            ov = getattr(self, '_overlay', None)
            if ov is not None:
                ov.setGeometry(self.rect())

        def keyPressEvent(self, event):
            if event.key() == Qt.Key.Key_Escape \
                    and getattr(self, '_overlay', None) is not None:
                self._close_overlay()
                return
            super().keyPressEvent(event)

        def open_customize_overlay(self):
            if getattr(self, '_overlay', None) is not None:
                self._close_overlay()
                return
            from aria import ui_prefs
            from aria.customize_panel import build_customize_widget
            start = ui_prefs.get_accent()

            def preview(hex_):
                _apply_painted_accent(hex_)

            def commit(hex_):
                ui_prefs.set_accent(hex_)
                _apply_painted_accent(hex_)
                self.setStyleSheet(style_for_accent(hex_))

            w = build_customize_widget(self, start, preview, commit,
                                       self._close_overlay)
            self._show_overlay('CUSTOMIZE — UI ACCENT', w)

        def open_extensions_overlay(self):
            if getattr(self, '_overlay', None) is not None:
                self._close_overlay()
                return
            self._show_overlay('EXTENSIONS', self._extensions_widget())

        def _extensions_widget(self):
            """The plugin surface this build actually has: MCP
            servers (aria/mcp.py) over the built-in tool catalog.
            Read-only — management stays with ARIA's mcp_* tools."""
            w = QWidget()
            lay = QVBoxLayout(w)
            try:
                from aria import mcp
                servers = mcp.get_servers()
            except Exception:
                servers = {}
            if servers:
                for name, cfg in sorted(servers.items()):
                    kind = cfg.get('transport', 'stdio')
                    target = cfg.get('url') or cfg.get('command', '')
                    lay.addWidget(QLabel(f'● {name} — {kind} — {target}'))
            else:
                lay.addWidget(QLabel('No MCP servers configured.'))
            try:
                from aria.tools import dispatch as _dispatch
                lay.addWidget(QLabel(
                    f'{len(_dispatch.tool_names())} built-in tools registered.'))
            except Exception:
                pass
            hint = QLabel('Manage servers by asking ARIA — '
                          '"connect the filesystem MCP server", '
                          '"list MCP servers", "remove the X server".')
            hint.setWordWrap(True)
            lay.addWidget(hint)
            return w

        def _toggle_centerpiece(self):
            self._centerpiece = 'holo' if self._centerpiece == 'orb' else 'orb'
            try:
                from aria import ui_prefs
                ui_prefs.set_centerpiece(self._centerpiece)
            except Exception:
                pass
            self._show_centerpiece()

        def _show_centerpiece(self):
            """Make the stored centerpiece the visible one (unless a
            video owns the center card right now)."""
            self.centerpiece_btn.setText(
                'Centerpiece: ' + ('Holo' if self._centerpiece == 'holo'
                                   else 'Orb'))
            if self.video is not None and self.video.isVisible():
                return
            if self._centerpiece == 'holo' and self.holo is not None:
                self.face.hide(); self.holo.show()
            else:
                if self.holo is not None:
                    self.holo.hide()
                self.face.show()

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
                p.drawPixmap(0, 0, self.neural.grab())
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
            else:
                self.face.advance(state, level, _muted)
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
                and self.holo.isVisible() else None)
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
            self.clock.setText(datetime.now().strftime('%H:%M  |  %a %d %b'))
            self.state.setText(str(hud.CURRENT_STATE).upper()); self.subtitle.setText(str(hud.SUBTITLE_TEXT))
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
            self.whisper.setText('Whisper on' if hud.WHISPER_MODE else 'Whisper off')
            if not self.volume.isSliderDown(): self.volume.setValue(int(data['volume'] * 100))
            music = frame.spotify_data(); _track_full = music.get('title') or music.get('track') or 'Spotify is not running'
            self.track.setToolTip(_track_full)
            # NOW PLAYING panel: track on one line, artist under it
            # (the telemetry parses both from the window title) —
            # still inside the label's two-line cap. The header dot
            # carries the state: green playing, amber open-but-
            # paused, plain when Spotify is closed. (No art or
            # progress: the window-title source has neither, and a
            # fabricated bar would be a lie.)
            _fm = self.track.fontMetrics()
            _w = max(self.track.width(), 180)
            _trk, _art = music.get('track') or '', music.get('artist') or ''
            if _trk and _art:
                self.track.setText(
                    _fm.elidedText(_trk, Qt.TextElideMode.ElideRight, _w)
                    + '\n' + _fm.elidedText(_art, Qt.TextElideMode.ElideRight, _w))
            else:
                self.track.setText(_fm.elidedText(
                    _track_full, Qt.TextElideMode.ElideRight, _w * 2))
            if music.get('playing'):
                self.now_playing_title.setText('● NOW PLAYING')
                self.now_playing_title.setStyleSheet('color: #2ee6a8;')
            elif music.get('running'):
                self.now_playing_title.setText('● NOW PLAYING')
                self.now_playing_title.setStyleSheet('color: #ffb84d;')
            else:
                self.now_playing_title.setText('NOW PLAYING')
                self.now_playing_title.setStyleSheet('')
            _entries = frame.chat_entries(100)
            chat = '\n\n'.join(f'{ts}  {sender}\n{text}' for ts, sender, text in _entries)
            if chat != self._last_chat:
                self._last_chat = chat
                if _entries:
                    self.views['Conversation'].setHtml(chat_html(_entries))
                else:
                    self.views['Conversation'].setPlainText('Your conversation with ARIA appears here.')
                self.views['Conversation'].verticalScrollBar().setValue(self.views['Conversation'].verticalScrollBar().maximum())
            activity = '\n\n'.join(f'{ts}  {level}\n{message}' for ts, level, message, meta in frame.activity_events(100))
            if activity != self._last_activity:
                self._last_activity = activity; self.views['Activity'].setPlainText(activity)
            try:
                from aria.agent.taskqueue import get_queue
                from aria.scheduler import sched_list
                tasks = get_queue().get_all_statuses()
                text = '\n\n'.join(f"{t.get('status', '?').upper()}  {t.get('goal', '')}\n{t.get('error') or t.get('result') or ''}" for t in tasks)
                scheduled = '\n'.join(str(row) for row in sched_list())
                self.views['Tasks'].setPlainText((text or 'No delegated tasks.') + '\n\nSCHEDULED\n' + (scheduled or 'No scheduled tasks.'))
            except Exception as exc:
                self.views['Tasks'].setPlainText('Task status unavailable: ' + str(exc))
            self._poll_approvals()

        def closeEvent(self, event):
            callbacks.ptt_release(); callbacks.request_stop(); super().closeEvent(event)

    return Dashboard()


def dashboard_enabled():
    """Explicit rollback without changing the supported UI host setting."""
    return os.environ.get('ARIA_UI_LAYOUT', 'dashboard').strip().lower() != 'classic'
