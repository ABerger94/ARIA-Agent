import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
pytest.importorskip('PyQt6')
from PyQt6.QtWidgets import QApplication, QPushButton, QInputDialog
from test_missions import env, config
from aria import workspaces, resource_policy
from aria.work_center import create_work_center


def test_native_work_center_creates_project_and_saves_policy(env, monkeypatch):
    monkeypatch.setattr(config, 'WORKSPACE_DIR', str(env.tmp / 'workspace'))
    monkeypatch.setattr(config, 'SETTINGS_FILE', str(env.tmp / 'settings.json'))
    app = QApplication.instance() or QApplication([])
    dialog = create_work_center()
    dialog.show(); app.processEvents()
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a: ('New project',True))
    next(b for b in dialog.findChildren(QPushButton) if b.text() == 'New project').click()
    assert dialog.project.currentData() != 'default'
    assert workspaces.get()['name'] == 'New project'
    dialog.daily_budget.setText('1.25')
    dialog.rates.setPlainText('{"test:m":{"input":1,"output":2}}')
    next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Save project settings').click()
    assert workspaces.get()['settings']['daily_budget_usd'] == 1.25
    dialog.profile.setCurrentText('minimal'); assert resource_policy.profile() == 'minimal'
    dialog.refresh(); dialog.close(); app.processEvents()


def test_profile_throttles_hidden_and_idle_windows(env, monkeypatch):
    monkeypatch.setattr(config, 'get_setting', lambda key, default=None: 'minimal' if key == 'resource_profile' else default)
    assert resource_policy.interval(minimized=True) > resource_policy.interval(active=True)
    assert resource_policy.interval(active=False) > resource_policy.interval(active=True)
    assert not resource_policy.optional_enabled('life')
