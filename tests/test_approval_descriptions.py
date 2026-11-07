"""Approval banners must name the operation (pass 25 P1#9).

_describe_tool used to fall back to a bare "execute tool 'x'"
for mcp_setup / edit_own_code / desktop_control, showed only
to+subject for send_email, and only the first line of
run_python_code — so the approver could not see what the tap
would actually do. These pin the descriptions.
"""
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_approval_desc_')
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
import importlib
approval = importlib.import_module('aria.approval')


def test_mcp_setup_names_server_and_secrets():
    desc = approval._describe_tool('mcp_setup', {
        'name': 'github', 'transport': 'stdio', 'command': 'npx',
        'args': ['run', 'server'],
        'env': {'GITHUB_TOKEN': 'ghp_secret'},
        'headers': {'Authorization': 'Bearer secret'}})
    assert 'github' in desc and 'stdio' in desc and 'npx' in desc
    # Pass 27 #15: the command args are shown whole, and env/header
    # VALUES never appear — only their names.
    assert 'run' in desc and 'server' in desc
    assert 'GITHUB_TOKEN' in desc and 'Authorization' in desc
    assert 'ghp_secret' not in desc and 'Bearer secret' not in desc


def test_edit_own_code_names_file():
    desc = approval._describe_tool('edit_own_code', {
        'path': 'aria/brain.py', 'content': 'x' * 50})
    assert 'aria/brain.py' in desc and '50 chars' in desc
    assert desc != "execute tool 'edit_own_code'"


def test_patch_own_code_shows_both_sides():
    desc = approval._describe_tool('patch_own_code', {
        'path': 'aria/brain.py',
        'old_text': 'return False  # old behaviour',
        'new_text': 'return True  # new behaviour'})
    assert 'aria/brain.py' in desc
    assert 'old behaviour' in desc and 'new behaviour' in desc


def test_desktop_control_names_node_action_args():
    desc = approval._describe_tool('desktop_control', {
        'node': 'desk-1', 'action': 'click',
        'arguments': {'x': 10, 'y': 20}})
    assert 'desk-1' in desc and 'click' in desc
    assert 'x' in desc and 'y' in desc


def test_send_email_shows_body_preview():
    desc = approval._describe_tool('send_email', {
        'to': 'a@example.invalid', 'subject': 'Hi',
        'body': 'Please wire $1000 to account 12345 immediately.'})
    assert 'a@example.invalid' in desc and 'Hi' in desc
    assert 'wire $1000' in desc


def test_run_python_code_shows_more_than_first_line():
    desc = approval._describe_tool('run_python_code', {
        'code': '# harmless comment\nimport os\nos.system("rm -rf /")\n'})
    assert 'import os' in desc
    assert 'os.system' in desc


def _stub_mcp_servers(monkeypatch, servers):
    # Pass 28 #37: _describe_tool lazily imports aria.mcp for the
    # connect description; stub it so the test never touches the
    # real settings store.
    fake = types.ModuleType('aria.mcp')
    fake.get_servers = lambda: servers
    monkeypatch.setitem(sys.modules, 'aria.mcp', fake)


def test_mcp_connect_names_server_and_command(monkeypatch):
    _stub_mcp_servers(monkeypatch, {
        'robinhood': {'transport': 'stdio', 'command': 'python',
                      'args': ['-m', 'robinhood_mcp'], 'enabled': True}})
    desc = approval._describe_tool('mcp_connect', {
        'name': 'robinhood', '_config_digest': 'abc123deadbeef'})
    assert 'robinhood' in desc and 'stdio' in desc
    assert 'python' in desc and '-m' in desc
    # The frozen digest binds the approval — it is not what the
    # tap does, and must never show as noise in the banner.
    assert 'abc123deadbeef' not in desc
    assert desc != "execute tool 'mcp_connect'"


def test_mcp_connect_all_names_the_set_not_the_digest(monkeypatch):
    _stub_mcp_servers(monkeypatch, {
        'robinhood': {'transport': 'stdio', 'command': 'python',
                      'enabled': True},
        'github': {'transport': 'http', 'url': 'https://mcp.example/x',
                   'enabled': True},
        'old': {'transport': 'stdio', 'command': 'old-server',
                'enabled': False}})
    desc = approval._describe_tool('mcp_connect', {
        'name': '', '_servers_digest': 'deadbeef00'})
    assert 'ALL' in desc
    assert 'robinhood' in desc and 'github' in desc
    assert 'old' not in desc  # disabled servers are not connected
    assert 'deadbeef00' not in desc


def test_mcp_connect_unknown_server_says_so(monkeypatch):
    _stub_mcp_servers(monkeypatch, {})
    desc = approval._describe_tool('mcp_connect', {'name': 'nope'})
    assert 'nope' in desc and 'not in configuration' in desc


def test_face_tracking_names_direction():
    on = approval._describe_tool('face_tracking', {'on': True})
    assert 'ON' in on and 'face' in on and 'servo' in on
    off = approval._describe_tool('face_tracking', {'on': False})
    assert 'OFF' in off and 'face' in off


def test_reload_user_tools_names_code_execution():
    desc = approval._describe_tool('reload_user_tools', {})
    assert 'reload' in desc and 'user' in desc
    assert 'executes' in desc
    assert desc != "execute tool 'reload_user_tools'"


def test_trust_routine_names_the_grant():
    desc = approval._describe_tool('trust_routine', {'name': 'morning'})
    assert 'morning' in desc
    assert 'WITHOUT' in desc and 'asking' in desc


def test_skill_activate_names_the_grant():
    desc = approval._describe_tool('skill_activate',
                                   {'name': 'deep-research'})
    assert 'deep-research' in desc
    assert 'prompt catalog' in desc


def test_manage_background_job_start_names_command():
    desc = approval._describe_tool('manage_background_job', {
        'action': 'start', 'command': 'python train.py',
        'name': 'train'})
    assert 'start' in desc and 'train.py' in desc and 'train' in desc
    assert not desc.startswith("execute tool")


def test_manage_background_job_cancel_names_target():
    desc = approval._describe_tool('manage_background_job', {
        'action': 'cancel', 'job_id': 7})
    assert 'cancel' in desc and '7' in desc
    assert not desc.startswith("execute tool")
