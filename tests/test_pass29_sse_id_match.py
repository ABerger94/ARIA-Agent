"""HTTP MCP SSE must match the response frame by JSON-RPC id (pass 29 #31).

_post used to take the FIRST `data:` line and stop: an SSE
notification (no `id`) or a frame for another request arriving
first discarded the real result, and call_tool then read an
empty result as a silent success (isError=False, no content).
_post now scans every event, joins multiline `data:` lines, and
takes only the frame whose `id` matches the request.
"""

import importlib
import json
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_pass29_sse_")
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
    def __init__(self, text, content_type="text/event-stream"):
        self.text = text
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        pass

    def json(self):
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


def _result_payload(text):
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": text}]}}


def _sse_event(frame):
    return "event: message\ndata: " + json.dumps(frame) + "\n\n"


def test_notification_first_then_response():
    # call_tool's request carries id 1; the server first pushes a
    # notification (no id), then the real response.
    notification = {"jsonrpc": "2.0",
                    "method": "notifications/message",
                    "params": {"level": "info", "data": "hi"}}
    body = _sse_event(notification) + _sse_event(_result_payload("real result"))
    client = _client_with(FakeResponse(body))
    result = client.call_tool("echo", {})
    assert not result.isError
    assert [b.text for b in result.content] == ["real result"], result.content
    print("ok notification_first_then_response")


def test_wrong_id_frame_first_is_skipped():
    other = {"jsonrpc": "2.0", "id": 999,
             "result": {"content": [{"type": "text", "text": "not mine"}]}}
    body = _sse_event(other) + _sse_event(_result_payload("mine"))
    client = _client_with(FakeResponse(body))
    result = client.call_tool("echo", {})
    assert [b.text for b in result.content] == ["mine"], result.content
    print("ok wrong_id_frame_first_skipped")


def test_multiline_data_lines_joined():
    # One JSON-RPC frame split across two data: lines of one event.
    payload = _result_payload("multi")
    text = json.dumps(payload)
    # Split at a token boundary (after the comma following "id"),
    # so the newline joining the two data: lines is legal JSON
    # whitespace rather than a newline inside a string literal.
    head, _sep, tail = text.partition(', "result"')
    first, second = head + ",", '"result"' + tail
    body = "event: message\ndata: " + first + "\ndata: " + second + "\n\n"
    client = _client_with(FakeResponse(body))
    result = client.call_tool("echo", {})
    assert [b.text for b in result.content] == ["multi"], result.content
    print("ok multiline_data_joined")


def test_notification_only_yields_empty_not_silent_success():
    notification = {"jsonrpc": "2.0",
                    "method": "notifications/message",
                    "params": {"level": "info", "data": "hi"}}
    body = _sse_event(notification)
    client = _client_with(FakeResponse(body))
    assert client._post({"jsonrpc": "2.0", "id": 1}) == {}
    print("ok notification_only_empty")


def test_matching_error_frame_is_returned_not_skipped():
    error = {"jsonrpc": "2.0", "id": 1,
             "error": {"code": -32602, "message": "bad args"}}
    body = _sse_event(error)
    client = _client_with(FakeResponse(body))
    result = client.call_tool("echo", {})
    assert result.isError
    assert "bad args" in result.content[0].text
    print("ok matching_error_frame_returned")
