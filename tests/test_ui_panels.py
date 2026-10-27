"""Native settings validation, persistence and command discovery/selection."""
import os
import sys
import types
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton
pkg = types.ModuleType('aria'); pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg
from aria.dashboard import STYLE
from aria.ui_panels import create_settings_dialog, create_commands_dialog, command_rows


def test_settings_validate_persist_and_apply(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication(['aria-settings-test'])
    saved = {}; calls = []
    config = types.ModuleType('aria.config'); config._KEYS = {}
    config.get_setting = lambda key, default=None: saved.get(key, default)
    config.settings_load = lambda: dict(saved)
    def save(values): saved.update(values); return True
    config.settings_save = save; config.key_get = lambda key, fallback='': ('', 'script default')
    config.save_keys = lambda: True; config.is_safe_mode = lambda: False
    speech = types.ModuleType('aria.speech'); speech.VOICE_ENABLED = True
    hud = types.ModuleType('aria.hud'); hud.WHISPER_MODE = False
    hud.get_hud_volume = lambda: .5; hud.set_hud_volume = calls.append
    approval = types.ModuleType('aria.approval'); approval.MODES = ('auto', 'confirm-risky', 'confirm-all')
    approval.get_mode = lambda: 'confirm-risky'; approval.set_mode = calls.append
    for name, mod in [('config', config), ('speech', speech), ('hud', hud), ('approval', approval)]:
        monkeypatch.setitem(sys.modules, 'aria.' + name, mod); monkeypatch.setattr(pkg, name, mod, raising=False)
    class Parent(QMainWindow):
        def apply_accent(self, value): calls.append(value)
        def open_remote(self): calls.append('remote')
    parent = Parent(); parent.setStyleSheet(STYLE)
    callbacks = types.SimpleNamespace(speak=calls.append, toggle_whisper=lambda: calls.append('whisper'))
    dialog = create_settings_dialog(parent, callbacks); dialog.show(); app.processEvents()
    dialog.name.setText('Alek'); dialog.city.setText('Dundalk, Maryland'); dialog.zone.setText('Invalid/Zone')
    dialog.save.click(); assert not saved and speech.VOICE_ENABLED
    dialog.zone.setText('America/New_York'); dialog.voice.setChecked(False); dialog.whisper.setChecked(True)
    dialog.volume.setValue(80); dialog.topics.setText('science, world economy')
    dialog.connection_fields['GMAIL_APP_PASSWORD'].setText('fixture-password-not-real')
    dialog.save.click()
    assert saved['briefing_city'] == 'Dundalk, Maryland' and saved['briefing_world_topics'] == ['science', 'world economy']
    assert saved['voice_enabled'] is False and speech.VOICE_ENABLED is False
    assert .8 in calls and 'whisper' in calls
    assert config._KEYS['GMAIL_APP_PASSWORD'] == 'fixture-password-not-real'
    assert dialog.connection_fields['GMAIL_APP_PASSWORD'].text() == ''
    assert 'saved' in dialog.message.text().lower()
    dialog.grab().save(str(tmp_path / 'settings.png')); dialog.close(); parent.close()


def test_settings_hosts_providers_audio_customize_pet(monkeypatch, tmp_path):
    # Alek, 2026-10-27: the Providers / Audio / Customize / Pet
    # functions belong in Settings too. Pins: the tabs exist, the
    # audio + providers widgets are the panels' own shared widgets,
    # the Customize wheel drives the saved accent, and the Pet
    # controls reach the host window.
    app = QApplication.instance() or QApplication(['aria-settings-test'])
    saved = {}; calls = []
    config = types.ModuleType('aria.config'); config._KEYS = {}
    config.get_setting = lambda key, default=None: saved.get(key, default)
    config.settings_load = lambda: dict(saved)
    def save(values): saved.update(values); return True
    config.settings_save = save; config.key_get = lambda key, fallback='': ('', 'script default')
    config.save_keys = lambda: True; config.is_safe_mode = lambda: False
    speech = types.ModuleType('aria.speech'); speech.VOICE_ENABLED = True
    speech._set_session_voice = lambda v: None
    hud = types.ModuleType('aria.hud'); hud.WHISPER_MODE = False
    hud.get_hud_volume = lambda: .5; hud.set_hud_volume = calls.append
    approval = types.ModuleType('aria.approval'); approval.MODES = ('auto', 'confirm-risky', 'confirm-all')
    approval.get_mode = lambda: 'confirm-risky'; approval.set_mode = calls.append
    for name, mod in [('config', config), ('speech', speech), ('hud', hud), ('approval', approval)]:
        monkeypatch.setitem(sys.modules, 'aria.' + name, mod); monkeypatch.setattr(pkg, name, mod, raising=False)
    mcp = types.ModuleType('aria.mcp')
    mcp.get_servers = lambda: {'fs': {'transport': 'stdio', 'command': 'npx fs'}}
    monkeypatch.setitem(sys.modules, 'aria.mcp', mcp); monkeypatch.setattr(pkg, 'mcp', mcp, raising=False)
    tools_pkg = types.ModuleType('aria.tools'); tools_pkg.__path__ = []
    dispatch = types.ModuleType('aria.tools.dispatch')
    dispatch.tool_names = lambda: ['alpha', 'beta']
    tools_pkg.dispatch = dispatch
    monkeypatch.setitem(sys.modules, 'aria.tools', tools_pkg)
    monkeypatch.setitem(sys.modules, 'aria.tools.dispatch', dispatch)
    monkeypatch.setattr(pkg, 'tools', tools_pkg, raising=False)
    import aria.ui_prefs as ui_prefs
    monkeypatch.setattr(ui_prefs, '_ui_file', lambda: str(tmp_path / 'ui.json'))
    class Parent(QMainWindow):
        def apply_accent(self, value): calls.append(('accent', value))
        def open_remote(self): calls.append('remote')
        def toggle_pet(self): calls.append('pet')
        def set_centerpiece(self, name): calls.append(('cp', name))
    parent = Parent(); parent.setStyleSheet(STYLE)
    callbacks = types.SimpleNamespace(speak=calls.append, toggle_whisper=lambda: calls.append('whisper'))
    dialog = create_settings_dialog(parent, callbacks); dialog.show(); app.processEvents()
    from PyQt6.QtWidgets import QTabWidget
    tabs = dialog.findChild(QTabWidget)
    titles = [tabs.tabText(i) for i in range(tabs.count())]
    for wanted in ('Providers', 'Extensions', 'Customize', 'Pet'):
        assert wanted in titles, titles
    # Customize hosts the centerpiece choice (Alek, 2026-10-27:
    # the dashboard button moved here): it persists via ui_prefs
    # and reaches the host window immediately.
    dialog.centerpiece.setCurrentIndex(1)
    assert ui_prefs.get_centerpiece() == 'holo'
    assert ('cp', 'holo') in calls
    dialog.centerpiece.setCurrentIndex(0)
    assert ui_prefs.get_centerpiece() == 'orb'
    assert ('cp', 'orb') in calls
    # Extensions (Alek, 2026-10-27): the read-only MCP/tool
    # readout that used to float over the dashboard now lives
    # here as a Settings tab.
    assert dialog.extensions_widget is not None
    from PyQt6.QtWidgets import QLabel
    ext_texts = [lbl.text() for lbl in dialog.extensions_widget.findChildren(QLabel)]
    assert any('fs — stdio — npx fs' in t for t in ext_texts), ext_texts
    assert any('2 built-in tools registered' in t for t in ext_texts), ext_texts
    # Audio: the shared device widget is embedded in Voice/audio.
    assert dialog.audio_widget is not None
    assert dialog.audio_widget._inputs.count() >= 1
    # Providers: the shared readout widget, refreshable.
    assert dialog.providers_widget is not None
    dialog.providers_widget._refresh()
    # Customize: the wheel pick wins the saved accent.
    assert dialog.customize_wheel is not None
    dialog.customize_wheel.hue_picked.emit('#ff3366')
    assert dialog._customize_hex == '#ff3366'
    assert ('accent', '#ff3366') in calls          # live preview
    # Pet: toggle reaches the host; the form persists via ui_prefs.
    assert dialog.pet_toggle.isEnabled()
    dialog.pet_toggle.click(); assert 'pet' in calls
    dialog.pet_form.setCurrentIndex(1)
    assert ui_prefs.get_centerpiece() == 'holo'
    # Save persists the wheel's accent over the preset combo.
    dialog.name.setText('Alek'); dialog.city.setText('Dundalk, Maryland'); dialog.zone.setText('America/New_York')
    dialog.save.click()
    assert saved['ui_accent'] == '#ff3366', saved.get('ui_accent')
    dialog.close(); parent.close()


def test_command_filter_and_example_only_drafts(tmp_path):
    app = QApplication.instance() or QApplication(['aria-commands-test'])
    parent = QMainWindow(); parent.setStyleSheet(STYLE); choices = []
    rows = [dict(name='read_email', description='Read messages without marking them read.', category='Email', source='ARIA', example='Show my unread email'),
            dict(name='check_calendar', description='Check upcoming calendar events.', category='Calendar', source='ARIA', example='What is on my calendar?')]
    dialog = create_commands_dialog(parent, choices.append, rows); dialog.show(); app.processEvents()
    dialog.search.setText('calendar'); assert dialog.rows[0][0].isHidden() and not dialog.rows[1][0].isHidden()
    dialog.search.setText('nothing matches'); assert not dialog.empty.isHidden()
    dialog.search.clear(); dialog.category.setCurrentText('Email')
    assert dialog.rows[1][0].isHidden()
    button = next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Try: Show my unread email')
    dialog.grab().save(str(tmp_path / 'commands.png')); button.click()
    assert choices == ['Show my unread email']
    parent.close()


def test_catalog_contains_registered_tools_only(monkeypatch):
    tools = types.ModuleType('aria.tools'); tools.__path__ = []
    dispatch = types.ModuleType('aria.tools.dispatch'); dispatch.get_registered_tools = lambda: {'read_email': lambda: None, 'mcp_test__tool': lambda: None}
    dispatch.TOOL_TO_TOOLKIT = {}
    schemas = types.ModuleType('aria.tools.schemas')
    schemas.ALL_FUNCTION_DECLARATIONS = [{'name': 'read_email', 'description': 'Reads mail'}, {'name': 'missing', 'description': 'Not loaded'}]
    schemas.COMMAND_GUIDE = [('Email', 'read_email', 'Show my inbox')]
    for name, mod in [('aria.tools', tools), ('aria.tools.dispatch', dispatch), ('aria.tools.schemas', schemas)]: monkeypatch.setitem(sys.modules, name, mod)
    rows = command_rows()
    assert {r['name'] for r in rows} == {'read_email', 'mcp_test__tool'}
    assert next(r for r in rows if r['name'].startswith('mcp_'))['source'] == 'MCP'


def test_catalog_against_the_real_schemas_and_dispatch(monkeypatch):
    # The integration the stubbed test above cannot give: the
    # REAL schemas + dispatch pair. command_rows imported
    # TOOL_TO_TOOLKIT from schemas, where it does not exist —
    # an ImportError on Alek's laptop (2026-10-27) while every
    # stubbed test passed. Only leaf externals whose third-party
    # packages are absent from the sandbox (speech_recognition
    # via aria.speech, psutil, cv2) are stubbed — the modules
    # under test stay real.
    speech = types.ModuleType('aria.speech')
    speech.speak = lambda t: None
    speech.edge_tts_bytes = lambda text, whisper_mode=False: b''
    speech.tts_bytes_for_bridge = lambda text: (b'', 'no speech in tests')
    speech.transcribe_audio = lambda audio, mime: ''
    speech._SPEECH_STOP = types.SimpleNamespace(is_set=lambda: False)
    speech._SPEECH_QUEUE = types.SimpleNamespace(unfinished_tasks=0)
    speech._AUDIO_PLAY_QUEUE = types.SimpleNamespace(unfinished_tasks=0)
    monkeypatch.setitem(sys.modules, 'aria.speech', speech)
    monkeypatch.setattr(pkg, 'speech', speech, raising=False)
    psutil = types.ModuleType('psutil')
    psutil.cpu_percent = lambda interval=0: 0.0
    psutil.virtual_memory = lambda: types.SimpleNamespace(
        percent=0.0, available=0, total=0, used=0, free=0)
    psutil.disk_usage = lambda p: types.SimpleNamespace(
        percent=0.0, total=0, used=0, free=0)
    psutil.disk_partitions = lambda: []
    psutil.sensors_battery = lambda: None
    monkeypatch.setitem(sys.modules, 'psutil', psutil)
    cv2 = types.ModuleType('cv2')
    cv2.VideoCapture = lambda *a, **k: None
    cv2.IMWRITE_JPEG_QUALITY = 1
    cv2.resize = lambda img, size: img
    cv2.imencode = lambda ext, img, params=None: (False, None)
    cv2.imwrite = lambda path, img: True
    monkeypatch.setitem(sys.modules, 'cv2', cv2)
    rows = command_rows()
    assert len(rows) > 50
    assert all(r['category'] for r in rows)
    assert any(r['name'] == 'read_email' for r in rows)
