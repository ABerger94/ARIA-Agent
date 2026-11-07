"""Pass 27 #29: user-tool return coercion must not discard failure status.

Before: the handler did ``return str(run_fn(...))``, flattening a
ResultText(FAILED) or a {"status": "failed", ...} dict to a plain
string; dispatch's normalize() then found no status marker and
read DONE — a failed user tool reported as a success.

Fix contract pinned here:
- a ResultText / ToolResult return passes through intact, so
  normalize() reads its status;
- a dict with a status outcome knows passes through intact;
- a plain string still coerces exactly as before;
- a plain dict WITHOUT a status still coerces to its str form
  (it is data, not an outcome — and must not crash dispatch).
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_usertools_struct_')
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

outcome = importlib.import_module('aria.outcome')
usertools = importlib.import_module('aria.tools.usertools')
dispatch = importlib.import_module('aria.tools.dispatch')


def _src(name, run_body, extra=""):
    return ('\n'
            f'{extra}\n'
            f'TOOL_NAME = "{name}"\n'
            f'TOOL_DESCRIPTION = "Probe tool {name}"\n'
            'TOOL_PARAMETERS = {"type": "OBJECT", "properties": {}, '
            '"required": []}\n'
            'def run(args):\n'
            f'    {run_body}\n')


def _load(tmp_path, name, run_body, extra=""):
    scan = tmp_path / "user_tools"
    scan.mkdir(exist_ok=True)
    (scan / f"{name}.py").write_text(_src(name, run_body, extra))
    usertools.load_user_tools_into_registry(str(scan))
    return usertools._USER_TOOLS[name][0]


def test_resulttext_failed_passes_through(tmp_path):
    handler = _load(tmp_path, "struct_probe_a",
                    'return _o.ResultText("disk full", _o.FAILED)',
                    extra='from aria import outcome as _o')
    out = handler({})
    assert isinstance(out, outcome.ResultText), out
    assert outcome.normalize(out).status == "failed", out
    assert "disk full" in str(out)


def test_status_dict_failed_passes_through(tmp_path):
    handler = _load(tmp_path, "struct_probe_b",
                    'return {"status": "failed", "text": "nope"}')
    out = handler({})
    assert out == {"status": "failed", "text": "nope"}, out
    assert outcome.normalize(out).status == "failed", out


def test_toolresult_passes_through(tmp_path):
    handler = _load(tmp_path, "struct_probe_c",
                    'return _o.ToolResult("failed", "kaput")',
                    extra='from aria import outcome as _o')
    out = handler({})
    assert isinstance(out, outcome.ToolResult), out
    assert outcome.normalize(out).status == "failed", out


def test_plain_string_unchanged(tmp_path):
    handler = _load(tmp_path, "struct_probe_d", 'return "ok"')
    out = handler({})
    assert out == "ok", out
    assert outcome.normalize(out).status == "done", out


def test_plain_dict_without_status_coerces(tmp_path):
    handler = _load(tmp_path, "struct_probe_e",
                    'return {"x": 1}')
    out = handler({})
    assert isinstance(out, str) and "x" in out, out
    assert outcome.normalize(out).status == "done", out


def test_resulttext_done_passes_through(tmp_path):
    handler = _load(tmp_path, "struct_probe_f",
                    'return _o.ResultText("fine")',
                    extra='from aria import outcome as _o')
    out = handler({})
    assert outcome.normalize(out).status == "done", out
