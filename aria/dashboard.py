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
    from PyQt6.QtCore import Qt, pyqtSignal
    from PyQt6.QtGui import QImage, QPixmap
    from PyQt6.QtWidgets import (QMainWindow, QWidget, QFrame, QLabel, QPushButton,
                                QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget,
                                QTextBrowser, QLineEdit, QProgressBar, QSlider,
                                QFileDialog, QDialog, QApplication)
    from aria import frame, hud, config

    class Dashboard(QMainWindow):
        panel_result = pyqtSignal(str, str)

        def __init__(self):
            super().__init__()
            self.setWindowTitle('A.R.I.A. | Command Center')
            self.resize(1440, 900)
            self.setMinimumSize(1080, 720)
            self.setStyleSheet(STYLE)
            self.setAcceptDrops(True)
            self._loading = set()
            self._last_chat = None
            self._last_activity = None
            self._last_refresh = 0.0
            root = QWidget(); self.setCentralWidget(root)
            layout = QVBoxLayout(root); layout.setContentsMargins(20, 16, 20, 16); layout.setSpacing(16)
            head = QHBoxLayout()
            brand = QLabel('A.R.I.A.'); brand.setObjectName('brand'); head.addWidget(brand)
            head.addWidget(QLabel('ADAPTIVE ROBOTIC INTELLIGENCE AGENT')); head.addStretch()
            self.status = QLabel('Connecting to services'); self.status.setFixedWidth(200); head.addWidget(self.status)
            self.clock = QLabel(); self.clock.setFixedWidth(190); head.addWidget(self.clock); layout.addLayout(head)
            body = QSplitter(Qt.Orientation.Horizontal); layout.addWidget(body, 1)
            left, ll = self.card('SYSTEM MONITOR'); left.setMinimumWidth(220); left.setMaximumWidth(290)
            self.cpu = self.meter(ll, 'PROCESSOR'); self.mem = self.meter(ll, 'MEMORY')
            self.power = QLabel(); ll.addWidget(self.power)
            self.engine = QLabel(); self.engine.setMinimumHeight(50); self.engine.setWordWrap(True); ll.addWidget(self.engine)
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
            self.face = QLabel(); self.face.setMinimumSize(320, 380); self.face.setAlignment(Qt.AlignmentFlag.AlignCenter); cl.addWidget(self.face, 1)
            self.state = QLabel('IDLE'); self.state.setAlignment(Qt.AlignmentFlag.AlignCenter); self.state.setObjectName('brand'); cl.addWidget(self.state)
            self.subtitle = QLabel(); self.subtitle.setWordWrap(True); self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter); cl.addWidget(self.subtitle)
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
                if name in ('Inbox', 'Calendar'):
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
            self.entry.setFocus()

        def card(self, title):
            card = QFrame(); card.setObjectName('card'); lay = QVBoxLayout(card); lay.setContentsMargins(16, 16, 16, 16); lay.setSpacing(14)
            label = QLabel(title); label.setObjectName('title'); lay.addWidget(label)
            return card, lay

        def meter(self, layout, name):
            label = QLabel(name); layout.addWidget(label)
            bar = QProgressBar(); bar.setRange(0, 100); bar.setTextVisible(False); layout.addWidget(bar)
            return label, bar

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
            self._loading.add(name); self.views[name].setPlainText('Loading…')
            def load():
                try:
                    from aria.tools import builtins
                    result = builtins.tool_read_email(limit=15) if name == 'Inbox' else builtins.tool_check_calendar(days=7)
                except Exception as exc:
                    result = 'Could not load ' + name.lower() + ': ' + str(exc)
                self.panel_result.emit(name, config.redact(str(result)))
            threading.Thread(target=load, daemon=True).start()

        def panel_finished(self, name, result):
            self._loading.discard(name); self.views[name].setPlainText(result)

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
                    note.setText('This QR code works once and expires after 5 minutes. Keep it private.\nOn first connection, your browser may ask you to trust the laptop’s local HTTPS certificate. If the page cannot load, check Windows Firewall and Wi-Fi client isolation.')
                except Exception as exc:
                    qr.clear(); url.clear(); note.setText(str(exc))
            refresh()
            row = QHBoxLayout(); retry = QPushButton('Refresh'); retry.clicked.connect(refresh); row.addWidget(retry)
            copy = QPushButton('Copy connection link'); copy.clicked.connect(lambda: QApplication.clipboard().setText(url.text())); row.addWidget(copy); lay.addLayout(row)
            dialog.exec()

        def render_once(self):
            if not callbacks.is_running(): self.close(); return
            canvas = callbacks.render_frame(); callbacks.housekeeping()
            # Existing rendering continues publishing phone face frames. Only the
            # avatar/waveform region is displayed in the native desktop layout.
            crop = canvas[66:500, 340:940].copy()
            image = QImage(crop.data, crop.shape[1], crop.shape[0], int(crop.strides[0]), QImage.Format.Format_BGR888).copy()
            self.face.setPixmap(QPixmap.fromImage(image).scaled(self.face.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            if time.monotonic() - self._last_refresh < 0.8: return
            self._last_refresh = time.monotonic()
            data = frame.strip_data()
            self.clock.setText(datetime.now().strftime('%H:%M  |  %a %d %b'))
            self.state.setText(str(hud.CURRENT_STATE).upper()); self.subtitle.setText(str(hud.SUBTITLE_TEXT))
            for key, meter in [('cpu', self.cpu), ('mem', self.mem)]:
                value = int(data[key]); meter[0].setText(('PROCESSOR' if key == 'cpu' else 'MEMORY') + f'   {value}%'); meter[1].setValue(value)
            self.power.setText(f"POWER  {data['battery']}   /   UPTIME {frame._fmt_uptime(data['uptime_s'])}")
            self.engine.setText('ENGINE\n' + (data['active_provider'] or 'Waiting for first response'))
            self.subsystems.setText('\n'.join(f'{name}   {status}' for name, status, ok in data['subsystems']))
            self.status.setText(f"{data['task_active']} active / {data['scheduled']} scheduled")
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

        def closeEvent(self, event):
            callbacks.ptt_release(); callbacks.request_stop(); super().closeEvent(event)

    return Dashboard()


def dashboard_enabled():
    """Explicit rollback without changing the supported UI host setting."""
    return os.environ.get('ARIA_UI_LAYOUT', 'dashboard').strip().lower() != 'classic'
