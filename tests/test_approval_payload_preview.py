"""Pass 27 #15: approval previews must show the material payload,
not a summary.

Before: discovered MCP calls omitted arguments entirely, mcp_setup
showed command/url but env/headers only as counts, Python showed
three lines capped at 300 chars, email showed 200 body chars,
writes showed char counts, and mission plan steps stopped at 160
chars — all without any truncation indicator, so a cut preview
could pass for the whole payload. Now descriptions carry the
redacted payload whole up to a 2000-char bound, with an explicit
"[truncated: N more chars]" indicator when the bound cuts it.
"""
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_approval_payload_')
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
missions = importlib.import_module('aria.missions')


def test_mcp_call_shows_args():
    desc = approval._describe_tool(
        'mcp_probe__do_thing',
        {'query': 'delete everything', 'confirm': True})
    assert 'delete everything' in desc, desc
    assert 'confirm' in desc, desc


def test_python_shows_whole_code_with_indicator():
    code = "\n".join(f"line_{i} = {i}" for i in range(300))
    desc = approval._describe_tool('run_python_code', {'code': code})
    assert 'line_0' in desc and 'line_299' not in desc, desc
    assert '[truncated:' in desc and 'more chars]' in desc, desc
    short = approval._describe_tool(
        'run_python_code', {'code': 'import os\nos.system("x")\n'})
    assert 'os.system' in short and '[truncated:' not in short, short


def test_email_body_whole_and_flagged_when_cut():
    body = 'Please wire $1000 to account 12345 immediately. ' * 100
    desc = approval._describe_tool('send_email', {
        'to': 'a@example.invalid', 'subject': 'Hi', 'body': body})
    assert 'wire $1000' in desc, desc
    assert '[truncated:' in desc, desc


def test_write_file_shows_content_not_just_count():
    desc = approval._describe_tool('write_file', {
        'filename': 'x.txt', 'content': 'SECRET_PAYLOAD_HERE'})
    assert 'SECRET_PAYLOAD_HERE' in desc, desc


def test_fallback_tool_shows_redacted_args():
    desc = approval._describe_tool('some_unknown_tool', {
        'path': '/tmp/x', 'password': 'hunter2'})
    assert '/tmp/x' in desc, desc
    assert 'hunter2' not in desc, desc
    assert '[redacted]' in desc, desc


def test_payload_preview_redacts_credential_fields():
    out = approval._payload_preview(
        {'api_key': 'sk-live-123', 'query': 'hello'})
    assert 'sk-live-123' not in out, out
    assert 'hello' in out, out


def test_plan_text_shows_full_step_args():
    m = {'id': 7, 'goal': 'do the thing', 'deadline': '',
         'envelope': {'allowed_tools': ['write_file'],
                      'spend_cap': 0, 'pre_approved_irreversible': []}}
    steps = [{'idx': 0, 'tool': 'write_file',
              'args': {'filename': 'x.txt',
                       'content': 'PAYLOAD_MARKER_' + 'y' * 3000}}]
    text = missions._plan_text(m, steps)
    assert 'PAYLOAD_MARKER_' in text, text
    assert '[truncated:' in text, text
    short = missions._plan_text(m, [{'idx': 0, 'tool': 'write_file',
                                     'args': {'filename': 'x.txt'}}])
    assert 'x.txt' in short and '[truncated:' not in short, short
