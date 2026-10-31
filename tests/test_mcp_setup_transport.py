"""Regression tests (2026-10-30, Alek-directed fix 3): mcp_setup
could not express transport="http".

tool_mcp_setup had no transport parameter and inferred "sse" for
every url-only setup. Robinhood's MCP server
(https://agent.robinhood.com/mcp/trading) is a Streamable HTTP
endpoint; aria/mcp.py add_server already supports transport="http",
but the tool could not say it, so ARIA saved the server as SSE and
the SSE client could not talk to it (live failure on Alek's laptop,
2026-10-30).

Fix contract pinned here, against the REAL add_server (settings
file swapped to a temp file, the test_headless pattern):
- explicit transport="http" with a url saves transport "http";
- explicit "sse" is respected (case-insensitive);
- omitted transport keeps the legacy inference EXACTLY
  (url -> sse, command -> stdio), so existing setups are unaffected;
- an invalid transport errors and saves nothing (a pre-existing
  entry under the same name is left untouched);
- the mcp_setup schema declaration exposes the transport property
  and tells the model when to use "http";
- the dispatcher passes transport through to the tool.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_mcp_transport_')
os.environ['HOME'] = _TMPHOME
PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
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
for _name in ('aria.scheduler', 'aria.vision', 'aria.hardware',
              'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)

approval = importlib.import_module('aria.approval')
config = importlib.import_module('aria.config')
dispatch = importlib.import_module('aria.tools.dispatch')
builtins = importlib.import_module('aria.tools.builtins')
schemas = importlib.import_module('aria.tools.schemas')
mcp_mod = importlib.import_module('aria.mcp')

_TMPSETTINGS = os.path.join(_TMPHOME, 'settings.json')
_ORIG_SETTINGS_FILE = config.SETTINGS_FILE
config.SETTINGS_FILE = _TMPSETTINGS

ROBINHOOD_URL = 'https://agent.robinhood.com/mcp/trading'


def _fresh():
    if os.path.exists(_TMPSETTINGS):
        os.remove(_TMPSETTINGS)
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def teardown_module(module=None):
    config.SETTINGS_FILE = _ORIG_SETTINGS_FILE


def test_explicit_http_saves_http():
    _fresh()
    msg = builtins.tool_mcp_setup(
        name='robinhood', url=ROBINHOOD_URL, transport='http')
    assert 'saved (http)' in msg, msg
    entry = mcp_mod.get_servers()['robinhood']
    assert entry['transport'] == 'http', entry
    assert entry['url'] == ROBINHOOD_URL, entry
    print('ok explicit_http')


def test_explicit_sse_respected_case_insensitive():
    _fresh()
    msg = builtins.tool_mcp_setup(
        name='legacy', url='https://example.com/sse', transport='SSE')
    assert 'saved (sse)' in msg, msg
    assert mcp_mod.get_servers()['legacy']['transport'] == 'sse'
    print('ok explicit_sse')


def test_omitted_transport_keeps_legacy_inference():
    _fresh()
    msg = builtins.tool_mcp_setup(name='web', url='https://example.com/sse')
    assert 'saved (sse)' in msg, msg
    assert mcp_mod.get_servers()['web']['transport'] == 'sse'
    msg = builtins.tool_mcp_setup(
        name='fs', command='npx', args='["-y", "x"]')
    assert 'saved (stdio)' in msg, msg
    assert mcp_mod.get_servers()['fs']['transport'] == 'stdio'
    print('ok legacy_inference')


def test_invalid_transport_saves_nothing():
    _fresh()
    builtins.tool_mcp_setup(
        name='robinhood', url=ROBINHOOD_URL, transport='http')
    before = mcp_mod.get_servers()['robinhood']
    msg = builtins.tool_mcp_setup(
        name='robinhood', url=ROBINHOOD_URL, transport='carrier-pigeon')
    assert 'Unknown transport' in msg, msg
    # The bad call must not clobber or add anything.
    assert mcp_mod.get_servers()['robinhood'] == before
    msg = builtins.tool_mcp_setup(
        name='newbad', url='https://example.com/x', transport='bogus')
    assert 'Unknown transport' in msg, msg
    assert 'newbad' not in mcp_mod.get_servers()
    print('ok invalid_saves_nothing')


def test_schema_exposes_transport():
    decl = schemas.get_tool_decls_by_name()['mcp_setup']
    props = decl['parameters']['properties']
    assert 'transport' in props, sorted(props)
    assert props['transport']['type'] == 'STRING'
    desc = decl['description']
    assert 'Streamable HTTP' in desc and 'Robinhood' in desc, desc
    print('ok schema_transport')


def test_dispatch_passes_transport_through():
    _fresh()
    res, _ok = dispatch.execute_tool(
        'mcp_setup', {'name': 'viadispatch',
                      'url': 'https://example.com/mcp',
                      'transport': 'http'})
    assert 'saved (http)' in res, res
    entry = mcp_mod.get_servers()['viadispatch']
    assert entry['transport'] == 'http', entry
    print('ok dispatch_passthrough')
