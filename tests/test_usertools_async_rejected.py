"""Pass 27 #28: async user tools are rejected, never silently str()'d.

Before: the AST scan accepted ``async def run`` as a handler, but
the handler did ``str(run_fn(...))`` with no await — the body
never ran and the "<coroutine object ...>" string normalized as
DONE.

Fix contract pinned here:
- a tool whose run is async is skipped at scan time (never
  loaded, never callable);
- when both forms are defined, the LAST one wins at runtime, so
  that is the one judged (async-last skips, sync-last loads);
- the handler refuses a coroutine function even when the file
  changed after the scan (fresh approval for async bytes): typed
  FAILED, and the coroutine body never executes.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_usertools_async_')
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


def _src(name, run_defs):
    return ('\n'
            f'TOOL_NAME = "{name}"\n'
            f'TOOL_DESCRIPTION = "Probe tool {name}"\n'
            'TOOL_PARAMETERS = {"type": "OBJECT", "properties": {}, '
            '"required": []}\n'
            f'{run_defs}\n')


def _load_source(tmp_path, name, run_defs):
    scan = tmp_path / "user_tools"
    scan.mkdir(exist_ok=True)
    (scan / f"{name}.py").write_text(_src(name, run_defs))
    usertools.load_user_tools_into_registry(str(scan))
    return scan


def test_async_run_skipped_at_scan(tmp_path):
    _load_source(tmp_path, "async_probe_a",
                 'async def run(args):\n    return "ASYNC_RAN"')
    assert "async_probe_a" not in usertools._USER_TOOLS, (
        "an async run() must not load as a tool")


def test_async_last_wins_and_skips(tmp_path):
    _load_source(tmp_path, "async_probe_b",
                 'def run(args):\n    return "SYNC"\n'
                 'async def run(args):\n    return "ASYNC_RAN"')
    assert "async_probe_b" not in usertools._USER_TOOLS, (
        "the last run def wins at runtime — an async last def must skip")


def test_sync_last_wins_and_loads(tmp_path):
    scan = _load_source(tmp_path, "async_probe_c",
                        'async def run(args):\n    return "ASYNC_RAN"\n'
                        'def run(args):\n    return "SYNC"')
    assert "async_probe_c" in usertools._USER_TOOLS
    handler = usertools._USER_TOOLS["async_probe_c"][0]
    assert handler({}) == "SYNC"


def test_handler_refuses_async_after_scan(tmp_path):
    scan = _load_source(tmp_path, "async_probe_d",
                        'def run(args):\n    return "V1"')
    handler = usertools._USER_TOOLS["async_probe_d"][0]
    assert handler({}) == "V1"
    # Replace the file with an async run after the scan; a fresh
    # approval for the new bytes passes the digest check.
    (scan / "async_probe_d.py").write_text(_src(
        "async_probe_d",
        'async def run(args):\n    return "ASYNC_RAN"'))
    fresh = usertools.freeze_user_tool_args("async_probe_d", {})
    out = handler(fresh)
    assert "ASYNC_RAN" not in out, "the coroutine body must never run"
    assert "async" in out.lower(), out
    assert outcome.normalize(out).status == "failed", out
