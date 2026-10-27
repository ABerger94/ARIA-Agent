"""Audio devices panel adapted from Mark-LVI-ARIA's AudioDeviceOverlay.

Source: https://github.com/ABerger94/Mark-LVI-ARIA/blob/61a47b0/ui.py
Original MARK LVI: Copyright FatihMakes; ARIA fork modifications: Alek Berger.
Adapted panel retains CC BY-NC 4.0 terms. See LICENSES/Mark-LVI.txt.
Changes: the source drives two live streams (mic + speakers) directly.
ARIA-AGENT's capture accepts a device index but was never handed one,
so this panel is the selector for the knob that exists: the input
device, persisted via aria/audio_prefs.py and read by aria/main.py at
each microphone open. Output devices are shown READ-ONLY — speech
output routes through the OS default endpoint (comhost/SAPI), and the
panel says so instead of offering a control that does nothing.
"""
from __future__ import annotations


def build_audio_dialog(parent):
    """The Audio Devices dialog (not exec'd — the caller decides)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (QDialog, QLabel, QListWidget,
                                 QListWidgetItem, QPushButton, QVBoxLayout)
    from aria import audio_prefs
    from aria.dashboard import STYLE

    dlg = QDialog(parent)
    dlg.setWindowTitle('Audio Devices')
    dlg.setStyleSheet(STYLE)
    dlg.resize(520, 460)
    lay = QVBoxLayout(dlg)

    title = QLabel('AUDIO DEVICES')
    title.setObjectName('title')
    lay.addWidget(title)

    lay.addWidget(QLabel('MICROPHONE (applies at the next mic open)'))
    inputs = QListWidget()
    default_item = QListWidgetItem('System default')
    default_item.setData(Qt.ItemDataRole.UserRole, None)
    inputs.addItem(default_item)
    current = audio_prefs.get_input_device()
    selected_row = 0
    for dev in audio_prefs.list_input_devices():
        label = dev['name'] + (' (system default)' if dev['default'] else '')
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, dev['index'])
        inputs.addItem(item)
        if current is not None and dev['index'] == current:
            selected_row = inputs.count() - 1
    inputs.setCurrentRow(selected_row)
    lay.addWidget(inputs)

    note = QLabel('')
    note.setWordWrap(True)
    lay.addWidget(note)

    def on_input_changed(row):
        if row < 0:
            return
        index = inputs.item(row).data(Qt.ItemDataRole.UserRole)
        note.setText(audio_prefs.set_input_device(index))

    inputs.currentRowChanged.connect(on_input_changed)

    lay.addWidget(QLabel('SPEAKERS (read-only — output uses the OS default)'))
    outputs = QListWidget()
    outputs.setSelectionMode(QListWidget.SelectionMode.NoSelection)
    for dev in audio_prefs.list_output_devices():
        label = dev['name'] + (' (system default)' if dev['default'] else '')
        outputs.addItem(QListWidgetItem(label))
    if outputs.count() == 0:
        outputs.addItem(QListWidgetItem('No output devices found'))
    lay.addWidget(outputs)

    close_btn = QPushButton('Close')
    close_btn.clicked.connect(dlg.accept)
    lay.addWidget(close_btn)
    dlg._inputs = inputs
    dlg._note = note
    return dlg
