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
Exception (Alek, 2026-11-10): the "Local only" toggle. PROVIDER_MODE
was config-file-only, and Alek asked for the switch where he looks
for it — this tab. The toggle flips the mode live (no restart) via
aria.agent.providers.set_provider_mode, which persists it to
aria_keys.json; it edits no key and no model, so the no-credential-
editing line holds.
"""
from __future__ import annotations

import html

_STATE_COLORS = {
    'live': '#00ff88',
    'standby': '#829aaf',
    'quarantined': '#ff6b00',
    'error': '#ff6b6b',
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
        if row.get('last_result'):
            lines.append('Last result: ' + html.escape(str(row['last_result'])))
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


def _current_mode() -> str:
    """The live PROVIDER_MODE for the toggle's initial state: the
    providers module's live value, config's startup global as the
    fallback for a surface that predates provider_mode()."""
    try:
        from aria.agent.providers import provider_mode
        return provider_mode()
    except Exception:
        pass
    try:
        from aria import config
        return str(getattr(config, "PROVIDER_MODE", "auto"))
    except Exception:
        return "auto"


def build_providers_widget(parent=None):
    """The Providers readout as an embeddable widget — the same
    content the standalone dialog shows, so the Settings dialog's
    Providers tab and the dialog can never drift apart. The readout
    stays read-only (keys and chain order live in config, not in
    this surface); the one control is the Local only toggle above
    it, which flips PROVIDER_MODE live and persists it."""
    from PyQt6.QtWidgets import (QApplication, QCheckBox, QLabel,
                                QPushButton, QVBoxLayout, QWidget)

    w = QWidget(parent)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)

    mode_label = QLabel()
    mode_label.setWordWrap(True)
    lay.addWidget(mode_label)
    local_only = QCheckBox(
        'Local only — Ollama on this machine serves everything; '
        'API-keyed providers are never called')
    local_only.setChecked(_current_mode() == 'local_only')
    lay.addWidget(local_only)
    mode_status = QLabel('')
    mode_status.setWordWrap(True)
    lay.addWidget(mode_status)

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
        mode_now = _current_mode()
        mode_label.setText(
            'Provider mode: <b>LOCAL ONLY</b> — the local Ollama '
            'model serves every turn; if it is down you get an '
            'honest error, never a silent cloud fallback.'
            if mode_now == 'local_only' else
            'Provider mode: <b>Auto</b> — the configured provider '
            'chain serves, cloud legs included.')
        if local_only.isChecked() != (mode_now == 'local_only'):
            local_only.blockSignals(True)
            local_only.setChecked(mode_now == 'local_only')
            local_only.blockSignals(False)

    def _mode_toggled(checked):
        try:
            from aria.agent.providers import set_provider_mode
            effective = set_provider_mode(
                'local_only' if checked else 'auto')
            mode_status.setText(
                'Local only ON — takes effect on the next turn and '
                'is saved for the next start.'
                if effective == 'local_only' else
                'Local only OFF — the full provider chain is back, '
                'starting with the next turn.')
        except Exception:
            mode_status.setText(
                'Mode toggle unavailable — set PROVIDER_MODE in '
                'aria_keys.json instead.')
        refresh()

    local_only.toggled.connect(_mode_toggled)

    refresh()
    refresh_btn = QPushButton('Refresh')
    refresh_btn.clicked.connect(refresh)
    lay.addWidget(refresh_btn)
    copy_btn = QPushButton('Copy connection report')
    status = QLabel('')
    status.setWordWrap(True)
    def copy_report():
        try:
            from aria.agent.providers import connection_report
            QApplication.clipboard().setText(connection_report())
            status.setText('Connection report copied. API keys and response bodies are omitted.')
        except Exception:
            status.setText('Connection report unavailable.')
    copy_btn.clicked.connect(copy_report)
    lay.addWidget(copy_btn)
    lay.addWidget(status)
    lay.addStretch()
    w._refresh = refresh
    w._body = body
    w._stats = stats_label
    w._copy_report = copy_btn
    w._local_only = local_only
    w._mode_label = mode_label
    w._mode_status = mode_status
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
