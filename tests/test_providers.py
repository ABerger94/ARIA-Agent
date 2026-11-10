"""Unit tests for aria/agent/providers.py — pure translation logic, no network.

Run:  python3 tests/test_providers.py
"""
import json
import sys
import os
import types

# Bypass aria/__init__.py's eager hardware imports (same pattern as test_headless.py)
PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg

from aria.agent.providers import (
    gemini_contents_to_oai_messages,
    gemini_decls_to_oai_tools,
    oai_response_to_parts,
    _payload_fingerprint,
    _synth_tool_id,
    _build_chain,
)


def test_system_and_user_text():
    msgs = gemini_contents_to_oai_messages(
        "sys",
        [{"role": "user", "parts": [{"text": "hello"}]}],
    )
    assert msgs[0] == {"role": "system", "content": "sys"}, msgs[0]
    assert msgs[1] == {"role": "user", "content": "hello"}, msgs[1]
    print("ok system_and_user_text")


def test_model_function_call_roundtrip():
    contents = [
        {"role": "model", "parts": [
            {"text": "checking"},
            {"functionCall": {"name": "get_time", "args": {}}},
        ]},
        {"role": "user", "parts": [
            {"functionResponse": {"name": "get_time", "response": {"output": "noon"}}},
        ]},
    ]
    msgs = gemini_contents_to_oai_messages("", contents)
    asst = [m for m in msgs if m["role"] == "assistant"][0]
    tool = [m for m in msgs if m["role"] == "tool"][0]
    assert len(asst["tool_calls"]) == 1
    tc = asst["tool_calls"][0]
    assert tc["function"]["name"] == "get_time"
    assert json.loads(tc["function"]["arguments"]) == {}
    # tool message must reference the same id (FIFO per name)
    assert tool["tool_call_id"] == tc["id"], (tool["tool_call_id"], tc["id"])
    assert json.loads(tool["content"]) == {"output": "noon"}
    print("ok model_function_call_roundtrip")


def test_oai_id_preserved():
    # inbound OpenAI ids ride through functionCall parts untouched
    parts = oai_response_to_parts({
        "choices": [{"message": {
            "content": "done",
            "tool_calls": [{"id": "call_abc123", "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path": "x"}'}}],
        }}]
    })
    assert parts[0] == {"text": "done"}, parts[0]
    fc = parts[1]["functionCall"]
    assert fc["name"] == "read_file" and fc["args"] == {"path": "x"}
    assert fc["_oai_id"] == "call_abc123"
    # and the outbound translator reuses it
    msgs = gemini_contents_to_oai_messages("", [{"role": "model", "parts": parts}])
    assert msgs[0]["tool_calls"][0]["id"] == "call_abc123"
    print("ok oai_id_preserved")


def test_bad_tool_args():
    # Pass 25: invalid JSON (or a non-object) must NOT silently
    # become {} — {} reached handlers with their defaults
    # (media_key toggling playback). The parse failure is
    # preserved as a marker the dispatcher rejects.
    parts = oai_response_to_parts({
        "choices": [{"message": {
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "x", "arguments": "{not json"}}],
        }}]
    })
    assert parts[0]["functionCall"]["args"] == {"_args_parse_error": True}
    parts = oai_response_to_parts({
        "choices": [{"message": {
            "tool_calls": [{"id": "c2", "type": "function",
                            "function": {"name": "x", "arguments": "[1, 2]"}}],
        }}]
    })
    assert parts[0]["functionCall"]["args"] == {"_args_parse_error": True}
    # Valid JSON is untouched.
    parts = oai_response_to_parts({
        "choices": [{"message": {
            "tool_calls": [{"id": "c3", "type": "function",
                            "function": {"name": "x", "arguments": '{"a": 1}'}}],
        }}]
    })
    assert parts[0]["functionCall"]["args"] == {"a": 1}
    print("ok bad_tool_args")


def test_decls_mapping():
    decls = [{"function_declarations": [
        {"name": "get_time", "description": "now",
         "parameters": {"type": "object", "properties": {"tz": {"type": "string"}}}},
    ]}]
    tools = gemini_decls_to_oai_tools(decls)
    assert tools == [{"type": "function", "function": {
        "name": "get_time", "description": "now",
        "parameters": {"type": "object", "properties": {"tz": {"type": "string"}}}}}]
    assert gemini_decls_to_oai_tools(None) == []
    print("ok decls_mapping")


def test_synth_id_deterministic():
    a = _synth_tool_id("get_time", {"a": 1})
    b = _synth_tool_id("get_time", {"a": 1})
    c = _synth_tool_id("get_time", {"a": 2})
    assert a == b and a != c and a.startswith("call_")
    print("ok synth_id_deterministic")


def test_images_dropped():
    # Renamed behavior (role-routing build): inline_data is no longer dropped;
    # it becomes an OpenAI image_url part so vision-capable models can see it.
    msgs = gemini_contents_to_oai_messages("", [
        {"role": "user", "parts": [
            {"text": "look"},
            {"inline_data": {"mime_type": "image/jpeg", "data": "AAA"}},
        ]},
    ])
    assert msgs[0] == {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
        {"type": "text", "text": "look"},
    ]}, msgs[0]
    print("ok images_dropped")


def test_chain_order():
    chain = _build_chain()
    names = [p.name for p in chain]
    assert names == ["gemini", "groq", "openrouter",
                     "mistral", "ollama_local"], names
    print("ok chain_order", names)


def test_ollama_local_leg():
    # The last-resort leg (Alek, 2026-11-09): local Ollama /v1,
    # OLLAMA_MODEL (default qwen2.5:7b), LAST in the default chain,
    # available with no API key configured — there is no key.
    from aria.agent.providers import LocalOllamaProvider, _ollama_local_base_url
    chain = _build_chain()
    local = chain[-1]
    assert local.name == "ollama_local", [p.name for p in chain]
    assert isinstance(local, LocalOllamaProvider)
    assert local.base_url == "http://localhost:11434/v1", local.base_url
    assert local.model == "qwen2.5:7b", local.model
    assert _ollama_local_base_url() == "http://localhost:11434/v1"
    assert local.is_available() is True
    print("ok ollama_local_leg", local.base_url, local.model)


def test_ollama_local_timeout_floor():
    # The cloud legs' interactive deadline (<=30s) can never serve a
    # CPU prefill; the local leg's floor is OLLAMA_LOCAL_TIMEOUT_S
    # (default 180s) in both foreground and delegated contexts.
    from aria.agent.providers import LocalOllamaProvider, OpenAICompatProvider
    local = LocalOllamaProvider("ollama_local", "http://localhost:11434/v1",
                                "qwen2.5:7b")
    assert local._call_timeout() >= 180, local._call_timeout()
    cloud = OpenAICompatProvider("groq", "https://api.groq.com/openai/v1",
                                 "k", "m")
    assert cloud._call_timeout() <= 30, cloud._call_timeout()
    print("ok ollama_local_timeout_floor", local._call_timeout())


def test_ollama_local_tool_call_roundtrip():
    # End-to-end through the leg (mocked HTTP): an Ollama /v1
    # tool_calls response must come back as a functionCall part
    # ARIA's dispatcher can execute.
    import io, urllib.request
    from aria.agent.providers import LocalOllamaProvider

    body = json.dumps({"choices": [{"message": {
        "content": "",
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "get_time",
                                     "arguments": '{"tz": "EST"}'}}],
    }}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return body

    seen = {}
    real_urlopen = urllib.request.urlopen

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _Resp()

    urllib.request.urlopen = fake_urlopen
    try:
        p = LocalOllamaProvider("ollama_local", "http://localhost:11434/v1",
                                "qwen2.5:7b")
        decls = [{"function_declarations": [
            {"name": "get_time", "description": "now",
             "parameters": {"type": "object",
                            "properties": {"tz": {"type": "string"}}}}]}]
        data = p.call("sys", [{"role": "user", "parts": [{"text": "time?"}]}],
                      tool_decls=decls)
    finally:
        urllib.request.urlopen = real_urlopen
    assert seen["url"] == "http://localhost:11434/v1/chat/completions", seen
    assert seen["timeout"] >= 180, seen
    parts = data["candidates"][0]["content"]["parts"]
    fc = [pt["functionCall"] for pt in parts if "functionCall" in pt]
    assert len(fc) == 1, parts
    assert fc[0]["name"] == "get_time" and fc[0]["args"] == {"tz": "EST"}, fc
    print("ok ollama_local_tool_call_roundtrip")


def test_ollama_cloud_skips_without_key():
    # ollama_cloud left the default chain 2026-11-10 (pay-as-you-go);
    # it stays registered, so name it explicitly via the module chain.
    import aria.agent.providers as _prov
    saved = _prov.PROVIDER_CHAIN
    _prov.PROVIDER_CHAIN = ["ollama_cloud"]
    try:
        chain = _build_chain()
    finally:
        _prov.PROVIDER_CHAIN = saved
    oc = [p for p in chain if p.name == "ollama_cloud"][0]
    assert oc.base_url == "https://ollama.com/v1", oc.base_url
    assert oc.model == "gpt-oss:120b", oc.model
    # No OLLAMA_API_KEY in this env -> must report unavailable, never attempt.
    assert oc.is_available() is False
    print("ok ollama_cloud_skips_without_key")


def test_openrouter_extra_headers():
    from aria.agent.providers import OpenAICompatProvider
    p = OpenAICompatProvider("openrouter", "https://openrouter.ai/api/v1",
                             "k", "m",
                             extra_headers={"HTTP-Referer": "r", "X-Title": "t"})
    h = p._headers()
    assert h["HTTP-Referer"] == "r" and h["X-Title"] == "t", h
    assert "Python-urllib" not in h.get("User-Agent", "")
    print("ok openrouter_extra_headers")




def test_user_agent_header():
    from aria.agent.providers import OpenAICompatProvider
    p = OpenAICompatProvider("groq", "https://api.groq.com/openai/v1", "k", "m")
    h = p._headers()
    assert "Python-urllib" not in h.get("User-Agent", ""), h
    assert h["User-Agent"], "User-Agent must be set (Cloudflare 403/1010 otherwise)"
    print("ok user_agent_header")



def test_fail_fast_single_attempt():
    # A dead provider must NOT retry: exactly one HTTP attempt, then None.
    import urllib.request, urllib.error
    from aria.agent.providers import OpenAICompatProvider
    calls = {"n": 0}
    real_urlopen = urllib.request.urlopen

    def boom(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.URLError("simulated drop")

    urllib.request.urlopen = boom
    try:
        p = OpenAICompatProvider("groq", "https://x", "k", "m")
        assert p.call("sys", [{"role": "user", "parts": [{"text": "hi"}]}]) is None
        assert calls["n"] == 1, f"retried {calls['n']}x, want 1"
    finally:
        urllib.request.urlopen = real_urlopen
    print("ok fail_fast_single_attempt")



def test_uppercase_schema_types_lowercased():
    from aria.agent.providers import gemini_decls_to_oai_tools
    decls = [{"function_declarations": [{
        "name": "t", "description": "d",
        "parameters": {"type": "OBJECT",
                       "properties": {"x": {"type": "STRING"},
                                      "items": {"type": "ARRAY",
                                                "items": {"type": "NUMBER"}}}},
    }]}]
    tools = gemini_decls_to_oai_tools(decls)
    params = tools[0]["function"]["parameters"]
    assert params["type"] == "object", params
    assert params["properties"]["x"]["type"] == "string", params
    assert params["properties"]["items"]["items"]["type"] == "number", params
    print("ok uppercase_schema_types_lowercased")

def test_user_agent_header():
    from aria.agent.providers import OpenAICompatProvider
    p = OpenAICompatProvider("groq", "https://api.groq.com/openai/v1", "k", "m")
    h = p._headers()
    assert "Python-urllib" not in h.get("User-Agent", ""), h
    assert h["User-Agent"], "User-Agent must be set (Cloudflare 403/1010 otherwise)"
    print("ok user_agent_header")


def test_uppercase_schema_types_lowercased():
    from aria.agent.providers import gemini_decls_to_oai_tools
    decls = [{"function_declarations": [{
        "name": "t", "description": "d",
        "parameters": {"type": "OBJECT",
                       "properties": {"x": {"type": "STRING"},
                                      "items": {"type": "ARRAY",
                                                "items": {"type": "NUMBER"}}}},
    }]}]
    tools = gemini_decls_to_oai_tools(decls)
    params = tools[0]["function"]["parameters"]
    assert params["type"] == "object", params
    assert params["properties"]["x"]["type"] == "string", params
    assert params["properties"]["items"]["items"]["type"] == "number", params
    print("ok uppercase_schema_types_lowercased")


def _http_error(code, body=b'{"error": {"message": "gone"}}'):
    import io, urllib.error
    return urllib.error.HTTPError("https://x/chat/completions", code, "err",
                                 {}, io.BytesIO(body))


def test_410_model_retired_no_key_quarantine():
    # HTTP 410 = model/tag retired: key must NOT be quarantined, call
    # returns None so the chain/role-fallback moves on.
    import urllib.request
    from aria.agent.providers import OpenAICompatProvider
    from aria import config as cfg_mod
    real_urlopen = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(
        _http_error(410))
    try:
        p = OpenAICompatProvider("ollama_cloud", "https://ollama.com/v1",
                                 "k-gemini-test-key", "dead-tag:999b-cloud")
        assert p.call("sys", [{"role": "user", "parts": [{"text": "hi"}]}]) is None
        assert "410" in (p.last_error or ""), p.last_error
        assert not cfg_mod.key_is_quarantined("k-gemini-test-key"), \
            "410 must not quarantine the API key"
    finally:
        urllib.request.urlopen = real_urlopen
        cfg_mod._KEY_QUARANTINE_UNTIL.pop("k-gemini-test-key", None)
    print("ok 410_model_retired_no_key_quarantine")


def test_gemini_leg_present():
    from aria.agent.providers import _build_chain
    chain = _build_chain()
    gem = [p for p in chain if p.name == "gemini"]
    assert len(gem) == 1, [p.name for p in chain]
    assert gem[0].base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    print("ok gemini_leg_present", gem[0].model)


def test_assistant_no_text_content_not_null():
    # A model block with only functionCall parts must not produce
    # "content": null — several OpenAI-compatible servers 400 on it.
    contents = [
        {"role": "model", "parts": [
            {"functionCall": {"name": "get_time", "args": {}}},
        ]},
    ]
    msgs = gemini_contents_to_oai_messages("", contents)
    asst = [m for m in msgs if m["role"] == "assistant"][0]
    assert asst["content"] == "", repr(asst["content"])
    assert len(asst["tool_calls"]) == 1
    print("ok assistant_no_text_content_not_null")


def test_payload_fingerprint():
    msgs = gemini_contents_to_oai_messages("sys", [
        {"role": "user", "parts": [{"text": "secret hello"}]},
        {"role": "model", "parts": [
            {"functionCall": {"name": "get_time", "args": {}}},
        ]},
        {"role": "user", "parts": [
            {"functionResponse": {"name": "get_time", "response": {"output": "noon"}}},
        ]},
    ])
    fp = _payload_fingerprint({"model": "m", "messages": msgs,
                               "tools": [{"type": "function"}]})
    assert "system:str" in fp, fp
    assert "user:str" in fp, fp
    assert "assistant:str+tool_calls(1)" in fp, fp
    assert "tool:str+tool_call_id" in fp, fp
    assert "tools=1" in fp, fp
    assert "secret hello" not in fp, "fingerprint must never leak message text"
    print("ok payload_fingerprint", fp)


def test_provider_mode_local_only_chain():
    # PROVIDER_MODE=local_only (Alek, 2026-11-10): the effective
    # chain is exactly [ollama_local], whatever PROVIDER_CHAIN says
    # — every API-keyed leg is bypassed, never walked.
    import aria.agent.providers as _prov
    saved_mode, saved_chain = _prov.PROVIDER_MODE, _prov.PROVIDER_CHAIN
    _prov.PROVIDER_MODE = "local_only"
    _prov.PROVIDER_CHAIN = ["gemini", "groq", "openrouter",
                            "mistral", "ollama_cloud"]
    try:
        assert _prov.effective_chain_names() == ["ollama_local"]
        names = [p.name for p in _build_chain()]
        assert names == ["ollama_local"], names
    finally:
        _prov.PROVIDER_MODE, _prov.PROVIDER_CHAIN = saved_mode, saved_chain
    print("ok provider_mode_local_only_chain")


def test_provider_mode_auto_chain_unchanged():
    # Auto mode is the pre-switch behavior, byte for byte: the
    # configured chain, in order.
    import aria.agent.providers as _prov
    saved_mode = _prov.PROVIDER_MODE
    _prov.PROVIDER_MODE = "auto"
    try:
        assert _prov.effective_chain_names() == list(_prov.PROVIDER_CHAIN)
        names = [p.name for p in _build_chain()]
    finally:
        _prov.PROVIDER_MODE = saved_mode
    assert names == ["gemini", "groq", "openrouter",
                     "mistral", "ollama_local"], names
    print("ok provider_mode_auto_chain_unchanged")


def test_provider_mode_invalid_value_behaves_auto():
    # Config normalization: only "auto" / "local_only" survive;
    # anything else (typos, wrong case is fine) becomes "auto".
    from aria import config as cfg_mod
    assert cfg_mod._normalize_provider_mode("bogus") == "auto"
    assert cfg_mod._normalize_provider_mode("") == "auto"
    assert cfg_mod._normalize_provider_mode(None) == "auto"
    assert cfg_mod._normalize_provider_mode("auto") == "auto"
    assert cfg_mod._normalize_provider_mode("LOCAL_ONLY") == "local_only"
    assert cfg_mod._normalize_provider_mode(" Local_Only ") == "local_only"
    # And the providers gate treats an unrecognized value as auto
    # too (second line of defence): the full chain still builds.
    import aria.agent.providers as _prov
    saved_mode = _prov.PROVIDER_MODE
    _prov.PROVIDER_MODE = "bogus"
    try:
        names = [p.name for p in _build_chain()]
    finally:
        _prov.PROVIDER_MODE = saved_mode
    assert names == ["gemini", "groq", "openrouter",
                     "mistral", "ollama_local"], names
    print("ok provider_mode_invalid_value_behaves_auto")


def test_role_routing_local_only_never_cloud():
    # In local_only, vision resolves to the LOCAL vision model and
    # every other role to None — never an Ollama Cloud tag, even
    # though the cloud tags are still configured.
    import aria.agent.providers as _prov
    saved_mode = _prov.PROVIDER_MODE
    saved_vision = _prov.OLLAMA_LOCAL_VISION_MODEL
    _prov.PROVIDER_MODE = "local_only"
    _prov.OLLAMA_LOCAL_VISION_MODEL = "qwen2.5vl:7b"
    _prov.clear_role_model_cooldown()
    try:
        assert _prov.resolve_role_model("vision") == "qwen2.5vl:7b"
        assert _prov.resolve_role_model("code") is None
        assert _prov.resolve_role_model("default") is None
        assert _prov.resolve_role_model("bogus") is None
        for role in ("vision", "code", "default"):
            tag = _prov.resolve_role_model(role)
            assert tag is None or "-cloud" not in tag, (role, tag)
    finally:
        _prov.PROVIDER_MODE = saved_mode
        _prov.OLLAMA_LOCAL_VISION_MODEL = saved_vision
    print("ok role_routing_local_only_never_cloud")


def test_role_routing_auto_unchanged():
    # Auto mode keeps the cloud role map exactly as before.
    import aria.agent.providers as _prov
    saved_mode = _prov.PROVIDER_MODE
    _prov.PROVIDER_MODE = "auto"
    _prov.clear_role_model_cooldown()
    try:
        assert _prov.resolve_role_model("vision") == _prov.OLLAMA_VISION_MODEL
        assert _prov.resolve_role_model("code") == _prov.OLLAMA_CODE_MODEL
        assert _prov.resolve_role_model("default") is None
    finally:
        _prov.PROVIDER_MODE = saved_mode
    print("ok role_routing_auto_unchanged")


def test_local_vision_timeout_floor():
    # A call on the local leg serving the vision model gets the
    # vision timeout floor (max of the chat floor and
    # OLLAMA_LOCAL_VISION_TIMEOUT_S); the chat model keeps the
    # plain local floor.
    from aria.agent.providers import LocalOllamaProvider
    import aria.agent.providers as _prov
    chat = LocalOllamaProvider("ollama_local", "http://localhost:11434/v1",
                               "qwen2.5:7b")
    vision = LocalOllamaProvider("ollama_local", "http://localhost:11434/v1",
                                 _prov.OLLAMA_LOCAL_VISION_MODEL)
    local_floor = float(_prov.OLLAMA_LOCAL_TIMEOUT_S)
    vision_floor = max(local_floor, float(_prov.OLLAMA_LOCAL_VISION_TIMEOUT_S))
    assert chat._call_timeout() >= local_floor, chat._call_timeout()
    assert vision._call_timeout() >= vision_floor, vision._call_timeout()
    if float(_prov.OLLAMA_LOCAL_VISION_TIMEOUT_S) > local_floor:
        assert vision._call_timeout() > chat._call_timeout(), (
            vision._call_timeout(), chat._call_timeout())
    print("ok local_vision_timeout_floor", vision._call_timeout())


def test_local_only_failure_message_names_mode():
    # Strict honesty: when the single local leg is down, the
    # all-down message names local-only mode instead of suggesting
    # an API key or a cloud retry that will never happen.
    import aria.agent.providers as _prov
    from aria import runtime
    saved_mode = _prov.PROVIDER_MODE
    _prov.PROVIDER_MODE = "local_only"
    try:
        with runtime.execution():
            ctx = runtime.current()
            ctx.provider_failures = [{
                "provider": "ollama_local", "kind": "network",
                "summary": "network connection failed or timed out"}]
            msg = _prov.provider_failure_message()
    finally:
        _prov.PROVIDER_MODE = saved_mode
    assert "Local-only mode is on" in msg, msg
    assert "No cloud provider was tried" in msg, msg
    print("ok local_only_failure_message_names_mode")


if __name__ == "__main__":
    test_system_and_user_text()
    test_model_function_call_roundtrip()
    test_oai_id_preserved()
    test_bad_tool_args()
    test_decls_mapping()
    test_synth_id_deterministic()
    test_images_dropped()
    test_chain_order()
    test_user_agent_header()
    test_fail_fast_single_attempt()
    test_uppercase_schema_types_lowercased()
    test_assistant_no_text_content_not_null()
    test_payload_fingerprint()
    test_provider_mode_local_only_chain()
    test_provider_mode_auto_chain_unchanged()
    test_provider_mode_invalid_value_behaves_auto()
    test_role_routing_local_only_never_cloud()
    test_role_routing_auto_unchanged()
    test_local_vision_timeout_floor()
    test_local_only_failure_message_names_mode()
    print("ALL PROVIDER TESTS PASSED")


