import os
"""Role-based model routing tests (suite E): resolve_role_model,
_attempt_provider_call override/fallback/restore, inline_data -> image_url
conversion, and the vision hook default.

Follows the test_headless.py stub pattern: bypasses aria/__init__.py's eager
imports and stubs hardware-bound modules (cv2 is absent on this box).
"""
import sys
import types

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

results = []


def check(name, fn):
    try:
        fn()
        results.append((name, "PASS", ""))
        print(f"PASS  {name}")
    except AssertionError as e:
        results.append((name, "FAIL", str(e)))
        print(f"FAIL  {name}: {e}")
    except Exception as e:
        results.append((name, "ERROR", f"{type(e).__name__}: {e}"))
        print(f"ERROR {name}: {type(e).__name__}: {e}")


# stub psutil (config imports it)
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules["psutil"] = _ps

# stub cv2 (absent on server) and aria.hardware (pyserial-bound)
sys.modules["cv2"] = types.ModuleType("cv2")
_hw = types.ModuleType("aria.hardware")
_hw.send_servo_command = lambda *a, **k: None
_hw.SERVO_POS = {"pan": 90, "tilt": 45}
sys.modules["aria.hardware"] = _hw
# stub aria.agent.brain (vision's last-resort fallback must fail cleanly)
sys.modules["aria.agent.brain"] = types.ModuleType("aria.agent.brain")

import importlib
providers = importlib.import_module("aria.agent.providers")
vision = importlib.import_module("aria.vision")


# ---- resolve_role_model ----
def t_role_vision():
    assert providers.resolve_role_model("vision") == "gemma4:31b-cloud"


def t_role_code():
    assert providers.resolve_role_model("code") == "qwen3-coder:480b-cloud"


def t_role_default_none():
    assert providers.resolve_role_model("default") is None
    assert providers.resolve_role_model("") is None
    assert providers.resolve_role_model("bogus-role") is None


def t_role_empty_override_disables():
    old = providers.OLLAMA_VISION_MODEL
    providers.OLLAMA_VISION_MODEL = ""
    try:
        assert providers.resolve_role_model("vision") is None
    finally:
        providers.OLLAMA_VISION_MODEL = old


# ---- inline_data -> image_url ----
def _user_msg(contents):
    msgs = providers.gemini_contents_to_oai_messages("sys", contents)
    return [m for m in msgs if m.get("role") == "user"]


def t_image_becomes_image_url():
    contents = [{"role": "user", "parts": [
        {"text": "what is this"},
        {"inline_data": {"mime_type": "image/jpeg", "data": "QUJD"}},
    ]}]
    um = _user_msg(contents)
    assert len(um) == 1, um
    content = um[0]["content"]
    assert isinstance(content, list), content
    kinds = [c["type"] for c in content]
    assert kinds == ["image_url", "text"], kinds  # image BEFORE text (Ollama guidance)
    assert content[1]["text"] == "what is this"
    assert content[0]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"


def t_pure_text_unchanged():
    contents = [{"role": "user", "parts": [{"text": "a"}, {"text": "b"}]}]
    um = _user_msg(contents)
    assert len(um) == 1, um
    assert um[0]["content"] == "a\nb", um[0]["content"]  # old joined-string shape kept


# ---- _attempt_provider_call ----
class StubProvider:
    def __init__(self, name, model, fail_on=()):
        self.name = name
        self.model = model
        self.fail_on = set(fail_on)
        self.seen = []
        self.last_error = None

    def is_available(self):
        return True

    def call(self, system_instruction, contents, tool_decls=None, on_text_chunk=None):
        self.seen.append(self.model)
        if self.model in self.fail_on:
            raise RuntimeError(f"model {self.model} exploded")
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}


def t_override_used_and_restored():
    p = StubProvider("ollama_cloud", "gpt-oss:120b")
    data, fb = providers._attempt_provider_call(p, "s", [], None, None, "gemma4:31b-cloud")
    assert data["candidates"][0]["content"]["parts"][0]["text"] == "ok"
    assert fb is False
    assert p.seen == ["gemma4:31b-cloud"], p.seen
    assert p.model == "gpt-oss:120b", "model not restored"


def t_override_fails_falls_back_no_raise():
    p = StubProvider("ollama_cloud", "gpt-oss:120b", fail_on={"gemma4:31b-cloud"})
    data, fb = providers._attempt_provider_call(p, "s", [], None, None, "gemma4:31b-cloud")
    assert fb is True
    assert p.seen == ["gemma4:31b-cloud", "gpt-oss:120b"], p.seen
    assert p.model == "gpt-oss:120b", "model not restored after fallback"


def t_override_none_result_falls_back():
    # provider.call swallows HTTP errors into last_error + None (no raise);
    # that must ALSO trigger the default-model retry.
    class NoneThenOk(StubProvider):
        def call(self, system_instruction, contents, tool_decls=None, on_text_chunk=None):
            self.seen.append(self.model)
            if self.model == "gemma4:31b-cloud":
                self.last_error = "HTTP 400: {'error': 'bad request'}"
                return None
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    p = NoneThenOk("ollama_cloud", "gpt-oss:120b")
    p.last_error = None
    data, fb = providers._attempt_provider_call(p, "s", [], None, None, "gemma4:31b-cloud")
    assert fb is True
    assert p.seen == ["gemma4:31b-cloud", "gpt-oss:120b"], p.seen
    assert data["candidates"][0]["content"]["parts"][0]["text"] == "ok"
    assert p.model == "gpt-oss:120b"


def t_both_fail_returns_none_and_restores():
    # Both role and default fail -> (None, True), no raise; model restored.
    p = StubProvider("ollama_cloud", "gpt-oss:120b",
                     fail_on={"gemma4:31b-cloud", "gpt-oss:120b"})
    data, fb = providers._attempt_provider_call(p, "s", [], None, None, "gemma4:31b-cloud")
    assert data is None
    assert fb is True
    assert p.model == "gpt-oss:120b", "model not restored after double failure"


def t_override_ignored_off_ollama_cloud():
    p = StubProvider("groq", "llama-3.3-70b-versatile")
    data, fb = providers._attempt_provider_call(p, "s", [], None, None, "gemma4:31b-cloud")
    assert p.seen == ["llama-3.3-70b-versatile"], p.seen
    assert fb is False


# ---- vision hook ----
def t_vision_hook_attached():
    assert vision._VISION_TEXT_CALL is not None
    assert vision._VISION_TEXT_CALL.__name__ == "_default_vision_call"


def _canned_contents():
    return [{"role": "user", "parts": [
        {"text": "describe"},
        {"inline_data": {"mime_type": "image/jpeg", "data": "QUJD"}},
    ]}]


def t_vision_uses_chain_not_gemini():
    calls = {}

    def fake_provider_call(sys_prompt, contents, tool_decls=None, on_text_chunk=None,
                           model_override=None, **kwargs):
        calls["override"] = model_override
        return {"candidates": [{"content": {"parts": [{"text": "a red bicycle"}]}}]}

    old = providers.provider_call
    providers.provider_call = fake_provider_call
    try:
        out = vision._default_vision_call("sys", _canned_contents())
    finally:
        providers.provider_call = old
    assert out == "a red bicycle", out
    assert calls["override"] == "gemma4:31b-cloud", calls


def t_vision_chain_down_falls_back_cleanly():
    def dead_provider_call(*a, **k):
        return None

    old = providers.provider_call
    providers.provider_call = dead_provider_call
    try:
        out = vision._default_vision_call("sys", _canned_contents())
    finally:
        providers.provider_call = old
    # brain is stubbed without gemini_text -> ImportError -> clean bracket msg
    assert out.startswith("[Vision unavailable:"), out


def t_http_400_does_not_quarantine_key():
    import io
    import urllib.error
    quarantined = []
    old_q = providers.quarantine_key
    providers.quarantine_key = lambda key, dur, code: quarantined.append((key, dur, code))
    old_urlopen = providers.urllib.request.urlopen

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                     io.BytesIO(b'{"error":{"message":"unknown model"}}'))

    providers.urllib.request.urlopen = boom
    try:
        p = providers.OpenAICompatProvider("ollama_cloud", "https://ollama.com/v1",
                                           "test-key-123", "gemma4:31b-cloud")
        out = p.call("sys", [{"role": "user", "parts": [{"text": "hi"}]}])
    finally:
        providers.quarantine_key = old_q
        providers.urllib.request.urlopen = old_urlopen
    assert out is None
    assert quarantined == [], quarantined  # 400 = our payload, key stays live
    assert "unknown model" in (p.last_error or ""), p.last_error


def t_only_provider_restricts_chain():
    tried = []

    class ChainStub(StubProvider):
        def call(self, system_instruction, contents, tool_decls=None, on_text_chunk=None):
            tried.append(self.name)
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    old_build = providers._build_chain
    providers._build_chain = lambda: [ChainStub("groq", "m1"), ChainStub("ollama_cloud", "m2")]
    try:
        data = providers.provider_call("s", [{"role": "user", "parts": [{"text": "hi"}]}],
                                       only_provider="ollama_cloud")
    finally:
        providers._build_chain = old_build
    assert tried == ["ollama_cloud"], tried
    assert data["candidates"][0]["content"]["parts"][0]["text"] == "ok"


for name, fn in sorted([(k, v) for k, v in list(globals().items()) if k.startswith("t_")]):
    check(name, fn)

fails = [r for r in results if r[1] != "PASS"]
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
