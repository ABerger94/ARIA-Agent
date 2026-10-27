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
    schemas = types.ModuleType('aria.tools.schemas')
    schemas.ALL_FUNCTION_DECLARATIONS = [{'name': 'read_email', 'description': 'Reads mail'}, {'name': 'missing', 'description': 'Not loaded'}]
    schemas.COMMAND_GUIDE = [('Email', 'read_email', 'Show my inbox')]; schemas.TOOL_TO_TOOLKIT = {}
    for name, mod in [('aria.tools', tools), ('aria.tools.dispatch', dispatch), ('aria.tools.schemas', schemas)]: monkeypatch.setitem(sys.modules, name, mod)
    rows = command_rows()
    assert {r['name'] for r in rows} == {'read_email', 'mcp_test__tool'}
    assert next(r for r in rows if r['name'].startswith('mcp_'))['source'] == 'MCP'
