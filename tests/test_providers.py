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
    parts = oai_response_to_parts({
        "choices": [{"message": {
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "x", "arguments": "{not json"}}],
        }}]
    })
    assert parts[0]["functionCall"]["args"] == {}
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
    msgs = gemini_contents_to_oai_messages("", [
        {"role": "user", "parts": [
            {"text": "look"},
            {"inline_data": {"mime_type": "image/jpeg", "data": "AAA"}},
        ]},
    ])
    assert msgs[0] == {"role": "user", "content": "look"}, msgs[0]
    print("ok images_dropped")


def test_chain_order():
    chain = _build_chain()
    names = [p.name for p in chain]
    assert names == ["gemini", "groq", "openrouter", "mistral"], names
    print("ok chain_order", names)


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
    print("ALL PROVIDER TESTS PASSED")


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
