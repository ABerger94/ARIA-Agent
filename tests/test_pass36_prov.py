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


# --- #07: mode persistence is reported, not discarded ---------------

def test_07_set_mode_reports_saved_and_invalid():
    saved_mode = pv.PROVIDER_MODE
    saved_cfg = cfg.PROVIDER_MODE
    saved_status = dict(pv.PROVIDER_MODE_STATUS)
    try:
        # A valid flip reports saved=True (keys file writable here).
        assert pv.set_provider_mode("local_only") == "local_only"
        status = pv.provider_mode_status()
        assert status["mode"] == "local_only", status
        assert status.get("saved") is True, status
        assert status.get("error") is None, status
        # An invalid explicit value still coerces for the running
        # mode (no third mode exists) but is a VISIBLE error now.
        assert pv.set_provider_mode("bogus") == "auto"
        status = pv.provider_mode_status()
        assert status["mode"] == "auto", status
        assert status.get("error") and "bogus" in status["error"], status
        # A valid flip clears the error.
        assert pv.set_provider_mode("auto") == "auto"
        assert pv.provider_mode_status().get("error") is None
    finally:
        pv.PROVIDER_MODE = saved_mode
        cfg.PROVIDER_MODE = saved_cfg
        pv.PROVIDER_MODE_STATUS.clear()
        pv.PROVIDER_MODE_STATUS.update(saved_status)
    print("ok 07 mode status saved + invalid visible")


def test_07_save_failure_is_reported_not_saved(monkeypatch=None):
    # save_keys() False (e.g. broken keys file) must surface as
    # saved=False, never a silent "saved" claim.
    saved_mode = pv.PROVIDER_MODE
    saved_cfg = cfg.PROVIDER_MODE
    real_save = cfg.save_keys
    cfg.save_keys = lambda: False
    try:
        result = cfg.set_provider_mode_result("local_only")
        assert result["mode"] == "local_only", result
        assert result["saved"] is False, result
    finally:
        cfg.save_keys = real_save
        pv.PROVIDER_MODE = saved_mode
        cfg.PROVIDER_MODE = saved_cfg
    print("ok 07 failed save reported as not saved")


def test_07_env_override_is_visible():
    import os as _os
    saved = _os.environ.get("PROVIDER_MODE")
    _os.environ["PROVIDER_MODE"] = "auto"
    try:
        status = cfg.provider_mode_status()
        assert status["env_override"] is True, status
        assert "env var" in status["source"], status
    finally:
        if saved is None:
            _os.environ.pop("PROVIDER_MODE", None)
        else:
            _os.environ["PROVIDER_MODE"] = saved
    print("ok 07 env override visible in status")
