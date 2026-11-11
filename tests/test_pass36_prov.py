"""Pass 36 PROV chunk tests (p36b): findings 05, 06, 07, 10, 12, 13, 15,
16, 17, 18, 19. Pure logic + mocked HTTP, no network, no live keys.

Run:  python3 -m pytest tests/test_pass36_prov.py -q
"""
import json
import math
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg

import aria.agent.providers as pv
from aria import config as cfg


# --- #16: malformed Ollama configuration -------------------------------

def test_16_host_must_be_http_url_string():
    assert cfg._validate_ollama_host("http://localhost:11434") == "http://localhost:11434"
    assert cfg._validate_ollama_host("https://lan:11434/") == "https://lan:11434"
    assert cfg._validate_ollama_host(12345) == "http://localhost:11434"
    assert cfg._validate_ollama_host(["http://x"]) == "http://localhost:11434"
    assert cfg._validate_ollama_host("ftp://x") == "http://localhost:11434"
    assert cfg._validate_ollama_host("localhost:11434") == "http://localhost:11434"
    assert cfg._validate_ollama_host(None) == "http://localhost:11434"
    print("ok 16 host validation")


def test_16_timeouts_must_be_finite_and_bounded():
    assert cfg._finite_timeout("180", 180.0) == 180.0
    assert cfg._finite_timeout(float("inf"), 180.0) == 180.0
    assert cfg._finite_timeout(float("nan"), 300.0) == 300.0
    assert cfg._finite_timeout("junk", 180.0) == 180.0
    assert cfg._finite_timeout(5, 180.0) == 10.0
    assert cfg._finite_timeout(9999, 180.0) == 3600.0
    assert cfg._finite_timeout(None, 180.0) == 180.0
    print("ok 16 timeout validation")


def test_16_model_tag_coercion():
    assert cfg._coerce_model_tag("qwen2.5:7b") == "qwen2.5:7b"
    assert cfg._coerce_model_tag(12345) == ""
    assert cfg._coerce_model_tag(None, "qwen2.5:7b") == "qwen2.5:7b"
    assert cfg._coerce_model_tag("") == ""
    print("ok 16 model tag coercion")


def test_16_nonstring_host_never_crashes_base_url():
    saved = pv.OLLAMA_HOST
    try:
        pv.OLLAMA_HOST = 12345
        assert pv._ollama_local_base_url() == "http://localhost:11434/v1"
        pv.OLLAMA_HOST = "http://lan:11434/v1"
        assert pv._ollama_local_base_url() == "http://lan:11434/v1"
        pv.OLLAMA_HOST = None
        assert pv._ollama_local_base_url() == "http://localhost:11434/v1"
    finally:
        pv.OLLAMA_HOST = saved
    print("ok 16 non-string host base url")


def test_16_broken_leg_never_kills_chain():
    real_gemini = pv._gemini_providers
    pv._gemini_providers = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        names = [p.name for p in pv._build_chain()]
    finally:
        pv._gemini_providers = real_gemini
    assert "gemini" not in names, names
    assert "groq" in names and "ollama_local" in names, names
    print("ok 16 broken leg skipped, chain survives", names)
