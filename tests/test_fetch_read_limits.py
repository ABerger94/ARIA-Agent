"""Pass 26 B4#8 (builtins part): output limits applied AFTER unbounded
collection.

Bug: _fetch_html did resp.read() with no size limit (10s timeout
only); tool_fetch_url truncated to MAX_TOOL_OUTPUT only after the
whole body was in memory. tool_read_file did open + f.read() whole
file, no cap.

Fix contract:
- _fetch_html reads at most ~2 MiB (+1 byte): a huge chunked body
  is bounded at the read, never buffered whole;
- tool_read_file stats the file first and stream-caps files over
  ~2 MiB, with a truncation marker naming the total size;
- small fetches / reads are unchanged.
"""
import os
import sys
import tempfile
import types

import ipaddress

import pytest

_TMPHOME = tempfile.mkdtemp(prefix="aria_fetch_read_limits_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg

import importlib  # noqa: E402

builtins = importlib.import_module("aria.tools.builtins")

LIMIT = 2 * 1024 * 1024


class _ChunkedResponse:
    """A 5 MiB body served in chunks; records the largest read() asked for."""

    def __init__(self, total=5 * 1024 * 1024):
        self._remaining = total
        self.max_asked = 0

    def read(self, n=-1):
        if n is None or n < 0:
            n = self._remaining
        self.max_asked = max(self.max_asked, n)
        take = min(n, self._remaining)
        self._remaining -= take
        return b"x" * take

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubOpener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append(req.full_url if hasattr(req, "full_url") else str(req))
        return self.response


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(builtins, "WORKSPACE_DIR", str(tmp_path))
    return tmp_path


def _stub_public_resolution(monkeypatch):
    # Pass 28 #35: the public-destination gate resolves the host;
    # this sandbox's resolver maps example.com to a CGNAT address,
    # so stub the resolution instead of depending on live DNS.
    _real_resolved = builtins._resolved_ips
    monkeypatch.setattr(
        builtins, "_resolved_ips",
        lambda host: ([ipaddress.ip_address("93.184.216.34")]
                      if host == "example.com" else _real_resolved(host)))


def test_huge_chunked_http_body_is_bounded(monkeypatch):
    _stub_public_resolution(monkeypatch)
    resp = _ChunkedResponse()
    monkeypatch.setattr(builtins, "_HTTP_ONLY_OPENER", _StubOpener(resp))
    html = builtins._fetch_html("https://example.com/huge")
    assert html is not None
    assert len(html) <= LIMIT + 1, len(html)
    assert resp.max_asked <= LIMIT + 1, resp.max_asked


def test_small_fetch_still_works(monkeypatch):
    _stub_public_resolution(monkeypatch)
    resp = _ChunkedResponse(total=11)
    monkeypatch.setattr(builtins, "_HTTP_ONLY_OPENER", _StubOpener(resp))
    assert builtins.tool_fetch_url("https://example.com/page") == "x" * 11


def test_oversized_file_is_capped_with_marker(ws):
    big = ws / "big.txt"
    big.write_bytes(b"y" * (3 * 1024 * 1024))
    out = builtins.tool_read_file("big.txt")
    assert len(out) <= LIMIT + 1000, len(out)
    assert "truncated" in out
    assert str(3 * 1024 * 1024) in out
    assert big.stat().st_size == 3 * 1024 * 1024  # file untouched


def test_small_file_read_unchanged(ws):
    (ws / "small.txt").write_text("hello world", encoding="utf-8")
    assert builtins.tool_read_file("small.txt") == "hello world"


def test_missing_file_still_failed(ws):
    from aria.outcome import normalize
    assert normalize(builtins.tool_read_file("nope.txt")).status == "failed"
