"""Shell Home — the ARIA OS home screen panel (2026-10-30).

The front door of Shell Mode: a clock + greeting header, a
searchable list of his real Start Menu apps, and the command bar
— type (or later, say) what you want; an unambiguous app name
launches it, everything else goes to ARIA as a normal turn
through the dashboard's existing submit path, approval gates and
all. The **Exit to Windows** button is always visible, bottom
right: one click and the shell steps aside — v1 never replaces
explorer.exe, so Windows was never gone, just covered.

All routing logic lives in aria/shell_home.py and enumeration in
aria/app_launcher.py (both pure); this file is only the paint.
Callbacks are injected: on_launch(app), on_command(text),
on_exit() — the dashboard wires them; tests and screenshots wire
recorders.
"""
from __future__ import annotations

from datetime import datetime

from aria import app_launcher, shell_home

try:
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtWidgets import (
        QCheckBox,
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
QLineEdit { background: #101f30; color: #eaf6ff; border: 1px solid #1b3042;
            border-radius: 6px; padding: 9px 12px; font-size: 15px; }
QListWidget { background: #0e1c2c; color: #dceef7; border: 1px solid #1b3042;
              border-radius: 6px; font-size: 15px; }
QListWidget::item { padding: 7px 10px; }
QListWidget::item:selected { background: #17435a; }
QPushButton#shellExit { background: #12303f; color: #eaf6ff;
    border: 1px solid #2b5873; border-radius: 6px;
    padding: 10px 18px; font-size: 14px; }
QPushButton#shellExit:hover { background: #17435a; }
"""


class ShellPanel(QWidget):
    """The Shell Home screen. See the module docstring."""

    def __init__(self, parent=None, apps=None, apps_provider=None,
                 on_launch=None, on_command=None, on_exit=None,
                 now_provider=None, shell_mode=None,
                 on_shell_mode=None):
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

        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 28, 36, 24)
        lay.setSpacing(10)

        self.clock_label = QLabel("--:--")
        self.clock_label.setObjectName("shellClock")
        lay.addWidget(self.clock_label)
        self.greeting_label = QLabel("")
        self.greeting_label.setObjectName("shellGreeting")
        lay.addWidget(self.greeting_label)
        self.date_label = QLabel("")
        self.date_label.setObjectName("shellDate")
        lay.addWidget(self.date_label)
        lay.addSpacing(10)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps…")
        self.search.setObjectName("shellSearch")
        self.search.textChanged.connect(self._refilter)
        lay.addWidget(self.search)

        self.app_list = QListWidget()
        self.app_list.setObjectName("shellAppList")
        self.app_list.itemActivated.connect(self._activate_item)
        self.app_list.itemDoubleClicked.connect(self._activate_item)
        lay.addWidget(self.app_list, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("shellCount")
        lay.addWidget(self.count_label)

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
        lay.addWidget(self.shell_mode_box)

        bottom = QHBoxLayout()
        self.command = QLineEdit()
        self.command.setPlaceholderText(
            "Tell ARIA something, or type an app name to launch it…")
        self.command.setObjectName("shellCommand")
        self.command.returnPressed.connect(self._submit_command)
        bottom.addWidget(self.command, 1)
        self.exit_btn = QPushButton(shell_home.EXIT_LABEL)
        self.exit_btn.setObjectName("shellExit")
        self.exit_btn.setToolTip(
            "Leave Shell Mode and return to the Windows desktop")
        self.exit_btn.clicked.connect(self._exit)
        bottom.addWidget(self.exit_btn)
        lay.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_clock)
        self._timer.start(20000)
        if self._apps_provider is not None and apps is None:
            self.refresh_apps()
        self.refresh_clock()
        self._refilter()

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

    # -- interactions ------------------------------------------
    def _refilter(self):
        query = self.search.text() if hasattr(self, "search") else ""
        results = app_launcher.search_apps(self._apps, query,
                                           limit=200)
        self.app_list.clear()
        for app in results:
            item = QListWidgetItem(app["name"])
            item.setData(Qt.ItemDataRole.UserRole, app)
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
        if self._on_shell_mode is not None:
            self._on_shell_mode(bool(enabled))
            return
        try:
            from aria import ui_prefs
            ui_prefs.set_shell_mode(bool(enabled))
        except Exception:
            pass
