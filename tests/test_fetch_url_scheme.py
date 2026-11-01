"""Regression tests (2026-11-01, ChatGPT x ARIA loop pass 12):
tool_fetch_url handed any model-supplied URL straight to urllib's
urlopen, which speaks file:// as well as http(s) — so the model could
read local credential stores (aria_keys.json at the install root,
which holds the bridge token among other keys) through an ungated,
read-only tool, in every mode including safe mode. browser_control
get_text and research._fetch_text share the same fetcher, so the hole
was three tools wide.

Fix contract pinned here, at the fetcher level (fetch_url stays
ungated by design — it is a read-only web fetch; the read itself is
now impossible for non-web schemes):
- _fetch_html refuses every non-http(s) scheme BEFORE any opener
  call (zero reads: proven with a recording fake opener, and with a
  real temp file holding a sentinel);
- the http(s)-only opener carries no FileHandler/FTPHandler, so an
  http(s) response redirecting to file:// cannot escape the gate
  either (proven: the real opener raises on a file:// URL);
- normal http(s) fetching still works (proven with a stubbed opener
  returning script-laden HTML, stripped as before);
- browser_control get_text inherits the gate (it calls tool_fetch_url).

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import tempfile
import types
import urllib.error
import urllib.request

import pytest

_TMPHOME = tempfile.mkdtemp(prefix="aria_fetch_url_scheme_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg

import importlib

builtins = importlib.import_module("aria.tools.builtins")
gap = importlib.import_module("aria.tools.gaptools")

SENTINEL = "SENTINEL-BRIDGE-TOKEN-DO-NOT-LEAK"


class _RecordingOpener:
    """Stands in for _HTTP_ONLY_OPENER; any call is a gate failure."""

    def __init__(self):
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append(req)
        raise AssertionError("opener must not be called for a refused URL")


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubOpener:
    """Returns canned HTML for any http(s) request, recording the URL."""

    def __init__(self, body: bytes):
        self.body = body
        self.calls = []

    def open(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.calls.append(url)
        return _FakeResponse(self.body)


@pytest.fixture
def recording_opener(monkeypatch):
    fake = _RecordingOpener()
    monkeypatch.setattr(builtins, "_HTTP_ONLY_OPENER", fake)
    return fake


@pytest.fixture
def sentinel_file():
    fd, path = tempfile.mkstemp(prefix="aria_keys_", suffix=".json")
    with os.fdopen(fd, "w") as f:
        f.write('{"BRIDGE_TOKEN": "%s"}' % SENTINEL)
    yield path
    os.unlink(path)


NON_HTTP_URLS = [
    "file:///E:/ARIA/aria_keys.json",
    "FILE:///E:/ARIA/aria_keys.json",
    "ftp://example.com/pub/keys.json",
    "data:text/plain,hello",
    "javascript:alert(1)",
    "example.com/keys.json",
    "//example.com/keys.json",
    "",
]


@pytest.mark.parametrize("url", NON_HTTP_URLS)
def test_non_http_urls_refused_before_opener(recording_opener, url):
    result = builtins.tool_fetch_url(url)
    assert result.startswith("[Could not fetch")
    assert SENTINEL not in result
    assert builtins._fetch_html(url) is None
    assert recording_opener.calls == []


def test_file_url_to_real_sentinel_file_never_read(
        recording_opener, sentinel_file):
    url = "file://" + sentinel_file
    result = builtins.tool_fetch_url(url)
    assert result.startswith("[Could not fetch")
    assert SENTINEL not in result
    assert recording_opener.calls == []


def test_real_opener_has_no_local_file_handlers():
    opener = builtins._HTTP_ONLY_OPENER
    kinds = [type(h) for h in opener.handlers]
    assert urllib.request.FileHandler not in kinds
    assert urllib.request.FTPHandler not in kinds


def test_real_opener_raises_on_file_url():
    with pytest.raises(urllib.error.URLError):
        builtins._HTTP_ONLY_OPENER.open("file:///etc/passwd", timeout=5)


def test_http_fetch_still_works(monkeypatch):
    stub = _StubOpener(
        b"<html><head><script>evil()</script></head>"
        b"<body><p>Hello world</p></body></html>")
    monkeypatch.setattr(builtins, "_HTTP_ONLY_OPENER", stub)
    result = builtins.tool_fetch_url("https://example.com/page")
    assert result == "Hello world"
    assert stub.calls == ["https://example.com/page"]


def test_uppercase_http_scheme_allowed(monkeypatch):
    stub = _StubOpener(b"<p>Uppercase scheme</p>")
    monkeypatch.setattr(builtins, "_HTTP_ONLY_OPENER", stub)
    result = builtins.tool_fetch_url("HTTP://example.com/page")
    assert result == "Uppercase scheme"


def test_browser_control_get_text_inherits_gate(
        recording_opener, sentinel_file):
    result = gap.tool_browser_control(
        {"action": "get_text", "url": "file://" + sentinel_file})
    assert result.startswith("status=failed"), result
    assert SENTINEL not in result
    assert recording_opener.calls == []
