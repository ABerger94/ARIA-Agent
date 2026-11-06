"""is_robinhood_url: the access token goes only to the real Robinhood
origin (pass 25 P1#4).

A substring check on the raw URL let any URL containing
"robinhood.com" — suffix domain, query string, path — receive the
Bearer token. The helper parses the URL and requires HTTPS plus a
hostname of robinhood.com or one of its subdomains.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_mcp_robinhood_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, 'ARIA')

mcp = importlib.import_module('aria.mcp')


def test_real_robinhood_origins_accepted():
    assert mcp.is_robinhood_url('https://robinhood.com/mcp') is True
    assert mcp.is_robinhood_url(
        'https://agent.robinhood.com/mcp/trading') is True
    assert mcp.is_robinhood_url('https://api.robinhood.com:443/x') is True


def test_lookalike_urls_rejected():
    assert mcp.is_robinhood_url('https://robinhood.com.evil.com/') is False
    assert mcp.is_robinhood_url('https://evil.com/?robinhood.com') is False
    assert mcp.is_robinhood_url('https://evil.com/robinhood.com') is False
    assert mcp.is_robinhood_url('https://notrobinhood.com/') is False
    assert mcp.is_robinhood_url('http://robinhood.com/mcp') is False
    assert mcp.is_robinhood_url('') is False
    assert mcp.is_robinhood_url('robinhood.com') is False
