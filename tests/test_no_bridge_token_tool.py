"""Regression tests (2026-10-31, ChatGPT x ARIA loop pass 10):
the model could retrieve its own phone-bridge credential and
use it to approve its own held calls.

The chain was: `bridge_token` was an ordinary model-callable
tool (dispatch.py:706) returning BRIDGE_TOKEN verbatim, so a
model turn could fetch the credential, open the phone bridge
page through browser_agent (whose private-host filter misses
non-dotted IP spellings), and click the phone page's Confirm
button — whose label "Confirm" does not match the irreversible
regex — so POST /api/approvals/resolve called approve_token()
on the model's own held call. That is the same self-approval
class removed in 2026-10-28 (approve/deny/set_approval_mode),
reopened by the new phone approval surface (2026-10-31).

Fix contract pinned here: `bridge_token` is NOT in the tool
registry, NOT in the model-facing schemas, NOT in any toolkit,
and NOT in the command guide, so a model turn calling it is
rejected as unknown and the credential never reaches the
model through a tool. The trusted paths still work: the
bridge server itself still authenticates with BRIDGE_TOKEN
(aria/config.py, aria/bridge.py untouched), and a real phone
still resolves approvals through /api/approvals/resolve with
its own session — only the model's on-demand copy is gone.
Alek gets the token from the dashboard pairing page instead.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_no_bridge_token_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir_v20
_data_dir_v20._ROOT = os.path.join(_TMPHOME, 'ARIA')  # V20: state stores resolve via data_dir (install root), not HOME

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

dispatch = importlib.import_module('aria.tools.dispatch')
schemas = importlib.import_module('aria.tools.schemas')
config = importlib.import_module('aria.config')


def test_bridge_token_is_not_a_model_tool():
    assert 'bridge_token' not in dispatch._REGISTRY
    declared = {d['name'] for d in schemas.ALL_FUNCTION_DECLARATIONS}
    assert 'bridge_token' not in declared
    for _kit, spec in schemas.TOOLKITS.items():
        assert 'bridge_token' not in spec['tools'], _kit
    guide_tools = {entry[1] for entry in schemas.COMMAND_GUIDE}
    assert 'bridge_token' not in guide_tools


def test_model_call_is_rejected_and_leaks_no_token():
    res, _ok = dispatch.execute_tool('bridge_token', {})
    assert 'bridge token is:' not in res.lower()
    # Unknown-tool rejection (pass 1 contract), not a credential.
    assert 'unknown' in res.lower() or 'no such tool' in res.lower() or 'not' in res.lower()
    assert config.BRIDGE_TOKEN not in res


def test_bridge_credential_still_exists_for_the_server():
    # The server-side credential is untouched: only the model's
    # tool copy is gone, so a real phone can still pair and log in.
    assert isinstance(config.BRIDGE_TOKEN, str)
    assert len(config.BRIDGE_TOKEN) > 6
    bridge_src = open(os.path.join(PKG, 'bridge.py'), encoding='utf-8').read()
    assert 'BRIDGE_TOKEN' in bridge_src
    assert '/api/approvals/resolve' in bridge_src
    # The brain prompt must not name the removed tool either.
    brain_src = open(os.path.join(PKG, 'agent', 'brain.py'), encoding='utf-8').read()
    assert '(bridge_token)' not in brain_src
