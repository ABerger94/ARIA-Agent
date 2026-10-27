"""Providers panel adapted from Mark-LVI-ARIA's ProvidersOverlay.

Source: https://github.com/ABerger94/Mark-LVI-ARIA/blob/61a47b0/ui.py
Original MARK LVI: Copyright FatihMakes; ARIA fork modifications: Alek Berger.
Adapted panel retains CC BY-NC 4.0 terms. See LICENSES/Mark-LVI.txt.
Changes: the source's overlay also EDITS engine configuration (keys and
models). This adaptation is deliberately READ-ONLY: ARIA-AGENT's keys
live in its config/env store, and a UI port is not the place to grow a
credential-editing surface. What ports is the readout the source's
overlay exists for — the chain, each provider's state and model, and
the failover stats — fed by aria/frame.py's provider rows and
aria/agent/providers.py's session stats.
"""
from __future__ import annotations

import html

_STATE_COLORS = {
    'live': '#00ff88',
    'standby': '#829aaf',
    'quarantined': '#ff6b00',
}


def provider_data():
    """(active_name, rows, stats) from the live sources. Never raises:
    a dead source degrades its own section, not the panel."""
    active, rows, stats = '', [], {}
    try:
        from aria import frame
        active, rows = frame._provider_rows()
    except Exception:
        pass
    try:
        from aria.agent import providers as pv
        stats = dict(pv.PROVIDER_STATS)
    except Exception:
        pass
    return active, rows, stats


def _providers_html(active, rows) -> str:
    if not rows:
        return 'Provider chain unavailable.'
    lines = []
    for row in rows:
        name = html.escape(str(row.get('name', '?')))
        state = str(row.get('state', ''))
        model = html.escape(str(row.get('model', '')))
        color = _STATE_COLORS.get(state, '#dce6ef')
        marker = ' ▸ ACTIVE' if row.get('name') == active else ''
        lines.append(
            f'<span style="color:{color}">●</span> <b>{name}</b> — '
            f'{html.escape(state)}'
            + (f' · {model}' if model else '')
            + marker)
    return '<br>'.join(lines)


def _stats_text(stats) -> str:
    if not stats:
        return 'Session stats unavailable.'
    calls = stats.get('calls') or {}
    total_calls = sum(int(v) for v in calls.values())
    last = stats.get('last_failover')
    last_text = str(last[0]) if last else '—'
    return (f"Failovers: {int(stats.get('failovers', 0))} · "
            f"Calls: {total_calls} · Last failover: {last_text}")


def build_providers_widget(parent=None):
    """The Providers readout as an embeddable widget — the same
    content the standalone dialog shows, so the Settings dialog's
    Providers tab and the dialog can never drift apart. Read-only:
    keys and chain order live in config, not in this surface."""
    from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

    w = QWidget(parent)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)

    body = QLabel()
    body.setWordWrap(True)
    lay.addWidget(body)
    stats_label = QLabel()
    stats_label.setWordWrap(True)
    lay.addWidget(stats_label)

    def refresh():
        active, rows, stats = provider_data()
        body.setText(_providers_html(active, rows))
        stats_label.setText(_stats_text(stats))

    refresh()
    refresh_btn = QPushButton('Refresh')
    refresh_btn.clicked.connect(refresh)
    lay.addWidget(refresh_btn)
    lay.addStretch()
    w._refresh = refresh
    w._body = body
    w._stats = stats_label
    return w


def build_providers_dialog(parent):
    """The Providers dialog (not exec'd — the caller decides)."""
    from PyQt6.QtWidgets import (QDialog, QLabel, QPushButton, QVBoxLayout)
    from aria.dashboard import STYLE

    dlg = QDialog(parent)
    dlg.setWindowTitle('Providers')
    dlg.setStyleSheet(
        parent.styleSheet() if parent is not None and parent.styleSheet()
        else STYLE)
    dlg.resize(520, 400)
    lay = QVBoxLayout(dlg)

    title = QLabel('PROVIDERS')
    title.setObjectName('title')
    lay.addWidget(title)

    widget = build_providers_widget(dlg)
    lay.addWidget(widget, 1)
    dlg._refresh = widget._refresh

    close_btn = QPushButton('Close')
    close_btn.clicked.connect(dlg.accept)
    lay.addWidget(close_btn)
    return dlg
