"""Pass 27 #18: a user-tool approval must be bound to its source revision.

Before: metadata was scanned once while first execution imported
the mutable file later, and pending approvals froze arguments
without a source digest — replacing the file between scan and
invocation executed the replacement under the old declaration.

Fix contract pinned here:
- _freeze_park_args binds the CURRENT source digest into parked
  user-tool args (ValueError when the source is unreadable, so
  no revisionless approval is parked);
- the handler verifies the file still matches the approved digest
  BEFORE importing (the import itself runs top-level code), and
  refuses typed FAILED on mismatch or unreadable source;
- an approval for new bytes executes the NEW bytes, not the stale
  cached module;
- the approved digest never reaches the tool's run(args);
- calls with no digest (auto mode) run as before.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_usertools_rev_')
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


def _tool_src(name, run_body):
    return ('\n'
            f'TOOL_NAME = "{name}"\n'
            f'TOOL_DESCRIPTION = "Probe tool {name}"\n'
            'TOOL_PARAMETERS = {"type": "OBJECT", "properties": {}, '
            '"required": []}\n'
            'def run(args):\n'
            f'    {run_body}\n')


def _write(scan, name, run_body):
    (scan / f"{name}.py").write_text(_tool_src(name, run_body))


def _load(tmp_path, name, run_body):
    scan = tmp_path / "user_tools"
    scan.mkdir(exist_ok=True)
    _write(scan, name, run_body)
    usertools.load_user_tools_into_registry(str(scan))
    return scan, usertools._USER_TOOLS[name][0]


def test_approved_revision_runs_and_digest_hidden(tmp_path):
    _scan, handler = _load(tmp_path, "rev_probe_a",
                           'return "V1:" + str(sorted(args.keys()))')
    frozen = usertools.freeze_user_tool_args("rev_probe_a", {"x": 1})
    assert "_source_digest" in frozen and frozen["x"] == 1, frozen
    out = handler(frozen)
    assert out == "V1:['x']", out


def test_replaced_source_refuses_under_old_approval(tmp_path):
    scan, handler = _load(tmp_path, "rev_probe_b",
                          'return "V1"')
    frozen = usertools.freeze_user_tool_args("rev_probe_b", {})
    assert handler(frozen) == "V1"
    # Replace the file between approval and execution.
    _write(scan, "rev_probe_b", 'return "V2"')
    out = handler(frozen)
    assert "refused" in out and "changed since approval" in out, out
    assert "V2" not in out, out
    assert outcome.normalize(out).status == "failed", out


def test_new_approval_runs_new_bytes_not_cached_module(tmp_path):
    scan, handler = _load(tmp_path, "rev_probe_c",
                          'return "V1"')
    assert handler({}) == "V1"
    _write(scan, "rev_probe_c", 'return "V2"')
    fresh = usertools.freeze_user_tool_args("rev_probe_c", {})
    assert handler(fresh) == "V2", "stale cached module ran instead"


def test_freeze_unknown_tool_raises(tmp_path):
    try:
        usertools.freeze_user_tool_args("no_such_tool_xyz", {})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown tool")


def test_dispatch_freeze_binds_user_tool_digest(tmp_path):
    _scan, _handler = _load(tmp_path, "rev_probe_d",
                            'return "ok"')
    gate_args = dispatch._freeze_park_args("rev_probe_d", {"q": "z"})
    assert "_source_digest" in gate_args, gate_args
    assert gate_args["q"] == "z", gate_args
