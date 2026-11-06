"""User-defined tools must not bypass the approval / safe-mode policy,
and loading one must not execute its code (pass 25 P1#3).

Before this fix: load_user_tools exec_module()d every file at boot
(ungated), and the registered tool had no policy classification at
all — needs_approval() returned False in confirm-risky and
is_safe_mode_blocked() returned False, so a user tool's arbitrary
Python ran with no approval and no safe-mode block.

Fix contract pinned here, through the real loader and the real
policy classifiers:
- loading a tool (scan + register) does NOT run its top-level code
  (the import is deferred to the handler's first call);
- a loaded user tool is approval-gated under confirm-risky and
  hard-blocked in safe mode (config.is_user_tool classifier);
- the first call imports the module once (top-level code runs
  exactly once) and later calls reuse it;
- a file with a bad name or a syntax error is skipped at load
  without executing anything;
- a reload drops the policy classification of removed tools.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_usertools_policy_')
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

config = importlib.import_module('aria.config')
approval = importlib.import_module('aria.approval')
dispatch = importlib.import_module('aria.tools.dispatch')
usertools = importlib.import_module('aria.tools.usertools')


def _tool_src(name, marker, run_body='return "PROBE_OK"'):
    return ('\n'
            f'TOOL_NAME = "{name}"\n'
            f'TOOL_DESCRIPTION = "Probe tool {name}"\n'
            'TOOL_PARAMETERS = {"type": "OBJECT", "properties": {}, '
            '"required": []}\n'
            f'open({marker!r}, "a").write("imported\\n")\n'
            '\n'
            'def run(args):\n'
            f'    {run_body}\n')


def _load(tmp_path, name, marker, run_body='return "PROBE_OK"'):
    scan = tmp_path / "user_tools"
    scan.mkdir(exist_ok=True)
    (scan / f"{name}.py").write_text(_tool_src(name, marker, run_body))
    return usertools.load_user_tools_into_registry(str(scan))


def test_load_does_not_execute_module(tmp_path):
    marker = str(tmp_path / "imported.txt")
    loaded = _load(tmp_path, "policy_probe_a", marker)
    assert loaded == ["policy_probe_a"]
    assert not os.path.exists(marker), \
        'loading a user tool executed its top-level code'
    assert "policy_probe_a" in dispatch.get_registered_tools()
    assert config.is_user_tool("policy_probe_a")


def test_user_tool_is_gated(tmp_path):
    marker = str(tmp_path / "imported.txt")
    _load(tmp_path, "policy_probe_b", marker)
    assert config.is_safe_mode_blocked("policy_probe_b")
    approval.tool_set_approval_mode('confirm-risky')
    try:
        assert approval.needs_approval("policy_probe_b", {})
    finally:
        approval.tool_set_approval_mode('auto')
    # The banner names the tool's own description, not a bare name.
    desc = approval._describe_tool("policy_probe_b", {})
    assert "policy_probe_b" in desc and "Probe tool" in desc, desc
    # A plain read tool stays ungated (control).
    assert not approval.needs_approval("phone_status", {})


def test_first_call_imports_once(tmp_path):
    marker = str(tmp_path / "imported.txt")
    loaded = _load(tmp_path, "policy_probe_c", marker)
    assert loaded == ["policy_probe_c"]
    handler = usertools._USER_TOOLS["policy_probe_c"][0]
    assert handler({}) == "PROBE_OK"
    assert handler({}) == "PROBE_OK"
    with open(marker) as f:
        assert f.read() == "imported\n", \
            'top-level code ran more than once (or not at all)'


def test_bad_name_and_syntax_error_skipped_without_exec(tmp_path):
    scan = tmp_path / "user_tools"
    scan.mkdir()
    marker = str(tmp_path / "imported.txt")
    # Bad TOOL_NAME: skipped at load, top-level code never runs.
    (scan / "bad_name.py").write_text(
        _tool_src("Bad-Name!", marker))
    # Syntax error: skipped at load, top-level code never runs.
    (scan / "broken.py").write_text(
        'open(%r, "a").write("imported\\n")\nTOOL_NAME = (\n' % marker)
    loaded = usertools.load_user_tools_into_registry(str(scan))
    assert loaded == []
    assert not os.path.exists(marker)
    assert not config.is_user_tool("Bad-Name!")
    assert "bad_name" not in dispatch.get_registered_tools()


def test_non_callable_run_fails_at_first_call(tmp_path):
    marker = str(tmp_path / "imported.txt")
    scan = tmp_path / "user_tools"
    scan.mkdir()
    (scan / "policy_probe_d.py").write_text(
        _tool_src("policy_probe_d", marker)
        + "\nrun = 42\n")
    loaded = usertools.load_user_tools_into_registry(str(scan))
    assert loaded == ["policy_probe_d"]
    handler = usertools._USER_TOOLS["policy_probe_d"][0]
    out = handler({})
    assert "no run(args) function defined" in out, out
    # The top-level code ran exactly once — the second call does
    # not re-execute the module.
    assert handler({}).startswith("[user tool 'policy_probe_d' failed")
    with open(marker) as f:
        assert f.read() == "imported\n"


def test_reload_drops_policy_of_removed_tool(tmp_path):
    marker = str(tmp_path / "imported.txt")
    scan = tmp_path / "user_tools"
    scan.mkdir()
    path = scan / "policy_probe_e.py"
    path.write_text(_tool_src("policy_probe_e", marker))
    usertools._SCAN_DIR = str(scan)
    try:
        usertools.tool_reload_user_tools()
        assert config.is_user_tool("policy_probe_e")
        assert "policy_probe_e" in dispatch.get_registered_tools()
        path.unlink()
        out = usertools.tool_reload_user_tools()
        assert "policy_probe_e" in out, out
        assert not config.is_user_tool("policy_probe_e")
        assert "policy_probe_e" not in dispatch.get_registered_tools()
        # A removed tool is no longer approval-gated (it is gone).
        assert not approval.needs_approval("policy_probe_e", {})
    finally:
        usertools._SCAN_DIR = os.path.join(
            os.path.dirname(os.path.abspath(usertools.__file__)),
            "user_tools")
