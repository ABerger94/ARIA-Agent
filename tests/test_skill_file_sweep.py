"""skill_file_sweep: bullets are not filenames, failures are not reads
(pass 25 P2#20).

The listing is "- report.txt" bullet lines under a header. The old
code passed the raw line (bullet included) to read_file and counted
the "does not exist" failure as a successful read.
"""
import os
import sys
import types
import importlib

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
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

skills = importlib.import_module('aria.tools.skills')
from aria.outcome import FAILED, ResultText  # noqa: E402


def _call_factory(files, calls, readable=None):
    readable = set(files if readable is None else readable)

    def call(name, args):
        calls.append((name, args))
        if name == 'list_workspace':
            return "Workspace files:\n" + "\n".join(f"- {f}" for f in files)
        if name == 'read_file':
            fn = args.get('filename', '')
            if fn in readable:
                return f"CONTENT of {fn}"
            return ResultText(f"File '{fn}' does not exist.", FAILED)
        raise AssertionError(name)
    return call


def test_bullet_stripped_and_read_counted():
    calls = []
    call = _call_factory(['report.txt', 'notes.md'], calls)
    out = skills.skill_file_sweep('report summary', call)
    assert 'read 1 file(s) (report.txt)' in out, out
    assert 'CONTENT of report.txt' in out, out
    read_calls = [a for n, a in calls if n == 'read_file']
    assert read_calls == [{'filename': 'report.txt'}], read_calls


def test_failed_read_is_not_counted():
    calls = []
    # ghost.txt is listed but read_file fails for it (e.g. deleted
    # between listing and read): the sweep must report no match,
    # not a successful read of a failure message.
    call = _call_factory(['ghost.txt'], calls, readable=set())
    out = skills.skill_file_sweep('ghost', call)
    assert 'no workspace files matched' in out, out
    assert 'read 1 file' not in out, out
