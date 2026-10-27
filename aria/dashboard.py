"""Native Qt command center for ARIA's existing agent and desktop services.

No Mark source is copied. Qt stays lazy so the classic host remains available.
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


def create_dashboard(callbacks):
    from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
    from PyQt6.QtGui import QImage, QPixmap
    from PyQt6.QtWidgets import (QMainWindow, QWidget, QFrame, QLabel, QPushButton,
                                QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget,
                                QTextBrowser, QLineEdit, QSlider,
                                QFileDialog, QDialog, QApplication, QListWidget, QListWidgetItem)
    from aria import frame, hud, config
    from aria.resonance import ResonanceOrb
    from aria.metric_bar import MetricBar, StatusPlaque

    class Dashboard(QMainWindow):
        panel_result = pyqtSignal(str, object)
        email_result = pyqtSignal(int, object)

        def __init__(self):
            super().__init__()
            self.setWindowTitle('A.R.I.A. | Command Center')
            self.resize(1440, 900)
            self.setMinimumSize(1080, 720)
            self.setStyleSheet(STYLE)
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
            self.clock = QLabel(); self.clock.setFixedWidth(190); head.addWidget(self.clock); layout.addLayout(head)
            body = QSplitter(Qt.Orientation.Horizontal); layout.addWidget(body, 1)
            left, ll = self.card('SYSTEM MONITOR'); left.setMinimumWidth(220); left.setMaximumWidth(290)
            self.plaque_engine = StatusPlaque('ENGINE'); ll.addWidget(self.plaque_engine)
            self.plaque_state = StatusPlaque('STATE'); ll.addWidget(self.plaque_state)
            self.plaque_tasks = StatusPlaque('TASKS'); ll.addWidget(self.plaque_tasks)
            self.cpu = MetricBar('CPU'); ll.addWidget(self.cpu)
            self.mem = MetricBar('MEM'); ll.addWidget(self.mem)
            self.net = MetricBar('NET'); ll.addWidget(self.net)
            self.power = QLabel(); ll.addWidget(self.power)
            self.subsystems = QLabel(); self.subsystems.setWordWrap(True); ll.addWidget(self.subsystems)
            ll.addStretch()
            title = QLabel('NOW PLAYING'); title.setObjectName('title'); ll.addWidget(title)
            self.track = QLabel('Spotify is not running'); self.track.setWordWrap(True); ll.addWidget(self.track)
            media = QHBoxLayout()
            for name, action in [('Prev', 'previous'), ('Play', 'play_pause'), ('Next', 'next')]:
                b = QPushButton(name); b.clicked.connect(lambda checked=False, a=action: hud._dispatch_spotify_action(a)); media.addWidget(b)
            ll.addLayout(media)
            ll.addWidget(QLabel('SYSTEM VOLUME'))
            self.volume = QSlider(Qt.Orientation.Horizontal); self.volume.setRange(0, 100)
            self.volume.sliderMoved.connect(lambda value: hud.set_hud_volume(value / 100)); ll.addWidget(self.volume)
            remote = QPushButton('REMOTE CONTROL'); remote.setObjectName('primary'); remote.clicked.connect(self.open_remote); ll.addWidget(remote)
            body.addWidget(left)
            center, cl = self.card('ARIA / LIVE')
            self.face = ResonanceOrb(); cl.addWidget(self.face, 1)
            self._last_orb_publish = 0.0
            self.state = QLabel('IDLE'); self.state.setAlignment(Qt.AlignmentFlag.AlignCenter); self.state.setObjectName('brand'); cl.addWidget(self.state)
            self.subtitle = QLabel(); self.subtitle.setTextFormat(Qt.TextFormat.PlainText); self.subtitle.setWordWrap(True); self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter); cl.addWidget(self.subtitle)
            controls = QHBoxLayout()
            self.mic = QPushButton('Hold to talk'); self.mic.pressed.connect(callbacks.ptt_press); self.mic.released.connect(callbacks.ptt_release); controls.addWidget(self.mic)
            self.voice = QPushButton('Voice on'); self.voice.clicked.connect(frame.toggle_voice); controls.addWidget(self.voice)
            stop = QPushButton('Stop speaking'); stop.clicked.connect(callbacks.interrupt); controls.addWidget(stop); cl.addLayout(controls)
            self.whisper = QPushButton('Whisper off'); self.whisper.clicked.connect(callbacks.toggle_whisper); cl.addWidget(self.whisper)
            body.addWidget(center)
            right, rl = self.card('WORKSPACE'); right.setMinimumWidth(340)
            self.tabs = QTabWidget(); self.views = {}
            for name in ('Conversation', 'Inbox', 'Calendar', 'Tasks', 'Activity'):
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
                if name == 'Calendar':
                    btn = QPushButton('Refresh ' + name.lower()); btn.clicked.connect(lambda checked=False, n=name: self.load_panel(n)); pl.addWidget(btn)
                    view.setPlainText('Select Refresh to load your connected ' + name.lower() + '.')
                pl.addWidget(view); self.tabs.addTab(page, name)
            rl.addWidget(self.tabs, 1)
            row = QHBoxLayout()
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
                    if name == 'Inbox':
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

        def commands(self):
            from aria.tools.schemas import COMMAND_GUIDE
            dialog = QDialog(self); dialog.setWindowTitle('ARIA commands'); dialog.resize(700, 600)
            lay = QVBoxLayout(dialog); search = QLineEdit(); search.setPlaceholderText('Search commands…'); lay.addWidget(search)
            view = QTextBrowser(); lay.addWidget(view)
            def show(query=''):
                view.setPlainText('\n\n'.join(f'{category} / {tool}\nSay: {example}' for category, tool, example in COMMAND_GUIDE if query.lower() in f'{category} {tool} {example}'.lower()))
            search.textChanged.connect(show); show(); dialog.exec()

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

        def render_once(self):
            if not callbacks.is_running(): self.close(); return
            callbacks.housekeeping()
            from aria import audio_levels, speech
            state = str(hud.CURRENT_STATE)
            mic = getattr(hud, 'MIC_LEVEL', 0.0) if time.time() - getattr(hud, 'MIC_LEVEL_AT', 0.0) < .3 else 0.0
            level = audio_levels.current() if state == 'speaking' else mic
            self.face.advance(state, level, not getattr(speech, 'VOICE_ENABLED', True))
            # Publish the same orb to the phone; the old pixel renderer is not
            # run in the native dashboard. Limit JPEG production to 8 Hz.
            if time.monotonic() - self._last_orb_publish >= .125:
                self._last_orb_publish = time.monotonic()
                try:
                    import numpy as np
                    from aria.vision import publish_face_frame
                    image = self.face.grab().toImage().convertToFormat(QImage.Format.Format_BGR888)
                    ptr = image.bits(); ptr.setsize(image.sizeInBytes())
                    pixels = np.frombuffer(ptr, dtype=np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width() * 3].reshape(image.height(), image.width(), 3).copy()
                    publish_face_frame(pixels, (0, 0, image.width(), image.height()))
                except Exception as exc:
                    config.log_silent('dashboard.orb_publish', exc)
            if time.monotonic() - self._last_refresh < 0.8: return
            self._last_refresh = time.monotonic()
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
            self.subsystems.setText('\n'.join(f'{name}   {status}' for name, status, ok in data['subsystems']))
            self.voice.setText('Voice on' if data['voice_enabled'] else 'Voice off')
            self.whisper.setText('Whisper on' if hud.WHISPER_MODE else 'Whisper off')
            if not self.volume.isSliderDown(): self.volume.setValue(int(data['volume'] * 100))
            music = frame.spotify_data(); self.track.setText(music.get('title') or music.get('track') or 'Spotify is not running')
            chat = '\n\n'.join(f'{ts}  {sender}\n{text}' for ts, sender, text in frame.chat_entries(100))
            if chat != self._last_chat:
                self._last_chat = chat; self.views['Conversation'].setPlainText(chat or 'Your conversation with ARIA appears here.'); self.views['Conversation'].verticalScrollBar().setValue(self.views['Conversation'].verticalScrollBar().maximum())
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
