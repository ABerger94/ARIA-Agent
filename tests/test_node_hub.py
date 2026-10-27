"""Phone node channel: hub semantics, tool renderers, the
bridge's WebSocket codec, and a full socket round trip.

The protocol (hello / welcome / command / result / ping / pong)
is shared verbatim with the Android node app — these tests pin
the laptop half: futures bridged across the endpoint thread and
tool threads (aria/node_hub.py), tools in aria/phone_node.py,
RFC 6455 framing + /node/ws in aria/bridge.py."""
import base64
import io
import json
import os
import socket
import struct
import sys
import threading
import types

pkg = types.ModuleType('aria')
pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import node_hub
from aria.node_hub import NodeHub, NodeHubError

# Bridge imports pull speech/vision; stub them the way
# test_bridge.py does so aria.bridge loads without audio deps.
_ps = types.ModuleType('psutil')
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault('psutil', _ps)
_tools_pkg = types.ModuleType('aria.tools')
_tools_pkg.__path__ = [
    os.path.join(os.path.dirname(__file__), '..', 'aria', 'tools')]
sys.modules['aria.tools'] = _tools_pkg
_vision = types.ModuleType('aria.vision')
_vision.get_face_frame_jpeg = lambda: None
_vision.publish_face_frame = lambda *a, **k: None
_vision.publish_phone_frame = lambda jpeg: True
_vision.get_phone_frame_jpeg = lambda: None
_vision.get_phone_frame_status = lambda: {'active': False, 'age_s': -1.0}
_vision.describe_phone_view = lambda q='': '[stubbed]'
sys.modules['aria.vision'] = _vision
_speech = types.ModuleType('aria.speech')
_speech.tts_bytes_for_bridge = lambda text: (b'', 'no speech in tests')
_speech.transcribe_audio = lambda audio, mime: ''
sys.modules['aria.speech'] = _speech


def _answering_hub(data_by_action):
    """A hub with one fake node that answers commands inline."""
    hub = NodeHub()

    def send(frame):
        def answer():
            data = data_by_action.get(frame['action'], {})
            hub.handle_result({'id': frame['id'], 'ok': True,
                               'data': data})
        threading.Thread(target=answer, daemon=True).start()

    hub.register('android-test', kind='android',
                 capabilities=['apps', 'screen'], send=send)
    return hub


def test_send_command_round_trip_and_errors():
    hub = _answering_hub({'list_apps': {'apps': []}})
    assert hub.send_command(None, 'list_apps', {}) == {'apps': []}
    # ok=false surfaces the node's own error text.
    hub2 = NodeHub()

    def send_no(frame):
        threading.Thread(
            target=lambda: hub2.handle_result(
                {'id': frame['id'], 'ok': False, 'error': 'nope'}),
            daemon=True).start()

    hub2.register('n', kind='android', capabilities=[], send=send_no)
    try:
        hub2.send_command(None, 'open_app', {})
        assert False, 'expected NodeHubError'
    except NodeHubError as e:
        assert e.code == 'node_error' and 'nope' in str(e)
    # No node at all.
    try:
        NodeHub().send_command(None, 'list_apps', {})
        assert False, 'expected no_node'
    except NodeHubError as e:
        assert e.code == 'no_node'
    # Timeout.
    hub3 = NodeHub()
    hub3.register('silent', kind='android', capabilities=[],
                  send=lambda frame: None)
    try:
        hub3.send_command(None, 'list_apps', {}, timeout=0.2)
        assert False, 'expected timeout'
    except NodeHubError as e:
        assert e.code == 'timeout'


def test_unregister_strands_pending_and_replacement_is_safe():
    hub = NodeHub()
    send_a = lambda frame: None
    hub.register('phone', kind='android', capabilities=[], send=send_a)
    results = {}

    def call():
        try:
            hub.send_command(None, 'read_screen', {}, timeout=5)
        except NodeHubError as e:
            results['code'] = e.code

    t = threading.Thread(target=call, daemon=True)
    t.start()
    threading.Event().wait(0.2)
    # A replacement registers under the same name; the OLD socket
    # disconnecting late must not evict it.
    send_b = lambda frame: None
    hub.register('phone', kind='android', capabilities=[], send=send_b)
    assert hub.unregister('phone', send=send_a) is False
    assert hub.nodes()[0]['name'] == 'phone'
    assert hub.unregister('phone', send=send_b) is True
    t.join(3)
    assert results.get('code') == 'disconnected'


def test_phone_tools_render_and_guard(monkeypatch):
    from aria import phone_node
    hub = _answering_hub({
        'list_apps': {'apps': [{'label': 'Spotify',
                                'package': 'com.spotify.music'}]},
        'notifications': {'items': [
            {'app': 'Messages', 'title': 'Hi', 'text': 'there',
             'ts': 1760000000000}]},
        'open_app': {'label': 'Spotify', 'package': 'com.spotify.music'},
    })
    monkeypatch.setattr(phone_node, 'node_hub',
                        types.SimpleNamespace(HUB=hub, NodeHubError=NodeHubError))
    assert 'Spotify (com.spotify.music)' in phone_node.tool_phone_list_apps()
    assert 'android-test' in phone_node.tool_phone_status()
    assert '[Messages] Hi — there' in phone_node.tool_phone_notifications()
    assert phone_node.tool_phone_open_app(label='Spotify') \
        == 'Opened Spotify (com.spotify.music) on the phone.'
    # The replay guard: control tools refuse in preauthorized
    # scope. A stand-in dispatch carries the scope flag (the real
    # execute_tool sets the same thread-local; the tool only reads
    # in_preauthorized_scope()).
    fake_dispatch = types.ModuleType('aria.tools.dispatch')
    fake_dispatch._SCOPE = types.SimpleNamespace(preauthorized=True)
    fake_dispatch.in_preauthorized_scope = \
        lambda: bool(fake_dispatch._SCOPE.preauthorized)
    tools_pkg = types.ModuleType('aria.tools')
    tools_pkg.__path__ = [
        os.path.join(os.path.dirname(__file__), '..', 'aria', 'tools')]
    monkeypatch.setitem(sys.modules, 'aria.tools', tools_pkg)
    monkeypatch.setitem(sys.modules, 'aria.tools.dispatch', fake_dispatch)
    out = phone_node.tool_phone_tap(ref='3')
    assert 'never runs inside a routine replay' in out
    fake_dispatch._SCOPE.preauthorized = False
    out = phone_node.tool_phone_key('sideways')
    assert 'not a key this tool can send' in out


def test_ws_codec_vectors():
    from aria import bridge
    # The RFC 6455 worked example.
    assert bridge._ws_accept('dGhlIHNhbXBsZSBub25jZQ==') \
        == 's3pPLMBiTxaQ9kYGzzhZRbK+xOo='
    # A masked client text frame round-trips through the reader.
    payload = b'{"type":"ping"}'
    mask = b'\x01\x02\x03\x04'
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    frame = bytes([0x81, 0x80 | len(payload)]) + mask + masked
    opcode, data = bridge._ws_read_frame(io.BytesIO(frame))
    assert opcode == 0x1 and data == payload
    # Server frames are unmasked and length-coded.
    out = bridge._ws_frame(0x1, b'hi')
    assert out[0] == 0x81 and out[1] == 2 and out[2:] == b'hi'
    big = bridge._ws_frame(0x1, b'x' * 200)
    assert big[1] == 126 and struct.unpack('!H', big[2:4])[0] == 200


def test_node_key_ok(monkeypatch):
    from aria import bridge
    monkeypatch.setattr(bridge, 'BRIDGE_TOKEN', 'tok-abc')
    assert bridge._node_key_ok('tok-abc') is True
    assert bridge._node_key_ok('tok-nope') is False
    assert bridge._node_key_ok('') is False


def test_node_ws_end_to_end(monkeypatch):
    """Handshake → hello → welcome → command out → result back,
    over a real socket through the real handler."""
    from http.server import ThreadingHTTPServer
    from aria import bridge
    monkeypatch.setattr(bridge, 'BRIDGE_TOKEN', 'tok-e2e')
    server = ThreadingHTTPServer(('127.0.0.1', 0), bridge.BridgeHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = socket.create_connection(('127.0.0.1', port), timeout=5)
    try:
        key = base64.b64encode(b'0123456789abcdef').decode()
        client.sendall((
            'GET /node/ws HTTP/1.1\r\nHost: x\r\n'
            'Upgrade: websocket\r\nConnection: Upgrade\r\n'
            f'Sec-WebSocket-Key: {key}\r\n'
            'Sec-WebSocket-Version: 13\r\n\r\n').encode())
        resp = b''
        while b'\r\n\r\n' not in resp:
            resp += client.recv(4096)
        assert b'101' in resp.split(b'\r\n', 1)[0]
        rest = resp.split(b'\r\n\r\n', 1)[1]
        stream = io.BytesIO(rest + client.recv(65536) if rest else b'')

        def read_frame():
            nonlocal stream
            while True:
                pos = stream.tell()
                try:
                    return bridge._ws_read_frame(stream)
                except Exception:
                    stream = io.BytesIO(stream.getvalue()[pos:]
                                        + client.recv(65536))

        def send_client(payload: bytes):
            mask = b'\x05\x06\x07\x08'
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            client.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + masked)

        hello = json.dumps({'type': 'hello', 'node': 'android-e2e',
                            'kind': 'android', 'key': 'tok-e2e',
                            'capabilities': ['apps']}).encode()
        send_client(hello)
        opcode, data = read_frame()
        assert json.loads(data) == {'type': 'welcome', 'ok': True}
        assert node_hub.HUB.nodes()[0]['name'] == 'android-e2e'

        def answer_commands():
            opcode, data = read_frame()
            frame = json.loads(data)
            assert frame['type'] == 'command' \
                and frame['action'] == 'list_apps'
            result = json.dumps({'type': 'result', 'id': frame['id'],
                                 'ok': True,
                                 'data': {'apps': []}}).encode()
            send_client(result)

        t = threading.Thread(target=answer_commands, daemon=True)
        t.start()
        out = node_hub.HUB.send_command(None, 'list_apps', {}, timeout=5)
        assert out == {'apps': []}
        t.join(3)
    finally:
        client.close()
        server.shutdown()
    # Disconnect unregisters.
    for _ in range(50):
        if not node_hub.HUB.nodes():
            break
        threading.Event().wait(0.1)
    assert node_hub.HUB.nodes() == []
