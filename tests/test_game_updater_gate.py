"""game_updater's update action is approval-gated (pass 18,
closing the pass-11 carried 'direct launches' item).

Before: tool_game_updater(action='update') handed steam://
validate URIs straight to the OS handler — no approval gate,
no safe-mode block — while its sibling launch tool
(open_app_or_url) had both. Now:
- needs_approval gates ONLY action='update' under
  confirm-risky (list/status read local manifests and stay
  ungated), following the file_organize / manage_background_job
  conditional pattern;
- the banner description names the target games;
- safe mode blocks the whole tool by name (the
  manage_background_job precedent: is_safe_mode_blocked
  classifies by name only).

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_game_updater_gate_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, 'ARIA')

tools_pkg = types.ModuleType('aria.tools')
tools_pkg.__path__ = [PKG + '/tools']
sys.modules['aria.tools'] = tools_pkg
agent_pkg = types.ModuleType('aria.agent')
agent_pkg.__path__ = [PKG + '/agent']
sys.modules['aria.agent'] = agent_pkg
_ps = types.ModuleType('psutil')
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules['psutil'] = _ps
for _name in ('aria.scheduler', 'aria.vision', 'aria.hardware',
              'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)

import pytest

approval = importlib.import_module('aria.approval')
config = importlib.import_module('aria.config')


@pytest.fixture(autouse=True)
def mode():
    approval.set_mode('confirm-risky')
    yield
    approval.set_mode('confirm-risky')


def test_update_gates_in_confirm_risky():
    assert approval.needs_approval(
        'game_updater', {'action': 'update'}) is True
    assert approval.needs_approval(
        'game_updater', {'action': 'update', 'game': 'Portal'}) is True


def test_list_and_status_stay_ungated():
    assert approval.needs_approval(
        'game_updater', {'action': 'list'}) is False
    assert approval.needs_approval(
        'game_updater', {'action': 'status'}) is False
    assert approval.needs_approval('game_updater', {}) is False


def test_auto_mode_never_gates():
    approval.set_mode('auto')
    assert approval.needs_approval(
        'game_updater', {'action': 'update'}) is False


def test_description_names_the_target():
    desc = approval._describe_tool(
        'game_updater', {'action': 'update', 'game': 'Portal'})
    assert 'Portal' in desc and 'steam://' in desc
    desc = approval._describe_tool('game_updater', {'action': 'update'})
    assert 'update-flagged' in desc


def test_safe_mode_blocks_the_tool():
    assert config.is_safe_mode_blocked('game_updater') is True
