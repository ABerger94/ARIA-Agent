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


# --- #05: locality enforcement for ollama_local ------------------------

def test_05_loopback_host_detection():
    assert pv.is_loopback_host("http://localhost:11434") is True
    assert pv.is_loopback_host("http://127.0.0.1:11434/v1") is True
    assert pv.is_loopback_host("http://127.12.34.56:11434") is True
    assert pv.is_loopback_host("http://[::1]:11434") is True
    assert pv.is_loopback_host("http://192.168.1.5:11434") is False
    assert pv.is_loopback_host("http://ollama.example.com") is False
    assert pv.is_loopback_host("http://localhost.evil.com") is False
    assert pv.is_loopback_host(12345) is False
    print("ok 05 loopback detection")


def test_05_dedicated_opener_disables_proxy_and_redirects():
    import urllib.request
    opener = pv._local_opener()
    # ProxyHandler({}) is deliberately never registered by
    # add_handler (its only method is the skipped "proxy_open"),
    # so the opener has NO proxy handling at all: local traffic
    # can never be carried off-machine by an env proxy.
    assert not any(hasattr(h, "proxies") for h in opener.handlers), (
        "local opener must have no proxy handler")
    assert "proxy" not in opener.handle_open, "no proxy open chain"
    handler = [h for h in opener.handlers
               if isinstance(h, pv._NoRedirectHandler)]
    assert handler, "local opener must refuse redirects"
    try:
        handler[0].redirect_request(
            urllib.request.Request("http://127.0.0.1/"),
            None, 302, "Found", {}, "http://evil.example/")
    except urllib.error.HTTPError as e:
        assert e.code == 302
    else:
        raise AssertionError("redirect was not refused")
    print("ok 05 dedicated opener: no proxy, no redirects")


def _fake_opener(models):
    import urllib.request

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"models": [{"name": m} for m in models]}).encode()

    class _Opener:
        def open(self, req, timeout=None):
            return _Resp()
    return _Opener()


def test_05_verify_rejects_non_loopback_in_strict_mode():
    saved = pv.OLLAMA_ALLOW_REMOTE
    pv._LOCAL_VERIFY_CACHE.clear()
    try:
        pv.OLLAMA_ALLOW_REMOTE = False
        result = pv.verify_local_ollama("http://192.168.1.5:11434",
                                        "qwen2.5:7b", force=True)
        assert result["ok"] is False and result["code"] == "non_loopback", result
        assert "OLLAMA_ALLOW_REMOTE" in result["reason"], result
        # Explicit non-strict route: the host is accepted for probing.
        pv.OLLAMA_ALLOW_REMOTE = True
        real_opener = pv._local_opener
        pv._local_opener = lambda: _fake_opener(["qwen2.5:7b"])
        try:
            result = pv.verify_local_ollama("http://192.168.1.5:11434",
                                            "qwen2.5:7b", force=True)
        finally:
            pv._local_opener = real_opener
        assert result["ok"] is True and result["code"] == "ok", result
        assert result["strict"] is False, result
    finally:
        pv.OLLAMA_ALLOW_REMOTE = saved
        pv._LOCAL_VERIFY_CACHE.clear()
    print("ok 05 non-loopback rejected in strict mode, allowed non-strict")


def test_05_verify_reports_missing_model():
    saved = pv.OLLAMA_ALLOW_REMOTE
    pv._LOCAL_VERIFY_CACHE.clear()
    pv.OLLAMA_ALLOW_REMOTE = False
    real_opener = pv._local_opener
    pv._local_opener = lambda: _fake_opener(["llama3:8b"])
    try:
        result = pv.verify_local_ollama("http://localhost:11434",
                                        "qwen2.5:7b", force=True)
        assert result["ok"] is False and result["code"] == "model_missing", result
        assert "qwen2.5:7b" in result["reason"], result
        result = pv.verify_local_ollama("http://localhost:11434",
                                        "llama3:8b", force=True)
        assert result["ok"] is True, result
    finally:
        pv._local_opener = real_opener
        pv.OLLAMA_ALLOW_REMOTE = saved
        pv._LOCAL_VERIFY_CACHE.clear()
    print("ok 05 daemon + model capability check")


def test_05_non_loopback_leg_is_unavailable_in_strict_mode():
    saved = pv.OLLAMA_ALLOW_REMOTE
    try:
        pv.OLLAMA_ALLOW_REMOTE = False
        leg = pv.LocalOllamaProvider("ollama_local",
                                     "http://192.168.1.5:11434/v1", "qwen2.5:7b")
        assert leg.is_available() is False
        pv.OLLAMA_ALLOW_REMOTE = True
        assert leg.is_available() is True
        # Loopback stays available in strict mode (daemon liveness
        # does not gate availability — it fails fast at call time).
        pv.OLLAMA_ALLOW_REMOTE = False
        local = pv.LocalOllamaProvider("ollama_local",
                                       "http://localhost:11434/v1", "qwen2.5:7b")
        assert local.is_available() is True
    finally:
        pv.OLLAMA_ALLOW_REMOTE = saved
    print("ok 05 strict non-loopback leg unavailable")


def test_05_walk_skips_missing_model_with_typed_error():
    from aria import runtime
    saved_mode = pv.PROVIDER_MODE
    saved_chain = pv._build_chain
    pv.PROVIDER_MODE = "auto"
    leg = pv.LocalOllamaProvider("ollama_local",
                                 "http://localhost:11434/v1", "qwen2.5:7b")
    leg.verify = lambda force=False: {
        "ok": False, "code": "model_missing",
        "reason": "model 'qwen2.5:7b' is not on the local Ollama daemon",
        "strict": True, "host": "http://localhost:11434",
        "model": "qwen2.5:7b", "models": []}
    pv._build_chain = lambda: [leg]
    try:
        with runtime.execution():
            assert pv.provider_call("sys", [{"role": "user",
                                             "parts": [{"text": "hi"}]}]) is None
            failures = runtime.current().provider_failures
        assert failures and failures[0]["kind"] == "local_unverified", failures
        assert "qwen2.5:7b" in failures[0]["summary"], failures
    finally:
        pv._build_chain = saved_chain
        pv.PROVIDER_MODE = saved_mode
    print("ok 05 missing-model leg skipped with typed error, no cloud fall-through")
