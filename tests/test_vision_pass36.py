"""Pass 36 vision fixes: #08 pre-pass routes through the shared service,
#09 native vision rejects placeholder keys / gates the probe,
#38 vision failures stay typed FAILED through normalize().

Follows the test_vision_look.py / test_headless.py stub pattern (cv2
absent on this box); imports the REAL aria.vision and aria.agent.brain.

Run:  python3 -m pytest tests/test_vision_pass36.py -q
"""
import io
import os
import sys
import types
import urllib.error
import urllib.request

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
tools_pkg = types.ModuleType("aria.tools")
tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = tools_pkg
agent_pkg = types.ModuleType("aria.agent")
agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = agent_pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules["psutil"] = _ps
sys.modules["cv2"] = types.ModuleType("cv2")
_hw = types.ModuleType("aria.hardware")
_hw.send_servo_command = lambda *a, **k: None
_hw.SERVO_POS = {"pan": 90, "tilt": 45}
sys.modules["aria.hardware"] = _hw

# Metering stub: the native vision path wraps its HTTP call in
# usage.attempt(); keep the tests offline and side-effect free.
_usage = types.ModuleType("aria.usage")


class _Attempt:
    def __init__(self, *a, **k):
        self.measured = {}

    def __enter__(self):
        return self.measured

    def __exit__(self, *a):
        return False


_usage.attempt = lambda *a, **k: _Attempt()
_usage.max_output_tokens = lambda: 512
sys.modules["aria.usage"] = _usage

import importlib  # noqa: E402

config = importlib.import_module("aria.config")
vision = importlib.import_module("aria.vision")
outcome = importlib.import_module("aria.outcome")
normalize = outcome.normalize
_sp = types.ModuleType("aria.speech")
_sp.edge_tts_bytes = lambda text: b""
_sp.speak = lambda *a, **k: None
_sp._SPEECH_QUEUE = types.SimpleNamespace(unfinished_tasks=0)
_sp._AUDIO_PLAY_QUEUE = types.SimpleNamespace(unfinished_tasks=0)
sys.modules["aria.speech"] = _sp
_hud = types.ModuleType("aria.hud")
_hud.set_hud_state = lambda *a, **k: None
_hud.draw_hud = lambda *a, **k: None
sys.modules["aria.hud"] = _hud
brain = importlib.import_module("aria.agent.brain")
providers = importlib.import_module("aria.agent.providers")

FAKE_JPEG = b"\xff\xd8" + b"x" * 200


def _vision_contents():
    return [{"role": "user", "parts": [
        {"text": "Describe what you see."},
        {"inline_data": {"mime_type": "image/jpeg", "data": "aGVsbG8="}},
    ]}]


def _http_error(code):
    return urllib.error.HTTPError(
        "https://ollama.com/api/chat", code, "error", {},
        io.BytesIO(b"bad key"))


# ── Pass 36 #08 ──────────────────────────────────────────────────────

def test_describe_image_success_uses_shared_service(monkeypatch):
    """describe_image() routes through _VISION_TEXT_CALL, not native."""
    seen = {}

    def fake_service(sys_prompt, contents):
        seen["called"] = True
        return "A red bicycle leaning on a fence."

    monkeypatch.setattr(vision, "_VISION_TEXT_CALL", fake_service)
    monkeypatch.setattr(
        vision, "_ollama_native_vision_call",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("native must not be called")))
    result = vision.describe_image(FAKE_JPEG, "what is this?")
    assert seen.get("called") is True
    assert str(result) == "A red bicycle leaning on a fence."
    assert getattr(result, "status", None) == "done"
    assert normalize(result).status == "done"


def test_describe_image_failure_strings_are_typed(monkeypatch):
    for failure in (
        "[Vision unavailable: local-only mode is on and the local vision model did not respond.]",
        "[Vision unavailable: ollama native: HTTP 401: bad key]",
        "[Vision returned no description.]",
        "[Vision unavailable: no vision text caller configured.]",
    ):
        monkeypatch.setattr(vision, "_VISION_TEXT_CALL", lambda sp, c, _f=failure: _f)
        result = vision.describe_image(FAKE_JPEG, "what is this?")
        assert getattr(result, "status", None) == "failed", failure
        assert str(result) == failure
        assert normalize(result).status == "failed"


def test_describe_image_no_caller_or_image_is_failed(monkeypatch):
    monkeypatch.setattr(vision, "_VISION_TEXT_CALL", None)
    assert getattr(vision.describe_image(FAKE_JPEG), "status", None) == "failed"
    monkeypatch.setattr(vision, "_VISION_TEXT_CALL", lambda sp, c: "x")
    assert getattr(vision.describe_image(b""), "status", None) == "failed"
    assert getattr(vision.describe_image(None), "status", None) == "failed"


def test_release_screen_hash_lets_retry_recapture(monkeypatch):
    monkeypatch.setattr(vision, "capture_screen", lambda: b"fake-screen-bytes")
    vision._LAST_SCREEN_HASH = None
    assert vision.capture_screen_if_changed() == b"fake-screen-bytes"
    assert vision.capture_screen_if_changed() is None  # unchanged: nothing
    vision.release_screen_hash()
    assert vision._LAST_SCREEN_HASH is None
    assert vision.capture_screen_if_changed() == b"fake-screen-bytes"


def test_vision_prepass_uses_shared_service_not_native(monkeypatch):
    monkeypatch.setattr(
        vision, "_VISION_TEXT_CALL",
        lambda sp, c: "A desk with a coffee mug.")
    monkeypatch.setattr(
        vision, "_ollama_native_vision_call",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("native must not be called")))
    out = brain._vision_prepass("what is on my screen?", FAKE_JPEG, True)
    assert "A desk with a coffee mug." in out
    assert "what is on my screen?" in out


def test_vision_prepass_screen_failure_releases_hash(monkeypatch):
    monkeypatch.setattr(
        vision, "_VISION_TEXT_CALL",
        lambda sp, c: "[Vision unavailable: local-only mode is on and the local vision model did not respond.]")
    vision._LAST_SCREEN_HASH = "sentinel-hash"
    out = brain._vision_prepass("what is on my screen?", FAKE_JPEG, True)
    assert "could not be processed" in out
    assert vision._LAST_SCREEN_HASH is None


def test_vision_prepass_camera_failure_keeps_hash(monkeypatch):
    """Camera has no hash; a failed camera pre-pass must not touch it."""
    monkeypatch.setattr(
        vision, "_VISION_TEXT_CALL",
        lambda sp, c: "[Vision returned no description.]")
    vision._LAST_SCREEN_HASH = "sentinel-hash"
    out = brain._vision_prepass("what do you see?", FAKE_JPEG, False)
    assert "could not be processed" in out
    assert vision._LAST_SCREEN_HASH == "sentinel-hash"


# ── Pass 36 #09 ──────────────────────────────────────────────────────

def _no_call_urlopen(*a, **k):
    raise AssertionError("no HTTP call must be made")


def test_native_vision_placeholder_key_makes_no_call(monkeypatch):
    """'INSERT'/blank keys are 'no key configured', not a Bearer call."""
    monkeypatch.setattr(providers, "provider_mode", lambda: "auto")
    monkeypatch.setattr(urllib.request, "urlopen", _no_call_urlopen)
    for bad_key in ("INSERT", "insert", "  INSERT  ", "", "   "):
        monkeypatch.setattr(providers, "OLLAMA_CLOUD_API_KEY", bad_key)
        assert vision._ollama_native_vision_call("see?", _vision_contents()) is None
        assert vision._NATIVE_LAST_ERROR == "no key configured"


def test_probe_rejects_placeholder_key(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _no_call_urlopen)
    assert vision._probe_model_text("m", "INSERT") == "no key configured"
    assert vision._probe_model_text("m", "") == "no key configured"


def test_native_vision_auth_errors_skip_probe(monkeypatch):
    """401/403/402: no diagnostic probe — it would fail identically."""
    monkeypatch.setattr(providers, "provider_mode", lambda: "auto")
    monkeypatch.setattr(providers, "OLLAMA_CLOUD_API_KEY", "real-key-123")
    monkeypatch.setattr(providers, "OLLAMA_CLOUD_MODEL", "vision-model")
    monkeypatch.setattr(providers, "resolve_role_model", lambda role: None)
    probed = []
    monkeypatch.setattr(vision, "_probe_model_text",
                        lambda m, k: probed.append((m, k)) or "probe-ran")
    for code in (401, 403, 402):
        def _raise(*a, _c=code, **k):
            raise _http_error(_c)
        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        assert vision._ollama_native_vision_call("see?", _vision_contents()) is None
        assert probed == [], f"probe ran on HTTP {code}"
        assert "not probed" in (vision._NATIVE_LAST_ERROR or "")
        assert f"HTTP {code}" in (vision._NATIVE_LAST_ERROR or "")


def test_native_vision_400_runs_probe(monkeypatch):
    """400 (e.g. image format): the probe still runs and can exonerate the tag."""
    monkeypatch.setattr(providers, "provider_mode", lambda: "auto")
    monkeypatch.setattr(providers, "OLLAMA_CLOUD_API_KEY", "real-key-123")
    monkeypatch.setattr(providers, "OLLAMA_CLOUD_MODEL", "vision-model")
    monkeypatch.setattr(providers, "resolve_role_model", lambda role: None)
    monkeypatch.setattr(vision, "_probe_model_text", lambda m, k: None)  # model OK

    def _raise400(*a, **k):
        raise _http_error(400)
    monkeypatch.setattr(urllib.request, "urlopen", _raise400)
    assert vision._ollama_native_vision_call("see?", _vision_contents()) is None
    assert "image format rejected" in (vision._NATIVE_LAST_ERROR or "")


def test_native_vision_success_still_works(monkeypatch):
    """Happy path unchanged: a 200 with content returns the description."""
    monkeypatch.setattr(providers, "provider_mode", lambda: "auto")
    monkeypatch.setattr(providers, "OLLAMA_CLOUD_API_KEY", "real-key-123")
    monkeypatch.setattr(providers, "OLLAMA_CLOUD_MODEL", "vision-model")
    monkeypatch.setattr(providers, "resolve_role_model", lambda role: None)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"message": {"content": "a tabby cat"}}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert vision._ollama_native_vision_call("see?", _vision_contents()) == "a tabby cat"


# ── Pass 36 #38 ──────────────────────────────────────────────────────

def test_tool_read_screen_success_is_done(monkeypatch):
    monkeypatch.setattr(vision, "capture_screen", lambda: FAKE_JPEG)
    monkeypatch.setattr(vision, "_VISION_TEXT_CALL", lambda sp, c: "The screen shows a terminal.")
    result = vision.tool_read_screen("read it")
    assert str(result) == "The screen shows a terminal."
    assert normalize(result).status == "done"


def test_tool_read_screen_service_failure_is_failed(monkeypatch):
    monkeypatch.setattr(vision, "capture_screen", lambda: FAKE_JPEG)
    monkeypatch.setattr(
        vision, "_VISION_TEXT_CALL",
        lambda sp, c: "[Vision unavailable: local-only mode is on and the local vision model did not respond.]")
    result = vision.tool_read_screen("read it")
    assert getattr(result, "status", None) == "failed"
    assert normalize(result).status == "failed"
    # Display text is unchanged: the bracketed reason still travels.
    assert str(result).startswith("[Vision unavailable:")


def test_tool_read_screen_capture_failure_is_failed(monkeypatch):
    def _boom():
        raise RuntimeError("no display")
    monkeypatch.setattr(vision, "capture_screen", _boom)
    result = vision.tool_read_screen("read it")
    assert getattr(result, "status", None) == "failed"
    assert normalize(result).status == "failed"
    assert str(result) == "[Screen capture failed: no display]"


def test_tool_read_screen_no_caller_is_failed(monkeypatch):
    monkeypatch.setattr(vision, "capture_screen", lambda: FAKE_JPEG)
    monkeypatch.setattr(vision, "_VISION_TEXT_CALL", None)
    result = vision.tool_read_screen("read it")
    assert getattr(result, "status", None) == "failed"
    assert normalize(result).status == "failed"


def test_tool_describe_camera_success_is_done(monkeypatch):
    monkeypatch.setattr(vision, "capture_webcam", lambda: FAKE_JPEG)
    monkeypatch.setattr(vision, "_VISION_TEXT_CALL", lambda sp, c: "A person waving.")
    result = vision.tool_describe_camera("what do you see?")
    assert str(result) == "A person waving."
    assert normalize(result).status == "done"


def test_tool_describe_camera_failures_are_failed(monkeypatch):
    monkeypatch.setattr(vision, "capture_webcam", lambda: FAKE_JPEG)
    monkeypatch.setattr(
        vision, "_VISION_TEXT_CALL",
        lambda sp, c: "[Vision returned no description.]")
    result = vision.tool_describe_camera("what do you see?")
    assert normalize(result).status == "failed"

    monkeypatch.setattr(vision, "capture_webcam", lambda: None)
    result = vision.tool_describe_camera("what do you see?")
    assert str(result) == "[Camera not available right now.]"
    assert normalize(result).status == "failed"

    def _boom():
        raise RuntimeError("no camera")
    monkeypatch.setattr(vision, "capture_webcam", _boom)
    result = vision.tool_describe_camera("what do you see?")
    assert normalize(result).status == "failed"


def test_describe_phone_view_no_frame_is_failed(monkeypatch):
    monkeypatch.setitem(vision._PHONE_CAM, "jpeg", None)
    monkeypatch.setitem(vision._PHONE_CAM, "last", 0.0)
    result = vision.describe_phone_view()
    assert getattr(result, "status", None) == "failed"
    assert normalize(result).status == "failed"


def test_describe_phone_view_success_and_failure(monkeypatch):
    monkeypatch.setitem(vision._PHONE_CAM, "last", 0.0)
    assert vision.publish_phone_frame(FAKE_JPEG) is True
    monkeypatch.setattr(vision, "_VISION_TEXT_CALL", lambda sp, c: "A kitchen.")
    result = vision.describe_phone_view()
    assert str(result) == "A kitchen."
    assert normalize(result).status == "done"

    monkeypatch.setattr(
        vision, "_VISION_TEXT_CALL",
        lambda sp, c: "[Vision unavailable: ollama native: HTTP 401: bad key]")
    result = vision.describe_phone_view()
    assert normalize(result).status == "failed"
