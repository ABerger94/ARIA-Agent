"""Browser private-network restrictions (pass 26 B3#8).

169.254.0.0/16 (link-local, incl. the cloud metadata address
169.254.169.254) and 100.64.0.0/10 (CGNAT) were missing from
_ipv4_is_private, hostnames were never resolved at all, and
_url_blocked ran only in the tool's "open" branch — a click,
redirect or back-navigation could land on a private host
unchecked. BrowserSession now enforces the check centrally.
"""

import importlib
import os
import socket
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")


def _pkg(name, path):
    m = types.ModuleType(name)
    m.__path__ = [path]
    sys.modules[name] = m
    return m


_pkg("aria", PKG)
_pkg("aria.tools", os.path.join(PKG, "tools"))
_pkg("aria.agent", os.path.join(PKG, "agent"))
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
sys.modules["aria.speech"] = types.ModuleType("aria.speech")

ba = importlib.import_module("aria.browser_agent")

_FAKE_DNS = {
    "private.example": ["169.254.169.254"],
    "cgnat.example": ["100.64.0.9"],
    "public.example": ["93.184.216.34"],
}


def _fake_getaddrinfo(host, port, *args, **kwargs):
    if host == "unresolvable.example":
        raise socket.gaierror("Name or service not known")
    if host in _FAKE_DNS:
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "",
                 (ip, port or 0)) for ip in _FAKE_DNS[host]]
    raise AssertionError(f"unexpected DNS lookup for {host!r}")


def _install_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    ba._DNS_CACHE.clear()


# --- range table -------------------------------------------------------

def test_link_local_and_cgnat_blocked():
    assert ba._is_private_host("169.254.169.254")
    assert ba._is_private_host("169.254.1.1")
    assert ba._is_private_host("169.254.0.1")
    assert ba._is_private_host("100.64.0.1")
    assert ba._is_private_host("100.127.255.255")
    assert not ba._is_private_host("100.128.0.1")
    assert not ba._is_private_host("169.253.169.254")
    assert "private" in ba._url_blocked("http://169.254.169.254/latest/meta-data/")
    assert "private" in ba._url_blocked("http://169.254.1.1/")
    print("ok link_local_cgnat")


def test_hostname_resolving_to_private_is_blocked(monkeypatch):
    _install_dns(monkeypatch)
    assert ba._is_private_host("private.example")
    assert ba._is_private_host("cgnat.example")
    assert "private" in ba._url_blocked("https://private.example/admin")
    print("ok hostname_private")


def test_hostname_resolving_to_public_is_allowed(monkeypatch):
    _install_dns(monkeypatch)
    assert not ba._is_private_host("public.example")
    assert ba._url_blocked("https://public.example/page") == ""
    print("ok hostname_public")


def test_unresolvable_hostname_fails_closed(monkeypatch):
    _install_dns(monkeypatch)
    assert ba._is_private_host("unresolvable.example")
    assert "private" in ba._url_blocked("https://unresolvable.example/")
    print("ok unresolvable_fails_closed")


# --- central enforcement in BrowserSession ------------------------------

class _FakeLocator:
    def __init__(self, page=None):
        self.page = page

    def count(self):
        return 0


class _FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        # A form submit that lands on a private host.
        self.page.url = "http://169.254.169.254/submit"


class _FakePage:
    def __init__(self):
        self.url = "https://public.example/"
        self._prev = self.url
        self.gotos = []
        self.backs = 0
        self.keyboard = _FakeKeyboard(self)

    def goto(self, url, wait_until=None):
        self.gotos.append(url)
        self._prev = self.url
        # Simulate a redirect to the metadata address.
        if url == "https://public.example/redirect":
            self.url = "http://169.254.169.254/latest/meta-data/"
        else:
            self.url = url

    def go_back(self):
        self.backs += 1
        self.url = self._prev

    def title(self):
        return "Fake"

    def inner_text(self, selector):
        return ""

    def locator(self, selector):
        return _FakeLocator()

    def wait_for_timeout(self, ms):
        pass


def _session_with_page():
    session = ba.BrowserSession()
    session.page = _FakePage()
    return session


def test_open_refuses_private_literal_before_goto():
    session = _session_with_page()
    try:
        session.open("http://169.254.169.254/latest/meta-data/")
    except ValueError as exc:
        assert "private" in str(exc)
    else:
        raise AssertionError("open() did not refuse the metadata URL")
    assert session.page.gotos == []
    print("ok open_refuses_literal")


def test_open_refuses_after_redirect_to_private():
    session = _session_with_page()
    try:
        session.open("https://public.example/redirect")
    except ValueError as exc:
        assert "private" in str(exc)
    else:
        raise AssertionError("open() did not refuse the redirect landing")
    assert session.page.backs == 1  # navigated back away from it
    print("ok open_refuses_redirect")


def test_click_to_private_url_is_blocked():
    session = _session_with_page()

    class _ClickLoc:
        def __init__(self, page):
            self.page = page

        def click(self):
            self.page._prev = self.page.url
            self.page.url = "http://169.254.1.1/admin"

    def _resolve(target):
        return _ClickLoc(session.page), "admin link"

    session._resolve = _resolve
    try:
        session.click("index:0")
    except ValueError as exc:
        assert "private" in str(exc)
    else:
        raise AssertionError("click() did not refuse the private landing")
    assert session.page.backs == 1
    print("ok click_to_private_blocked")


def test_back_to_private_url_is_blocked():
    session = _session_with_page()
    session.page._prev = "http://10.0.0.5/admin"
    try:
        session.back()
    except ValueError as exc:
        assert "private" in str(exc)
    else:
        raise AssertionError("back() did not refuse the private landing")
    print("ok back_to_private_blocked")


def test_press_submit_to_private_url_is_blocked():
    session = _session_with_page()
    try:
        session.press("Enter")
    except ValueError as exc:
        assert "private" in str(exc)
    else:
        raise AssertionError("press() did not refuse the private landing")
    print("ok press_to_private_blocked")


def test_public_navigation_still_works():
    session = _session_with_page()
    out = session.open("https://public.example/page")
    assert "public.example" in out
    assert session.page.gotos == ["https://public.example/page"]
    print("ok public_navigation")
