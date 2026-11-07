"""Pass 27 #27: concurrent first calls must import a user tool once.

Before: _make_lazy_handler guarded the first import with a bare
``if "run" not in state`` on a plain dict, with no lock anywhere
in the file — two concurrent first calls could both enter
_load_module_from_path and run the module's top-level code twice,
contradicting the docstring's "at most once per process".

Fix contract pinned here:
- N threads calling a fresh handler at the same instant import
  the module exactly once (the top-level marker fires once) and
  all get the tool's result;
- the same holds for the #18 re-import path: concurrent calls
  carrying a fresh approval for new bytes import the new bytes
  exactly once;
- sequential calls still import once (the lock must not break
  the normal path).
"""
import importlib
import os
import sys
import tempfile
import threading
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_usertools_conc_')
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


def _tool_src(name, marker, version, run_body):
    # Top-level code appends one marker line and sleeps, so the
    # import takes long enough that N threads released together
    # would all be inside it at once without the lock.
    return ('\n'
            'import time\n'
            f'_MARKER = {marker!r}\n'
            'with open(_MARKER, "a", encoding="utf-8") as _f:\n'
            '    _f.write("import\\n")\n'
            'time.sleep(0.1)\n'
            f'TOOL_NAME = "{name}"\n'
            f'TOOL_DESCRIPTION = "Probe tool {name}"\n'
            'TOOL_PARAMETERS = {"type": "OBJECT", "properties": {}, '
            '"required": []}\n'
            'def run(args):\n'
            f'    {run_body}\n')


def _write(scan, name, marker, version, run_body):
    (scan / f"{name}.py").write_text(
        _tool_src(name, marker, version, run_body))


def _load(tmp_path, name, marker, version, run_body):
    scan = tmp_path / "user_tools"
    scan.mkdir(exist_ok=True)
    _write(scan, name, marker, version, run_body)
    usertools.load_user_tools_into_registry(str(scan))
    return scan, usertools._USER_TOOLS[name][0]


def _marker_count(marker):
    try:
        with open(marker, encoding="utf-8") as f:
            return len(f.read().splitlines())
    except FileNotFoundError:
        return 0


def _call_concurrently(handler, args_list, n):
    barrier = threading.Barrier(n)
    results = [None] * n

    def _call(i):
        barrier.wait(timeout=10)
        results[i] = handler(args_list[i])

    threads = [threading.Thread(target=_call, args=(i,))
               for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not any(t.is_alive() for t in threads), "a call hung"
    return results


def test_concurrent_first_calls_import_once(tmp_path):
    marker = str(tmp_path / "imports.log")
    _scan, handler = _load(tmp_path, "conc_probe_a", marker, "V1",
                           'return "V1"')
    results = _call_concurrently(handler, [{}] * 8, 8)
    assert results == ["V1"] * 8, results
    assert _marker_count(marker) == 1, (
        "top-level code ran more than once on concurrent first calls")


def test_concurrent_reimport_for_new_digest_imports_once(tmp_path):
    marker = str(tmp_path / "imports.log")
    scan, handler = _load(tmp_path, "conc_probe_b", marker, "V1",
                          'return "V1"')
    assert handler({}) == "V1"
    assert _marker_count(marker) == 1
    # Replace the file; a fresh approval for the new bytes.
    _write(scan, "conc_probe_b", marker, "V2", 'return "V2"')
    fresh = usertools.freeze_user_tool_args("conc_probe_b", {})
    results = _call_concurrently(handler, [dict(fresh)] * 8, 8)
    assert results == ["V2"] * 8, results
    assert _marker_count(marker) == 2, (
        "the new revision must be imported exactly once")


def test_sequential_calls_import_once(tmp_path):
    marker = str(tmp_path / "imports.log")
    _scan, handler = _load(tmp_path, "conc_probe_c", marker, "V1",
                           'return "V1"')
    assert [handler({}) for _ in range(3)] == ["V1"] * 3
    assert _marker_count(marker) == 1
