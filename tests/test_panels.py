"""Port 4 panels: audio prefs roundtrip, providers readout, audio selector.

Pinned behaviours: a broken/missing prefs file reads as the system
default (never raises — the mic must not go silent over a prefs file);
selecting a device in the Audio dialog persists through
aria/audio_prefs.py; the Providers dialog renders the live chain rows
and failover stats, read-only.
"""
import os
import sys
import types
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg


def _app():
    return QApplication.instance() or QApplication([])


# ---------------- audio prefs ----------------

def test_audio_prefs_roundtrip_and_soft_fail(monkeypatch, tmp_path):
    import importlib
    prefs = importlib.import_module('aria.audio_prefs')
    target = tmp_path / 'audio.json'
    monkeypatch.setattr(prefs, '_audio_file', lambda: str(target))
    assert prefs.get_input_device() is None        # missing file -> default
    assert 'system default' in prefs.set_input_device(None)
    assert prefs.get_input_device() is None
    prefs.set_input_device(3)
    assert prefs.get_input_device() == 3
    target.write_text('{broken json')
    assert prefs.get_input_device() is None        # broken file -> default


def test_audio_prefs_device_listing(monkeypatch):
    import importlib
    prefs = importlib.import_module('aria.audio_prefs')
    sd = types.SimpleNamespace()
    sd.default = types.SimpleNamespace(device=[1, 2])
    sd.query_devices = lambda: [
        {'name': 'Mic A', 'max_input_channels': 2, 'max_output_channels': 0},
        {'name': 'Mic B', 'max_input_channels': 1, 'max_output_channels': 2},
        {'name': 'Speakers', 'max_input_channels': 0, 'max_output_channels': 2},
    ]
    monkeypatch.setitem(sys.modules, 'sounddevice', sd)
    assert prefs.list_input_devices() == [
        {'index': 0, 'name': 'Mic A', 'default': False},
        {'index': 1, 'name': 'Mic B', 'default': True},
    ]
    assert prefs.list_output_devices() == [
        {'index': 1, 'name': 'Mic B', 'default': False},
        {'index': 2, 'name': 'Speakers', 'default': True},
    ]


# ---------------- providers panel ----------------

def test_providers_dialog_renders_chain_and_stats(monkeypatch):
    app = _app()
    frame = types.ModuleType('aria.frame')
    frame._provider_rows = lambda: ('groq', [
        {'name': 'groq', 'state': 'live', 'model': 'llama-3.3'},
        {'name': 'gemini', 'state': 'quarantined', 'model': 'flash'},
    ])
    providers = types.ModuleType('aria.agent.providers')
    providers.PROVIDER_STATS = {
        'failovers': 2, 'calls': {'groq': 5, 'gemini': 1},
        'last_failover': ('gemini', 123.0)}
    # aria/agent/__init__ eagerly imports the brain (and speech stack);
    # stub the package so the leaf stub is reachable without it.
    agent_pkg = types.ModuleType('aria.agent')
    agent_pkg.__path__ = [str(Path(__file__).parents[1] / 'aria' / 'agent')]
    monkeypatch.setitem(sys.modules, 'aria.agent', agent_pkg)
    monkeypatch.setitem(sys.modules, 'aria.frame', frame)
    monkeypatch.setattr(sys.modules['aria'], 'frame', frame, raising=False)
    monkeypatch.setitem(sys.modules, 'aria.agent.providers', providers)
    monkeypatch.setattr(agent_pkg, 'providers', providers, raising=False)

    from aria.providers_panel import build_providers_dialog
    from PyQt6.QtWidgets import QLabel
    dlg = build_providers_dialog(None)
    all_text = '\n'.join(w.text() for w in dlg.findChildren(QLabel))
    assert 'groq' in all_text and 'llama-3.3' in all_text
    assert 'quarantined' in all_text and 'ACTIVE' in all_text
    assert 'Failovers: 2' in all_text and 'Calls: 6' in all_text
    dlg.close(); app.processEvents()


# ---------------- audio panel ----------------

def test_audio_dialog_selection_persists(monkeypatch):
    app = _app()
    saved = []
    fake = types.ModuleType('aria.audio_prefs')
    fake.get_input_device = lambda: None
    fake.list_input_devices = lambda: [
        {'index': 2, 'name': 'USB Mic', 'default': True},
        {'index': 5, 'name': 'Headset Mic', 'default': False},
    ]
    fake.list_output_devices = lambda: [
        {'index': 0, 'name': 'Speakers', 'default': True}]
    fake.set_input_device = lambda i: saved.append(i) or f'saved {i}'
    monkeypatch.setitem(sys.modules, 'aria.audio_prefs', fake)
    monkeypatch.setattr(sys.modules['aria'], 'audio_prefs', fake,
                        raising=False)

    from aria.audio_panel import build_audio_dialog
    dlg = build_audio_dialog(None)
    assert dlg._inputs.count() == 3               # default + 2 devices
    assert dlg._inputs.item(1).text().startswith('USB Mic')
    dlg._inputs.setCurrentRow(2)
    assert saved == [5]
    dlg._inputs.setCurrentRow(0)
    assert saved == [5, None]
    dlg.close(); app.processEvents()
