"""Pass 26 B3#4 (user-tool part): user-tool exceptions must return
typed failures, not untyped strings.

Verified against the code at b1ba4ad:
- usertools.py _make_lazy_handler() catches import failure, a
  missing run(), and run() exceptions and returns "[user tool
  'X' failed: ...]" plain strings, which outcome.normalize()
  maps to DONE — a failed user tool reads as a success.

Fix contract pinned here (the legacy_error_results pattern):
- the failure text is kept verbatim (existing tests pin it),
  but typed as FAILED, so outcome.normalize() yields FAILED.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_usertools_typed_')
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


def _tool_src(name, run_body='return "PROBE_OK"', extra=""):
    return ('\n'
            f'TOOL_NAME = "{name}"\n'
            f'TOOL_DESCRIPTION = "Probe tool {name}"\n'
            'TOOL_PARAMETERS = {"type": "OBJECT", "properties": {}, '
            '"required": []}\n'
            f'{extra}\n'
            'def run(args):\n'
            f'    {run_body}\n')


def _load(tmp_path, name, **kw):
    scan = tmp_path / "user_tools"
    scan.mkdir(exist_ok=True)
    (scan / f"{name}.py").write_text(_tool_src(name, **kw))
    return usertools.load_user_tools_into_registry(str(scan))


def test_raising_user_tool_is_typed_failed(tmp_path):
    _load(tmp_path, "typed_fail_a",
          run_body='raise RuntimeError("kaboom")')
    handler = usertools._USER_TOOLS["typed_fail_a"][0]
    out = handler({})
    assert "[user tool 'typed_fail_a' failed" in out, out
    assert "kaboom" in out, out
    assert outcome.normalize(out).status == "failed", out


def test_import_failure_is_typed_failed(tmp_path):
    scan = tmp_path / "user_tools"
    scan.mkdir(exist_ok=True)
    (scan / "typed_fail_b.py").write_text(
        _tool_src("typed_fail_b") + "\nraise ImportError('no such module')\n")
    usertools.load_user_tools_into_registry(str(scan))
    handler = usertools._USER_TOOLS["typed_fail_b"][0]
    out = handler({})
    assert "[user tool 'typed_fail_b' failed" in out, out
    assert outcome.normalize(out).status == "failed", out
    # A cached broken module still reports FAILED on repeat calls.
    assert outcome.normalize(handler({})).status == "failed"


def test_missing_run_is_typed_failed(tmp_path):
    scan = tmp_path / "user_tools"
    scan.mkdir(exist_ok=True)
    (scan / "typed_fail_c.py").write_text(
        _tool_src("typed_fail_c") + "\nrun = 42\n")
    usertools.load_user_tools_into_registry(str(scan))
    handler = usertools._USER_TOOLS["typed_fail_c"][0]
    out = handler({})
    assert out.startswith("[user tool 'typed_fail_c' failed"), out
    assert outcome.normalize(out).status == "failed", out


def test_successful_user_tool_still_typed_done(tmp_path):
    _load(tmp_path, "typed_ok_d")
    handler = usertools._USER_TOOLS["typed_ok_d"][0]
    assert handler({}) == "PROBE_OK"
    assert outcome.normalize("PROBE_OK").status == "done"
