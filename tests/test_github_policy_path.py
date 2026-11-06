"""Pass 26 B5#8: a GitHub repo path is not a local file path.

tool_github_push uses `filepath` solely as a remote path (URL-quoted
into /repos/{repo}/contents/{path}) and never reads a local file, so
it must not be in tool_policy.FILE_ARGUMENTS: with file_roots
configured, the dispatch gate otherwise rejected legitimate pushes
to repo paths outside the local roots. GitHub token + repo scoping
is the authorization boundary for this tool.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_gh_policy_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir_v20
_data_dir_v20._ROOT = os.path.join(_TMPHOME, 'ARIA')

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

approval = importlib.import_module('aria.approval')
dispatch = importlib.import_module('aria.tools.dispatch')
from aria import workspaces
from aria.tool_policy import FILE_ARGUMENTS

_CALLS = []
_ORIG_PUSH = dispatch._REGISTRY.get('github_push_file')


def _stub_push(args):
    _CALLS.append(dict(args or {}))
    return "Pushed 'docs/notes/hello.md' to ABerger94/ARIA-Agent."


dispatch._REGISTRY['github_push_file'] = _stub_push


def teardown_module(module=None):
    if _ORIG_PUSH is not None:
        dispatch._REGISTRY['github_push_file'] = _ORIG_PUSH
    else:
        dispatch._REGISTRY.pop('github_push_file', None)


def _fresh():
    dispatch._TURN_CALLS.clear()
    _CALLS.clear()
    approval._PENDING.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def test_github_push_file_not_in_file_arguments():
    assert 'github_push_file' not in FILE_ARGUMENTS


def test_github_push_repo_path_passes_dispatch_gate_with_roots_set():
    _fresh()
    # Local roots point somewhere unrelated to the repo path.
    roots_dir = os.path.join(_TMPHOME, 'local-roots')
    os.makedirs(roots_dir, exist_ok=True)
    workspaces.update_settings({'file_roots': [roots_dir]})
    res = dispatch.execute_tool(
        'github_push_file',
        {'repo': 'ABerger94/ARIA-Agent',
         'filepath': 'docs/notes/hello.md',
         'content': 'hi',
         'message': 'test'})[0]
    assert "outside this project" not in res, res
    assert len(_CALLS) == 1, f'push handler never ran; dispatch said: {res}'
    assert _CALLS[0]['filepath'] == 'docs/notes/hello.md'
