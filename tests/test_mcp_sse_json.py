"""Plain JSON containing "data:" must not be misparsed as SSE (pass 26 B4#3).

HTTPMCPClient._post decided SSE vs JSON on `"data:" in text` —
a substring search over the whole body. A plain-JSON tool
result containing e.g. "Useful data: alpha" took the SSE
branch, no line started with `data:`, data stayed None and
_post returned {} — silently discarding the real result. A
malformed data: line also raised json.loads out of _post
unhandled. SSE is now decided on Content-Type / a leading
event:/data: line, with a JSON fallback.
"""

import importlib
import json
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_mcp_sse_json_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
from aria import data_dir as _data_dir  # noqa: E402
_data_dir._ROOT = os.path.join(_TMPHOME, "ARIA")

mcp = importlib.import_module("aria.mcp")


class FakeResponse:
    def __init__(self, text, content_type="application/json",
                 payload=None):
        self.text = text
        self.headers = {"Content-Type": content_type}
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        if self._payload is not None:
            return self._payload
        return json.loads(self.text)


class FakeSession:
    def __init__(self, response):
        self._response = response

    def post(self, url, headers=None, json=None, timeout=None):
        return self._response


def _client_with(response):
    client = mcp.HTTPMCPClient("http://127.0.0.1:9/mcp")
    client._get_session = lambda: FakeSession(response)
    return client


def test_json_containing_data_colon_is_not_sse():
    payload = {"result": {"content": [
        {"type": "text", "text": "Useful data: alpha"}]}}
    client = _client_with(FakeResponse(json.dumps(payload),
                                       "application/json",
                                       payload=payload))
    result = client.call_tool("echo", {})
    assert not result.isError
    texts = [b.text for b in result.content]
    assert texts == ["Useful data: alpha"], texts
    print("ok json_with_data_colon")


def test_post_returns_json_payload_directly():
    payload = {"result": {"content": [
        {"type": "text", "text": "Useful data: alpha"}]}}
    client = _client_with(FakeResponse(json.dumps(payload),
                                       "application/json",
                                       payload=payload))
    assert client._post({"jsonrpc": "2.0"}) == payload
    print("ok post_returns_json")


def test_genuine_sse_still_parses():
    # Pass 29 #31: a response frame must carry the request's
    # JSON-RPC id (call_tool's first request is id 1) — a frame
    # without a matching id is a notification and is skipped.
    payload = {"jsonrpc": "2.0", "id": 1,
               "result": {"content": [
                   {"type": "text", "text": "hello via sse"}]}}
    sse_text = "event: message\ndata: " + json.dumps(payload) + "\n\n"
    client = _client_with(FakeResponse(
        sse_text, "text/event-stream", payload=None))
    result = client.call_tool("echo", {})
    assert [b.text for b in result.content] == ["hello via sse"]
    print("ok genuine_sse")


def test_malformed_data_line_does_not_raise():
    client = _client_with(FakeResponse(
        "event: message\ndata: {not json}\n\n",
        "text/event-stream", payload=None))
    # No exception escapes _post; the unparseable SSE yields {}.
    assert client._post({"jsonrpc": "2.0"}) == {}
    print("ok malformed_data_line")
