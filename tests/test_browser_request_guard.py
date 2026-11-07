"""Pass 27 #10: browser LAN restrictions must run BEFORE requests,
not after the landing — and must cover subrequests, not just
navigations, and read() must enforce the current-page restriction.

Before: _assert_current_url_public ran only after a navigation
landed (go_back cannot undo a request already made), redirects
and page subresources reached private hosts unchecked, and read()
reported any page without enforcing anything. Now start() installs
a context-level request guard that aborts private-host http(s)
requests before they leave, and read() enforces the landing check.
"""
import importlib
import os
import sys
import types

pkg = types.ModuleType("aria")
pkg.__path__ = [os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")]
sys.modules["aria"] = pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
sys.modules["aria.speech"] = types.ModuleType("aria.speech")

ba = importlib.import_module("aria.browser_agent")

# Deterministic DNS for the tests: example.com is public, the
# .invalid name does not resolve (fail closed), anything else is
# unresolvable too — no live DNS is consulted.
_REAL_RESOLVE = ba._resolve_host


def _fake_resolve(host):
    if host == "example.com":
        return ["93.184.216.34"]
    return []


ba._resolve_host = _fake_resolve
ba.get_setting = lambda key, default=False: False


class FakeRoute:
    def __init__(self, url):
        self.request = types.SimpleNamespace(url=url)
        self.aborted = False
        self.continued = False

    def abort(self):
        self.aborted = True

    def continue_(self):
        self.continued = True


class FakeCtx:
    def __init__(self):
        self.routes = []

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))


def _guard_for(session):
    assert len(session._ctx.routes) == 1
    pattern, handler = session._ctx.routes[0]
    assert pattern == "**/*"
    return handler


def _install():
    session = ba.BrowserSession()
    session._ctx = FakeCtx()
    session._install_request_guard()
    return session, _guard_for(session)


def _fire(handler, url):
    route = FakeRoute(url)
    handler(route)
    return route


def test_private_ip_literals_aborted():
    _, handler = _install()
    for url in ("http://127.0.0.1/admin",
                "https://192.168.1.5/x",
                "http://169.254.169.254/latest/meta-data/",
                "http://localhost:8080/"):
        route = _fire(handler, url)
        assert route.aborted and not route.continued, url


def test_public_hosts_continue():
    _, handler = _install()
    for url in ("https://example.com/",
                "http://93.184.216.34/"):
        route = _fire(handler, url)
        assert route.continued and not route.aborted, url


def test_non_web_schemes_continue():
    _, handler = _install()
    for url in ("data:text/html,<b>x</b>",
                "blob:https://example.com/uuid",
                "about:blank"):
        route = _fire(handler, url)
        assert route.continued and not route.aborted, url


def test_unresolvable_hostname_fails_closed():
    _, handler = _install()
    route = _fire(handler, "https://unresolvable.invalid/x")
    assert route.aborted and not route.continued


def test_allow_lan_lets_private_through():
    old = ba.get_setting
    ba.get_setting = lambda key, default=False: True
    try:
        _, handler = _install()
        route = _fire(handler, "http://127.0.0.1/admin")
        assert route.continued and not route.aborted
    finally:
        ba.get_setting = old


def test_context_without_route_is_tolerated():
    session = ba.BrowserSession()
    session._ctx = types.SimpleNamespace()  # no route()
    session._install_request_guard()  # must not raise


class FakePage:
    def __init__(self, url):
        self.url = url
        self.backs = 0

    def title(self):
        return "Fake"

    def inner_text(self, selector):
        return ""

    def locator(self, _query):
        return types.SimpleNamespace(count=lambda: 0)

    def go_back(self):
        self.backs += 1


def test_read_enforces_current_page_restriction():
    session = ba.BrowserSession()
    session.page = FakePage("http://127.0.0.1/admin")
    try:
        session.read()
    except ValueError as exc:
        assert "private/loopback" in str(exc)
    else:
        raise AssertionError("read() on a private landing must refuse")
    assert session.page.backs == 1


def test_read_on_public_page_proceeds():
    session = ba.BrowserSession()
    session.page = FakePage("https://example.com/")
    out = session.read()
    assert "Page: Fake (https://example.com/)" in out
