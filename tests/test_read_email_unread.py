"""tool_read_email: unread_only must filter query searches too (pass 25 P2#19).

The UNSEEN criterion used to be built and then discarded on the
query branch, so read_email(query=..., unread_only=True) returned
read mail as well. Uses a fake IMAP server; no network.
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
for name in ('aria.scheduler', 'aria.vision', 'aria.hardware', 'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[name] = types.ModuleType(name)

builtins = importlib.import_module('aria.tools.builtins')


class _FakeIMAP:
    """uids 1..3 match the query; only 2 and 3 are unseen."""

    def select(self, *a, **kw):
        return ('OK', [b'3'])

    def uid(self, cmd, *args):
        if cmd == 'SEARCH':
            if 'X-GM-RAW' in args or 'TEXT' in args:
                return ('OK', [b'1 2 3'])
            if 'UNSEEN' in args:
                return ('OK', [b'2 3'])
            return ('OK', [b'1 2 3'])
        if cmd == 'FETCH':
            u = args[0]
            raw = (f"From: a@example.com\r\nSubject: Sub {u}\r\n"
                   f"Date: Mon, 5 Oct 2026 10:00:00 +0000\r\n\r\n").encode()
            return ('OK', [(b'1 (BODY[])', raw)])
        raise AssertionError(cmd)

    def logout(self):
        pass


def _patched(monkeypatch):
    fake = _FakeIMAP()
    monkeypatch.setattr(builtins, '_gmail_imap', lambda: (fake, None))
    return fake


def test_query_with_unread_only_returns_only_unseen(monkeypatch):
    _patched(monkeypatch)
    out = builtins.tool_read_email(query='bill', limit=10, unread_only=True)
    assert '[uid=3]' in out and '[uid=2]' in out, out
    assert '[uid=1]' not in out, out


def test_query_without_unread_only_returns_all(monkeypatch):
    _patched(monkeypatch)
    out = builtins.tool_read_email(query='bill', limit=10, unread_only=False)
    assert '[uid=1]' in out and '[uid=2]' in out and '[uid=3]' in out, out


def test_no_query_unread_only_still_uses_unseen(monkeypatch):
    _patched(monkeypatch)
    out = builtins.tool_read_email(query='', limit=10, unread_only=True)
    assert '[uid=3]' in out and '[uid=2]' in out, out
    assert '[uid=1]' not in out, out
