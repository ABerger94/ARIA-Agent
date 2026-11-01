"""Native project, work, deliverable, usage, and companion controls."""
from __future__ import annotations

import json
import math
from pathlib import Path


def create_work_center(parent=None, callbacks=None):
    from PyQt6.QtCore import QTimer, QUrl, Qt
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
        QFormLayout, QComboBox, QLineEdit, QPlainTextEdit, QPushButton, QLabel,
        QListWidget, QListWidgetItem, QInputDialog)
    from aria import workspaces, memory, runtime, missions, deliverables, usage, resource_policy

    dialog = QDialog(parent)
    dialog.setWindowTitle('ARIA | Work Center')
    dialog.resize(950, 720)
    layout = QVBoxLayout(dialog)
    bar = QHBoxLayout()
    dialog.project = QComboBox()
    for item in workspaces.list_projects():
        dialog.project.addItem(item['name'], item['id'])
    dialog.project.setCurrentIndex(dialog.project.findData(workspaces.selected()))
    bar.addWidget(QLabel('Project')); bar.addWidget(dialog.project, 1)
    new = QPushButton('New project'); bar.addWidget(new); layout.addLayout(bar)
    tabs = QTabWidget(); layout.addWidget(tabs, 1)
    message = QLabel(''); message.setWordWrap(True); layout.addWidget(message)

    def page(name):
        widget = QWidget(); box = QVBoxLayout(widget); tabs.addTab(widget, name)
        return box

    def guarded(fn):
        def call(*_):
            try: fn()
            except Exception as exc: message.setText(str(exc))
        return call

    def bind(fn):
        def call():
            with workspaces.use(dialog.project.currentData()): return fn()
        return call

    project_page = page('Project settings')
    form = QFormLayout(); project_page.addLayout(form)
    dialog.daily_budget = QLineEdit(); dialog.task_budget = QLineEdit()
    dialog.output_tokens = QLineEdit()
    form.addRow('Daily model budget (USD; empty = unlimited)', dialog.daily_budget)
    form.addRow('Per-task model budget (USD; empty = unlimited)', dialog.task_budget)
    form.addRow('Maximum output tokens per model call', dialog.output_tokens)
    dialog.provider_order = QLineEdit(); form.addRow('Provider preference (comma-separated)', dialog.provider_order)
    dialog.role_models = QPlainTextEdit(); dialog.role_models.setMaximumHeight(80)
    form.addRow('Role models (JSON: code / vision)', dialog.role_models)
    dialog.rates = QPlainTextEdit(); dialog.rates.setMaximumHeight(120)
    form.addRow('USD per million tokens (JSON)', dialog.rates)
    note = QLabel('Example: {"gemini:your-model":{"input":0.10,"output":0.40}}. '
                  'Enter your provider’s actual prices. Budgeted work refuses unpriced models. '
                  'Missing usage remains an estimate. Daily budgets use UTC.')
    note.setWordWrap(True); project_page.addWidget(note)
    dialog.roots = QPlainTextEdit(); dialog.roots.setMaximumHeight(80)
    form.addRow('File roots (one absolute directory per line; empty = unrestricted)', dialog.roots)
    save = QPushButton('Save project settings'); project_page.addWidget(save)
    project_page.addStretch()

    work_page = page('Work')
    dialog.work_list = QListWidget(); work_page.addWidget(dialog.work_list, 1)
    dialog.work_detail = QPlainTextEdit(); dialog.work_detail.setReadOnly(True); work_page.addWidget(dialog.work_detail, 1)
    controls = QHBoxLayout()
    for label, fn in [('Stop speech', lambda: callbacks.interrupt() if callbacks else None),
                      ('Cancel foreground', lambda: message.setText(
                          f'Cancellation requested for {runtime.cancel_work(kind="foreground")} execution(s).'))]:
        button = QPushButton(label); button.clicked.connect(guarded(bind(fn))); controls.addWidget(button)
    pause, cancel, reconcile = QPushButton('Pause / resume mission'), QPushButton('Cancel selected work'), QPushButton('Record checked receipt')
    for button in (pause, cancel, reconcile): controls.addWidget(button)
    work_page.addLayout(controls)
    work_note = QLabel('Cancellation prevents new actions. An action already in flight may finish and record a receipt. '
                       'Pause takes effect between mission steps. Record a receipt only after checking the destination yourself.')
    work_note.setWordWrap(True); work_page.addWidget(work_note)

    output_page = page('Deliverables')
    dialog.outputs = QListWidget(); output_page.addWidget(dialog.outputs, 1)
    dialog.preview = QPlainTextEdit(); dialog.preview.setReadOnly(True); output_page.addWidget(dialog.preview, 2)
    output_buttons = QHBoxLayout()
    def selected_output():
        item = dialog.outputs.currentItem()
        if item is None: raise ValueError('Select a deliverable')
        return item.data(Qt.ItemDataRole.UserRole)
    for label, fn in [('Open file', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(selected_output()['path']))),
                      ('Show folder', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(selected_output()['path']).parent)))),
                      ('Verify file', lambda: message.setText(json.dumps(deliverables.verify(selected_output()))))]:
        button = QPushButton(label); button.clicked.connect(guarded(bind(fn))); output_buttons.addWidget(button)
    output_page.addLayout(output_buttons)

    usage_page = page('Models and resources')
    dialog.profile = QComboBox(); dialog.profile.addItems(list(resource_policy.PROFILES)); dialog.profile.setCurrentText(resource_policy.profile())
    usage_page.addWidget(QLabel('Resource profile (background-service changes apply after restart)'))
    usage_page.addWidget(dialog.profile)
    dialog.metrics = QLabel(); usage_page.addWidget(dialog.metrics)
    dialog.usage = QPlainTextEdit(); dialog.usage.setReadOnly(True); usage_page.addWidget(dialog.usage, 1)
    def set_profile(value):
        from aria import config
        settings = config.settings_load(); settings['resource_profile'] = value
        if not config.settings_save(settings): raise OSError('Could not save resource profile')
    dialog.profile.currentTextChanged.connect(lambda value: guarded(lambda: set_profile(value))())

    device_page = page('Computers')
    dialog.nodes = QPlainTextEdit(); dialog.nodes.setReadOnly(True); device_page.addWidget(dialog.nodes)
    instructions = QPlainTextEdit(); instructions.setReadOnly(True)
    instructions.setPlainText(
        'On your second laptop, install this repository and its requirements.\n'
        'Save the bridge pairing key in a local file, then run:\n\n'
        'py -3.12 -m aria.desktop_node --url wss://HOST:PORT/node/ws '
        '--name second-laptop --root C:\\Shared --key-file C:\\Private\\aria-node.key '
        '--ca-file C:\\Private\\bridge-cert.pem\n\n'
        'The default shares file reads only. Add --allow-writes for file writes, '
        'or --allow-controls for screen, keyboard, mouse and web links. '
        'Use the exact computer name with desktop_control. The node connects outward '
        'and reconnects automatically. Never share the pairing key in chat.\n'
        'See docs/DESKTOP-COMPANION.md for certificate and pairing steps.')
    device_page.addWidget(instructions)

    def load_settings():
        value = workspaces.get(dialog.project.currentData()).get('settings', {})
        for field, key in [(dialog.daily_budget, 'daily_budget_usd'), (dialog.task_budget, 'task_budget_usd')]:
            field.setText('' if value.get(key) is None else str(value[key]))
        dialog.output_tokens.setText(str(value.get('max_output_tokens', 2048)))
        dialog.provider_order.setText(','.join(value.get('provider_order', [])))
        dialog.role_models.setPlainText(json.dumps(value.get('role_models', {}), indent=2))
        dialog.rates.setPlainText(json.dumps(value.get('model_rates', {}), indent=2))
        dialog.roots.setPlainText('\n'.join(value.get('file_roots', [])))

    def save_settings():
        value = {}
        for field, key in [(dialog.daily_budget,'daily_budget_usd'), (dialog.task_budget,'task_budget_usd')]:
            amount = float(field.text()) if field.text().strip() else None
            if amount is not None and (amount < 0 or not math.isfinite(amount)): raise ValueError('Budgets must be nonnegative numbers')
            value[key] = amount
        value['max_output_tokens'] = int(dialog.output_tokens.text())
        if not 64 <= value['max_output_tokens'] <= 16384: raise ValueError('Output tokens must be 64–16384')
        value['provider_order'] = [v.strip() for v in dialog.provider_order.text().split(',') if v.strip()]
        value['role_models'] = json.loads(dialog.role_models.toPlainText() or '{}')
        value['model_rates'] = json.loads(dialog.rates.toPlainText() or '{}')
        if not isinstance(value['role_models'], dict) or not isinstance(value['model_rates'], dict): raise ValueError('Model settings must be JSON objects')
        for price in value['model_rates'].values():
            if not isinstance(price, dict): raise ValueError('Each model needs input and output prices')
            for key in ('input','output'):
                if not math.isfinite(float(price[key])) or float(price[key]) < 0: raise ValueError('Prices must be nonnegative numbers')
        value['file_roots'] = [str(Path(v.strip()).expanduser().resolve()) for v in dialog.roots.toPlainText().splitlines() if v.strip()]
        workspaces.update_settings(value, dialog.project.currentData())
        message.setText('Project settings saved. In-flight work keeps its project; new calls use these settings.')
    save.clicked.connect(guarded(save_settings))

    def change_project():
        previous = workspaces.selected()
        workspaces.select(dialog.project.currentData())
        if previous != dialog.project.currentData():
            import sys
            main_module = sys.modules.get('aria.main')
            live_manager = getattr(main_module, '_LIVE_MANAGER', None)
            if live_manager is not None:
                live_manager.stop()  # its existing ended hook re-arms pipeline voice
                main_module._LIVE_MANAGER = None
        with workspaces.use(dialog.project.currentData()): memory.init_databases()
        load_settings(); dialog.work_list.clear(); dialog.outputs.clear(); dialog.preview.clear()
        refresh()
        if parent is not None and hasattr(parent, '_rebuild_memory_tab'): parent._rebuild_memory_tab()

    def new_project():
        name, ok = QInputDialog.getText(dialog, 'New project', 'Project name')
        if ok and name.strip():
            pid = workspaces.create(name); dialog.project.addItem(name.strip(), pid)
            dialog.project.setCurrentIndex(dialog.project.findData(pid))
    new.clicked.connect(guarded(new_project)); dialog.project.currentIndexChanged.connect(guarded(change_project))

    def selected_work():
        item = dialog.work_list.currentItem()
        if item is None: raise ValueError('Select a work item')
        return item.data(Qt.ItemDataRole.UserRole)

    def show_work():
        item = selected_work()
        if item['kind'] == 'mission':
            details = {'mission': missions._mission_row(item['id']), 'steps': missions._step_rows(item['id'])}
            from aria import action_journal
            details['receipts'] = [action_journal.get(f'mission:{item["id"]}:step:{s["id"]}') for s in details['steps']]
        else: details = item
        dialog.work_detail.setPlainText(json.dumps(details, indent=2, default=str))
    dialog.work_list.currentItemChanged.connect(guarded(bind(show_work)))
    def pause_work():
        item = selected_work()
        if item['kind'] != 'mission': raise ValueError('Pause is available for missions')
        message.setText(missions.pause_mission(item['id']))
    pause.clicked.connect(guarded(bind(pause_work)))
    def cancel_work():
        item = selected_work()
        if item['kind'] == 'mission': message.setText(missions.cancel_mission(item['id']))
        else:
            runtime.cancel_work(item['id']); message.setText('Cancellation requested.')
    cancel.clicked.connect(guarded(bind(cancel_work)))
    def record_receipt():
        item = selected_work()
        if item['kind'] != 'mission': raise ValueError('Select a mission awaiting evidence')
        uncertain = [s for s in missions._step_rows(item['id']) if s['status'] == 'uncertain']
        if not uncertain: raise ValueError('This mission has no uncertain step')
        reference, ok = QInputDialog.getText(dialog, 'Checked external receipt',
            'After checking the destination, enter its receipt or evidence reference:')
        if ok:
            missions.record_receipt(item['id'], uncertain[0]['id'], reference)
            message.setText('User-checked receipt recorded; the mission can continue.')
    reconcile.clicked.connect(guarded(bind(record_receipt)))
    def show_output():
        item = selected_output()
        dialog.preview.setPlainText(json.dumps(item['evidence'], indent=2) + '\n\n' + deliverables.preview(item))
    dialog.outputs.currentItemChanged.connect(guarded(bind(show_output)))

    def refresh():
        with workspaces.use(dialog.project.currentData()):
            current = dialog.work_list.currentItem()
            selected = current.data(Qt.ItemDataRole.UserRole) if current else None
            work = [{'kind':'mission', **m} for m in missions.mission_summaries(100)]
            work += [v for v in runtime.snapshot() if v['project'] == workspaces.current() and v['kind'] != 'mission']
            signature = json.dumps(work, sort_keys=True)
            if getattr(dialog, '_work_signature', None) != signature:
                dialog._work_signature = signature
                dialog.work_list.blockSignals(True); dialog.work_list.clear()
                for row in work:
                    item = QListWidgetItem(f'{row["kind"]} {row["id"]}: {row.get("status", "running")} {row.get("goal", "")}')
                    item.setData(Qt.ItemDataRole.UserRole, row); dialog.work_list.addItem(item)
                    if selected and row['id'] == selected['id'] and row['kind'] == selected['kind']: dialog.work_list.setCurrentItem(item)
                dialog.work_list.blockSignals(False)
            items = deliverables.list_items()
            if getattr(dialog, '_output_ids', None) != [i['id'] for i in items]:
                dialog._output_ids = [i['id'] for i in items]; dialog.outputs.clear()
                for row in items:
                    item = QListWidgetItem(f'{Path(row["path"]).name} · {row["tool"]} · {(row["sha256"] or "unverified")[:12]}')
                    item.setData(Qt.ItemDataRole.UserRole, row); dialog.outputs.addItem(item)
            attempts = usage.recent()
            dialog.usage.setPlainText('\n'.join(f'{v["provider"]}/{v["model"]} · {v["latency_ms"] or 0} ms · '
                f'{"unknown cost" if v["cost"] is None else ("estimated " if v["estimated"] else "") + "$" + format(v["cost"], ".6f")} · '
                f'{v["status"]} · {v["scope"]} · {v["reason"]}' for v in attempts) or 'No model calls recorded in this project.')
            try: dialog.metrics.setText(json.dumps(resource_policy.metrics()))
            except Exception: dialog.metrics.setText('Resource measurements unavailable')
            from aria.node_hub import HUB
            dialog.nodes.setPlainText(json.dumps(HUB.nodes(), indent=2))
    dialog.refresh = guarded(refresh)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    dialog._timer = QTimer(dialog); dialog._timer.timeout.connect(dialog.refresh); dialog._timer.start(1500)
    dialog.finished.connect(dialog._timer.stop)
    load_settings(); dialog.refresh()
    return dialog
