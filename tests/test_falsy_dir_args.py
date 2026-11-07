"""Pass 27 #3: wrong-typed, falsy directory arguments must not bypass
project file-root checks.

Before the fix, dispatch skipped root checks for falsy paths
(`if not path: continue`), required-argument validation accepted
`false`, and _resolve_dir(False) fell back to the process
directory — file_organize(directory=false, dry_run=false) moved
files outside the configured roots. Now a wrong-typed file
argument is an argument error at the dispatcher, a wrong-typed
required argument is invalid at validation, and _resolve_dir
refuses non-string/empty input instead of defaulting to cwd.
"""
import os
import sys
import types
import tempfile

_TMPHOME = tempfile.mkdtemp(prefix='aria_falsy_dir_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
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
import importlib
approval = importlib.import_module('aria.approval')
dispatch = importlib.import_module('aria.tools.dispatch')
fileops = importlib.import_module('aria.tools.fileops')
from aria.tools.sandbox import missing_required_args  # noqa: E402

_CALLS = []
_ORIG = dispatch._REGISTRY.get('file_organize')


def _stub_organize(args):
    _CALLS.append(('file_organize', dict(args or {})))
    return 'STUB_ORGANIZED'


dispatch._REGISTRY['file_organize'] = _stub_organize


def _fresh():
    dispatch._TURN_CALLS.clear()
    _CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def teardown_module(module=None):
    if _ORIG is not None:
        dispatch._REGISTRY['file_organize'] = _ORIG
    else:
        dispatch._REGISTRY.pop('file_organize', None)


def test_false_directory_never_executes(tmp_path):
    _fresh()
    res, _ok = dispatch.execute_tool(
        'file_organize', {'directory': False, 'dry_run': False})
    assert _CALLS == [], f'falsy directory executed the handler: {_CALLS}'
    assert '[Argument error:' in res, res


def test_numeric_directory_never_executes(tmp_path):
    _fresh()
    res, _ok = dispatch.execute_tool(
        'file_organize', {'directory': 0, 'dry_run': False})
    assert _CALLS == []
    assert '[Argument error:' in res, res


def test_string_directory_still_executes(tmp_path):
    _fresh()
    res, _ok = dispatch.execute_tool(
        'file_organize', {'directory': str(tmp_path), 'dry_run': True})
    assert 'STUB_ORGANIZED' in res, res
    assert len(_CALLS) == 1


def test_missing_required_args_rejects_wrong_types():
    decls = {'file_organize': {'parameters': {
        'required': ['directory', 'dry_run'],
        'properties': {'directory': {'type': 'STRING'},
                       'dry_run': {'type': 'BOOLEAN'}}}}}
    assert missing_required_args(
        'file_organize', {'directory': False, 'dry_run': False},
        decls) == ['directory']
    assert missing_required_args(
        'file_organize', {'directory': '/tmp', 'dry_run': 'yes'},
        decls) == ['dry_run']
    assert missing_required_args(
        'file_organize', {'directory': '/tmp', 'dry_run': True},
        decls) == []
    # Lowercase schema types (as used by some unit decls) work too.
    decls['file_organize']['parameters']['properties']['directory']['type'] = 'string'
    assert missing_required_args(
        'file_organize', {'directory': 42, 'dry_run': True},
        decls) == ['directory']


def test_resolve_dir_refuses_falsy_input(tmp_path, monkeypatch):
    victim = tmp_path / 'victim.txt'
    victim.write_text('do not move me', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    _d, err = fileops._resolve_dir(False)
    assert err is not None and 'must be a text path' in err, err
    _d, err = fileops._resolve_dir('')
    assert err is not None and 'must be a text path' in err, err
    out = fileops.tool_file_organize(False, dry_run=False)
    assert 'must be a text path' in out, out
    assert victim.exists()
    assert not (tmp_path / 'Documents' / 'victim.txt').exists()
