"""Pass 36 PROV chunk tests (p36b): findings 05, 06, 07, 10, 12, 13, 15,
16, 17, 18, 19. Pure logic + mocked HTTP, no network, no live keys.

Run:  python3 -m pytest tests/test_pass36_prov.py -q
"""
import json
import math
import os
import sqlite3
import sys
import time
import types
from contextlib import contextmanager

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
    real_save = cfg.save_keys
    # Stub the keys-file write (see test_06_policy): no test may
    # leave a flipped PROVIDER_MODE in the real aria_keys.json.
    cfg.save_keys = lambda: True
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
        cfg.save_keys = real_save
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


# --- #06: one policy per turn, rechecked per attempt -------------------

def test_06_policy_captures_mode_and_generation():
    saved_mode = pv.PROVIDER_MODE
    saved_cfg = cfg.PROVIDER_MODE
    saved_gen = pv._MODE_GENERATION
    real_save = cfg.save_keys
    # Stub the keys-file write: the toggle must not rewrite the
    # real aria_keys.json (a leftover "local_only" there would make
    # the NEXT process import local_only).
    cfg.save_keys = lambda: True
    try:
        pv.PROVIDER_MODE = "auto"
        policy = pv.current_request_policy()
        assert policy == {"mode": "auto", "generation": saved_gen}, policy
        # A real flip bumps the generation; setting the live mode
        # again does not.
        pv.set_provider_mode("local_only")
        assert pv._MODE_GENERATION == saved_gen + 1
        pv.set_provider_mode("local_only")
        assert pv._MODE_GENERATION == saved_gen + 1
        assert pv.current_request_policy()["mode"] == "local_only"
    finally:
        cfg.save_keys = real_save
        pv.PROVIDER_MODE = saved_mode
        cfg.PROVIDER_MODE = saved_cfg
        pv._MODE_GENERATION = saved_gen
    print("ok 06 policy + generation bump on real flips only")


def test_06_role_resolves_from_captured_policy_not_live_mode():
    saved_mode = pv.PROVIDER_MODE
    saved_vision = pv.OLLAMA_LOCAL_VISION_MODEL
    pv.clear_role_model_cooldown()
    try:
        pv.PROVIDER_MODE = "auto"
        policy = pv.current_request_policy()
        # Flip live to local_only AFTER capturing the policy.
        pv.PROVIDER_MODE = "local_only"
        pv.OLLAMA_LOCAL_VISION_MODEL = "qwen2.5vl:7b"
        # The captured (auto) policy still resolves the cloud tag;
        # a fresh read resolves the local tag. The tag can no
        # longer be resolved under one mode and applied under another.
        assert pv.resolve_role_model("vision", policy) == pv.OLLAMA_VISION_MODEL
        assert pv.resolve_role_model("vision") == "qwen2.5vl:7b"
        assert pv.resolve_role_model("vision", pv.current_request_policy()) == "qwen2.5vl:7b"
    finally:
        pv.PROVIDER_MODE = saved_mode
        pv.OLLAMA_LOCAL_VISION_MODEL = saved_vision
        pv.clear_role_model_cooldown()
    print("ok 06 role tag pinned to the turn's policy")


def test_06_walk_aborts_when_mode_flips_mid_walk():
    from aria import runtime

    class _Stub:
        def __init__(self, name):
            self.name = name
            self.model = "stub-model"
            self.base_url = ""
            self.api_key = "stub"
        def is_available(self):
            return True

    saved_mode = pv.PROVIDER_MODE
    saved_gen = pv._MODE_GENERATION
    real_build = pv._build_chain
    real_attempt = pv._attempt_provider_call
    attempts = []
    pv.PROVIDER_MODE = "auto"
    policy = pv.current_request_policy()

    def fake_attempt(provider, system_instruction, contents, tool_decls,
                     on_text_chunk, model_override, policy=None):
        attempts.append(provider.name)
        # The toggle flips after the first attempt starts.
        pv.PROVIDER_MODE = "local_only"
        return (None, False)

    try:
        pv._build_chain = lambda: [_Stub(n) for n in ("gemini", "groq")]
        pv._attempt_provider_call = fake_attempt
        pv.clear_provider_cooldown()
        pv.clear_model_cooldown()
        with runtime.execution():
            assert pv.provider_call("sys", [{"role": "user",
                                             "parts": [{"text": "hi"}]}],
                                    policy=policy) is None
            failures = runtime.current().provider_failures
            # Read the message INSIDE the execution context: the
            # mode_change failure lives on this turn's context.
            message = pv.provider_failure_message()
        assert attempts == ["gemini"], attempts  # groq never attempted
        assert any(f.get("kind") == "mode_change" for f in failures), failures
        assert "changed" in message.lower(), message
    finally:
        pv._build_chain = real_build
        pv._attempt_provider_call = real_attempt
        pv.PROVIDER_MODE = saved_mode
        pv._MODE_GENERATION = saved_gen
    print("ok 06 mid-walk mode flip aborts, second leg never attempted")


def test_06_attempt_records_models_actually_tried():
    p = pv.OpenAICompatProvider("ollama_cloud", "https://example.invalid/v1",
                                "k", "default-model")
    p.call = lambda *a, **k: None
    p.last_error = "HTTP 404: gone"
    try:
        data, used_fallback = pv._attempt_provider_call(
            p, "", [], None, None, "role-model",
            policy={"mode": "auto", "generation": 0})
    finally:
        pv.clear_role_model_cooldown()
        pv.clear_model_cooldown()
    assert data is None and used_fallback is True
    assert p._last_attempted_models == ["role-model", "default-model"], (
        p._last_attempted_models)
    assert p.model == "default-model", "provider model must be restored"
    print("ok 06 attempted-model record for cooldown attribution")


# --- #10: role overrides bind to a named provider + model -----------

def test_10_route_binds_vision_and_ignores_code_with_reason():
    saved_mode = pv.PROVIDER_MODE
    pv.clear_role_model_cooldown()
    try:
        pv.PROVIDER_MODE = "auto"
        vision = pv.resolve_role_route("vision")
        assert vision["status"] == "ok", vision
        assert vision["provider"] == "ollama_cloud", vision
        assert vision["model"] == pv.OLLAMA_VISION_MODEL, vision
        code = pv.resolve_role_route("code")
        assert code["status"] == "ignored", code
        assert "Alek" in code["reason"] or "product call" in code["reason"], code
        assert code["model"] == pv.OLLAMA_CODE_MODEL, code
        default = pv.resolve_role_route("default")
        assert default["status"] == "default", default
        # Local-only: vision binds the local leg, code stays ignored.
        pv.PROVIDER_MODE = "local_only"
        vision = pv.resolve_role_route("vision")
        assert (vision["status"], vision["provider"]) == ("ok", "ollama_local"), vision
        assert vision["model"] == pv.OLLAMA_LOCAL_VISION_MODEL, vision
        assert pv.resolve_role_route("code")["status"] == "ignored"
    finally:
        pv.PROVIDER_MODE = saved_mode
        pv.clear_role_model_cooldown()
    print("ok 10 route binds vision, ignores code with its reason")


def test_10_explicit_binding_is_honored_and_validated():
    saved_mode = pv.PROVIDER_MODE
    real_settings = pv.usage.settings
    pv.clear_role_model_cooldown()
    try:
        pv.PROVIDER_MODE = "auto"
        pv.usage.settings = lambda: {"role_models": {
            "code": {"provider": "groq", "model": "llama-3.3-70b"},
            "vision": "custom-vision:1b"}}
        code = pv.resolve_role_route("code")
        assert (code["status"], code["provider"], code["model"]) == (
            "ok", "groq", "llama-3.3-70b"), code
        vision = pv.resolve_role_route("vision")
        assert (vision["status"], vision["provider"]) == (
            "ok", "ollama_cloud"), vision
        pv.usage.settings = lambda: {"role_models": {
            "code": {"provider": "nope", "model": "x"}}}
        bad = pv.resolve_role_route("code")
        assert bad["status"] == "ignored" and "unknown provider" in bad["reason"], bad
        # In local-only, even an explicit cloud binding is ignored.
        pv.PROVIDER_MODE = "local_only"
        pv.usage.settings = lambda: {"role_models": {
            "code": {"provider": "groq", "model": "llama-3.3-70b"}}}
        assert pv.resolve_role_route("code")["status"] == "ignored"
    finally:
        pv.usage.settings = real_settings
        pv.PROVIDER_MODE = saved_mode
        pv.clear_role_model_cooldown()
    print("ok 10 explicit provider+model binding, unknown provider ignored")


def test_10_resolve_role_model_contract_unchanged():
    # Pinned by test_ultimate_e + test_providers: resolve_role_model
    # still returns the tag (the route layer, not the resolver, is
    # where the drop becomes visible).
    saved_mode = pv.PROVIDER_MODE
    pv.clear_role_model_cooldown()
    try:
        pv.PROVIDER_MODE = "auto"
        assert pv.resolve_role_model("code") == pv.OLLAMA_CODE_MODEL
        assert pv.resolve_role_model("vision") == pv.OLLAMA_VISION_MODEL
        assert pv.resolve_role_model("default") is None
    finally:
        pv.PROVIDER_MODE = saved_mode
        pv.clear_role_model_cooldown()
    print("ok 10 resolve_role_model return contract unchanged")


def test_10_walk_logs_ignored_override_instead_of_silently_dropping():
    from aria import runtime

    class _Stub:
        def __init__(self, name):
            self.name = name
            self.model = "stub-model"
            self.base_url = ""
            self.api_key = "stub"
        def is_available(self):
            return True

    saved_mode = pv.PROVIDER_MODE
    real_build = pv._build_chain
    real_attempt = pv._attempt_provider_call
    real_log = pv.add_log
    logged = []
    pv.add_log = lambda msg, **kw: logged.append(msg)
    pv.PROVIDER_MODE = "auto"
    try:
        pv._build_chain = lambda: [_Stub(n) for n in ("gemini", "groq")]
        pv._attempt_provider_call = lambda *a, **k: (None, False)
        pv.clear_provider_cooldown()
        pv.clear_model_cooldown()
        with runtime.execution():
            assert pv.provider_call("sys", [{"role": "user",
                                             "parts": [{"text": "hi"}]}],
                                    model_override="qwen3-coder:480b-cloud") is None
        assert any("ignored" in m and "qwen3-coder" in m for m in logged), logged
    finally:
        pv._build_chain = real_build
        pv._attempt_provider_call = real_attempt
        pv.add_log = real_log
        pv.PROVIDER_MODE = saved_mode
    print("ok 10 walk logs the ignored override with its tag")


def test_10_bound_provider_actually_serves_the_override():
    p = pv.OpenAICompatProvider("groq", "https://example.invalid/v1",
                                "k", "groq-default")
    seen = {}
    ok = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
    def fake_call(system_instruction, contents, tool_decls=None, on_text_chunk=None):
        seen["model"] = p.model
        return ok
    p.call = fake_call
    try:
        data, used = pv._attempt_provider_call(
            p, "", [], None, None, "llama-3.3-70b",
            policy={"mode": "auto", "generation": 0},
            override_provider="groq")
    finally:
        pv.clear_role_model_cooldown()
        pv.clear_model_cooldown()
    assert data is ok and used is False, (data, used)
    assert seen["model"] == "llama-3.3-70b", seen
    assert p.model == "groq-default", "provider model must be restored"
    print("ok 10 explicitly bound provider serves the override")


def test_10_work_center_validates_role_bindings():
    from aria.work_center import validate_role_models
    errors, notes = validate_role_models(
        {"code": {"provider": "groq", "model": "llama-3.3-70b"},
         "vision": "gemma4:31b-cloud"})
    assert errors == [] and notes == [], (errors, notes)
    errors, _ = validate_role_models(
        {"code": {"provider": "nope", "model": "x"}})
    assert errors and "unknown provider" in errors[0], errors
    errors, _ = validate_role_models({"code": {"provider": "groq"}})
    assert errors and "non-empty model" in errors[0], errors
    errors, _ = validate_role_models({"chat": "x"})
    assert errors and "unknown role" in errors[0], errors
    errors, _ = validate_role_models("not-a-dict")
    assert errors, errors
    errors, notes = validate_role_models({"code": "qwen3-coder:480b-cloud"})
    assert errors == [] and len(notes) == 1 and "ignored" in notes[0], notes
    print("ok 10 Work Center validates role bindings")


# --- #12: vision capability per candidate ----------------------------

_IMAGE_CONTENTS = [{"role": "user", "parts": [
    {"text": "what is this?"},
    {"inline_data": {"mime_type": "image/jpeg",
                     "data": "AAAA"}}]}]


def test_12_model_supports_images():
    assert pv.model_supports_images("ollama_cloud", "gemma4:31b-cloud") is True
    assert pv.model_supports_images("ollama_local", "qwen2.5vl:7b") is True
    assert pv.model_supports_images("ollama_cloud", "gpt-oss:120b") is False
    assert pv.model_supports_images("ollama_local", "qwen2.5:7b") is False
    assert pv.model_supports_images("gemini", "gemini-3.8-flash") is True
    assert pv.model_supports_images("ollama_cloud", "") is False
    assert pv.model_supports_images("ollama_cloud", None) is False
    print("ok 12 vision capability is per-model, not per-leg")


def test_12_classify_vision_failure():
    assert pv.classify_vision_failure("HTTP 404: model 'x' not found") == "missing_model"
    assert pv.classify_vision_failure("HTTP 410: gone") == "missing_model"
    assert pv.classify_vision_failure("network timeout") == "transient"
    assert pv.classify_vision_failure("HTTP 503: unavailable") == "transient"
    assert pv.classify_vision_failure("HTTP 400: image format unsupported") == "format"
    assert pv.classify_vision_failure("something odd") == "unknown"
    print("ok 12 failure classes separate missing model / transient / format")


def test_12_park_length_follows_failure_class():
    p = pv.OpenAICompatProvider("ollama_cloud", "https://example.invalid/v1",
                                "k", "gpt-oss:120b")
    p.call = lambda *a, **k: None
    try:
        # Missing model -> the full 15-minute park.
        p.last_error = "HTTP 404: model 'gemma4:31b-cloud' not found"
        pv._attempt_provider_call(p, "", [], None, None, "gemma4:31b-cloud",
                                  policy={"mode": "auto", "generation": 0})
        remaining = pv._ROLE_MODEL_COOLDOWN["gemma4:31b-cloud"] - time.time()
        assert 800 < remaining <= 900, remaining
        # Transient failure -> a 60-second park, so a daemon outage
        # stops suppressing recovery for a quarter of an hour.
        p.last_error = "network timeout"
        pv._attempt_provider_call(p, "", [], None, None, "gemma4:31b-cloud",
                                  policy={"mode": "auto", "generation": 0})
        remaining = pv._ROLE_MODEL_COOLDOWN["gemma4:31b-cloud"] - time.time()
        assert 30 < remaining <= 60, remaining
    finally:
        pv.clear_role_model_cooldown()
        pv.clear_model_cooldown()
    print("ok 12 park is 15 min for a missing model, 60 s for transient")


def test_12_image_never_retried_on_text_default():
    from aria import runtime
    saved_mode = pv.PROVIDER_MODE
    saved_chain = pv._build_chain
    pv.PROVIDER_MODE = "auto"
    p = pv.OpenAICompatProvider("ollama_cloud", "https://example.invalid/v1",
                                "k", "gpt-oss:120b")
    calls = []
    p.call = lambda *a, **k: (calls.append(p.model) or None)
    p.last_error = "HTTP 404: model 'gemma4:31b-cloud' not found"
    pv._build_chain = lambda: [p]
    try:
        pv.clear_role_model_cooldown()
        pv.clear_model_cooldown()
        with runtime.execution():
            out = pv.provider_call("sys", _IMAGE_CONTENTS,
                                   model_override="gemma4:31b-cloud")
            failures = runtime.current().provider_failures
        assert out is None, out
        # The override was attempted; the text default was NOT — no
        # second call on gpt-oss:120b with the image still attached.
        assert calls == ["gemma4:31b-cloud"], calls
        assert "cannot accept images" in p.last_error, p.last_error
        assert any(f.get("kind") in ("model", "response", "no_vision_capability")
                   for f in failures), failures
    finally:
        pv._build_chain = saved_chain
        pv.PROVIDER_MODE = saved_mode
        pv.clear_role_model_cooldown()
        pv.clear_model_cooldown()
    print("ok 12 image payload is not retried on the text default model")


def test_12_walk_skips_text_only_leg_for_images():
    from aria import runtime
    saved_mode = pv.PROVIDER_MODE
    saved_chain = pv._build_chain
    pv.PROVIDER_MODE = "auto"
    p = pv.OpenAICompatProvider("ollama_cloud", "https://example.invalid/v1",
                                "k", "gpt-oss:120b")
    calls = []
    p.call = lambda *a, **k: (calls.append(1) or {"candidates": []})
    pv._build_chain = lambda: [p]
    try:
        with runtime.execution():
            out = pv.provider_call("sys", _IMAGE_CONTENTS)
            failures = runtime.current().provider_failures
        assert out is None, out
        assert calls == [], calls  # never handed the picture
        assert any(f.get("kind") == "no_vision_capability"
                   for f in failures), failures
    finally:
        pv._build_chain = saved_chain
        pv.PROVIDER_MODE = saved_mode
    print("ok 12 walk skips a text-only leg for an image payload")


def test_12_vision_failure_message_names_tag_and_reason():
    from aria import runtime
    from aria import vision as vis
    saved_mode = pv.PROVIDER_MODE
    saved_chain = pv._build_chain
    # _default_vision_call reads the live mode via provider_mode()
    # each call — flip the providers global, not a vision attribute.
    pv.PROVIDER_MODE = "local_only"
    leg = pv.LocalOllamaProvider("ollama_local",
                                 "http://localhost:11434/v1", "qwen2.5vl:7b")
    leg.verify = lambda force=False: {
        "ok": False, "code": "daemon_unreachable",
        "reason": "local Ollama daemon is not reachable at http://localhost:11434",
        "strict": True, "host": "http://localhost:11434",
        "model": "qwen2.5vl:7b", "models": []}
    pv._build_chain = lambda: [leg]
    try:
        with runtime.execution():
            out = vis._default_vision_call("describe", _IMAGE_CONTENTS)
        assert out.startswith("[Vision unavailable:"), out
        assert "qwen2.5vl:7b" in out, out
        # The typed walk summary (network), not a bare "did not respond".
        assert "network connection failed" in out, out
    finally:
        pv.PROVIDER_MODE = saved_mode
        pv._build_chain = saved_chain
    print("ok 12 vision failure names the tag and the reason")


# --- #13: verified-local exemption from priced-model refusal --------

def _stub_usage_db(settings_dict, verified):
    """Point usage at an in-memory model_usage table, stub its
    settings, and stub providers' verification. Returns (usage,
    restore) — call restore() in a finally."""
    import aria.usage as us
    conn = sqlite3.connect(":memory:")
    conn.execute('''CREATE TABLE model_usage (
        id TEXT PRIMARY KEY, scope TEXT, provider TEXT, model TEXT, day TEXT,
        created REAL, latency_ms INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER,
        reserved REAL, cost REAL, estimated INTEGER, status TEXT, reason TEXT, input_rate REAL, output_rate REAL)''')
    conn.commit()

    @contextmanager
    def _connect():
        yield conn

    saved_settings, saved_connect = us.settings, us._connect
    saved_verify = pv.ollama_local_verified
    us.settings = lambda: dict(settings_dict)
    us._connect = _connect
    pv.ollama_local_verified = lambda: verified

    def restore():
        us.settings, us._connect = saved_settings, saved_connect
        pv.ollama_local_verified = saved_verify
        conn.close()
    return us, restore


def test_13_verified_local_exempt_under_zero_cap():
    from aria import runtime
    us, restore = _stub_usage_db(
        {'enforce_model_budget': True, 'daily_budget_usd': 0,
         'task_budget_usd': 0, 'model_rates': {}}, verified=True)
    try:
        with runtime.execution('foreground'):
            ticket = us.reserve('ollama_local', 'qwen2.5:7b', {})
            us.finish(ticket, usage={'prompt_tokens': 10,
                                     'completion_tokens': 5},
                      latency_ms=12)
        row = us.recent()[0]
        assert row['reserved'] == 0.0 and row['cost'] == 0.0, row
        assert row['status'] == 'ok' and row['latency_ms'] == 12, row
        assert row['prompt_tokens'] == 10 and row['completion_tokens'] == 5, row
    finally:
        restore()
    print("ok 13 verified-local leg reserves zero-cost under a $0 cap")


def test_13_unverified_local_still_refused():
    from aria import runtime
    us, restore = _stub_usage_db(
        {'enforce_model_budget': True, 'daily_budget_usd': 0,
         'task_budget_usd': 0, 'model_rates': {}}, verified=False)
    try:
        with runtime.execution('foreground'):
            try:
                us.reserve('ollama_local', 'qwen2.5:7b', {})
            except us.BudgetExceeded as e:
                assert 'Spending limits are enabled' in str(e), e
            else:
                raise AssertionError("unverified local leg was not refused")
    finally:
        restore()
    print("ok 13 unverified local leg keeps the priced-model refusal")


def test_13_remote_unpriced_still_refused():
    from aria import runtime
    us, restore = _stub_usage_db(
        {'enforce_model_budget': True, 'daily_budget_usd': 0,
         'task_budget_usd': 0, 'model_rates': {}}, verified=True)
    try:
        with runtime.execution('foreground'):
            try:
                us.reserve('mistral', 'mistral-small-latest', {})
            except us.BudgetExceeded as e:
                assert 'Spending limits are enabled' in str(e), e
            else:
                raise AssertionError("unpriced remote model was not refused")
    finally:
        restore()
    print("ok 13 verification exempts local only, never a remote model")


def test_13_verified_local_exempt_under_mission_cap():
    from aria import runtime
    us, restore = _stub_usage_db(
        {'enforce_model_budget': False, 'model_rates': {}}, verified=True)
    try:
        with runtime.execution('mission', spend_cap=0):
            ticket = us.reserve('ollama_local', 'qwen2.5:7b', {})
            assert ticket, ticket
            try:
                us.reserve('mistral', 'mistral-small-latest', {})
            except us.BudgetExceeded as e:
                assert 'approved spending cap' in str(e), e
            else:
                raise AssertionError("unpriced remote model was not refused")
    finally:
        restore()
    print("ok 13 verified-local leg also clears a zero-$ mission cap")


# --- #15: one shared request-wide deadline ---------------------------

class _CapLeg:
    """Fake leg that records the timeout_cap it was handed."""
    def __init__(self, name="gemini", model="gemini-3.8-flash"):
        self.name = name
        self.model = model
        self.base_url = "https://example.invalid/v1"
        self.api_key = "k"
        self.last_error = None
        self.caps = []
        self.calls = 0

    def is_available(self):
        return True

    def call(self, *a, **k):
        self.calls += 1
        self.caps.append(k.get("timeout_cap"))
        return {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}


def test_15_expired_deadline_starts_no_attempt():
    from aria import runtime
    saved_chain = pv._build_chain
    saved_active = pv.ACTIVE_PROVIDER
    legs = [_CapLeg("gemini"), _CapLeg("groq", "llama-3.3-70b")]
    pv._build_chain = lambda: legs
    try:
        pv.clear_provider_cooldown()
        pv.clear_model_cooldown()
        with runtime.execution():
            out = pv.provider_call(
                "sys", [{"role": "user", "parts": [{"text": "hi"}]}],
                deadline=time.monotonic() - 1)
            failures = runtime.current().provider_failures
        assert out is None, out
        assert all(leg.calls == 0 for leg in legs), [l.calls for l in legs]
        assert any(f.get("kind") == "deadline" for f in failures), failures
    finally:
        pv._build_chain = saved_chain
        pv.ACTIVE_PROVIDER = saved_active
    print("ok 15 an expired request deadline starts no attempt")


def test_15_remaining_time_caps_the_attempt():
    from aria import runtime
    saved_chain = pv._build_chain
    saved_active = pv.ACTIVE_PROVIDER
    leg = _CapLeg()
    pv._build_chain = lambda: [leg]
    try:
        pv.clear_provider_cooldown()
        pv.clear_model_cooldown()
        with runtime.execution():
            out = pv.provider_call(
                "sys", [{"role": "user", "parts": [{"text": "hi"}]}],
                deadline=time.monotonic() + 5)
        assert out is not None, out
        assert leg.calls == 1 and len(leg.caps) == 1, leg.caps
        assert 4.0 < leg.caps[0] <= 5.0, leg.caps
    finally:
        pv._build_chain = saved_chain
        pv.ACTIVE_PROVIDER = saved_active
    print("ok 15 the walk hands the remaining deadline to the attempt")


def test_15_dead_daemon_skips_second_model_attempt():
    leg = pv.LocalOllamaProvider("ollama_local",
                                 "http://localhost:11434/v1", "qwen2.5:7b")
    calls = []

    def _dead_call(*a, **k):
        calls.append(leg.model)
        leg.last_error = ("network drop: <urlopen error [Errno 111] "
                          "Connection refused>")
        return None

    leg.call = _dead_call
    try:
        data, used_fallback = pv._attempt_provider_call(
            leg, "", [], None, None, "qwen2.5vl:7b",
            policy={"mode": "local_only", "generation": 0})
        assert data is None and used_fallback is False, (data, used_fallback)
        assert calls == ["qwen2.5vl:7b"], calls  # default never retried
        assert "retry skipped" in leg.last_error, leg.last_error
    finally:
        pv.clear_role_model_cooldown()
        pv.clear_model_cooldown()
    print("ok 15 a dead daemon skips the second model attempt")


def test_15_mid_stream_drop_still_retries_default():
    leg = pv.LocalOllamaProvider("ollama_local",
                                 "http://localhost:11434/v1", "qwen2.5:7b")
    calls = []

    def _flaky_call(*a, **k):
        calls.append(leg.model)
        if len(calls) == 1:
            leg.last_error = "network drop: timed out"
            return None
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    leg.call = _flaky_call
    try:
        data, used_fallback = pv._attempt_provider_call(
            leg, "", [], None, None, "qwen2.5vl:7b",
            policy={"mode": "local_only", "generation": 0})
        assert used_fallback is True and data is not None, (data, used_fallback)
        assert calls == ["qwen2.5vl:7b", "qwen2.5:7b"], calls
    finally:
        pv.clear_role_model_cooldown()
        pv.clear_model_cooldown()
    print("ok 15 a mid-stream drop (not daemon-dead) still retries default")


# --- #17: local context sizing, num_ctx, all-part counting --------

def test_17_part_chars_counts_tool_payloads():
    import aria.agent.brain as brain
    assert brain._part_chars({"text": "hello"}) == 5
    call_chars = brain._part_chars(
        {"functionCall": {"name": "read_file", "args": {"path": "x" * 100}}})
    assert call_chars > 100, call_chars  # name + JSON args, not zero
    resp_chars = brain._part_chars(
        {"functionResponse": {"name": "read_file",
                              "response": {"content": "y" * 200}}})
    assert resp_chars > 200, resp_chars
    assert brain._part_chars({"text": "a",
                              "functionCall": {"name": "f", "args": {}}}) > 1
    print("ok 17 part size counts tool calls/results, not just text")


def test_17_compaction_counts_tool_results():
    import aria.agent.brain as brain
    saved = brain.CONVERSATION_HISTORY
    big = "y" * 10000
    entries = [
        {"role": "model", "parts": [
            {"text": "t1"},
            {"functionResponse": {"name": "f", "response": {"c": big}}}]},
        {"role": "user", "parts": [
            {"text": "t2"},
            {"functionResponse": {"name": "f", "response": {"c": big}}}]},
        {"role": "model", "parts": [
            {"text": "t3"},
            {"functionResponse": {"name": "f", "response": {"c": big}}}]},
    ]
    try:
        brain.CONVERSATION_HISTORY = list(entries)
        brain._compact_conversation_history()
        # 3 x ~10,013 chars > 24,000 cap: the oldest entry goes,
        # even though the TEXT parts total 6 chars — the old
        # text-only cap would have kept all three.
        assert len(brain.CONVERSATION_HISTORY) == 2, brain.CONVERSATION_HISTORY
        assert brain.CONVERSATION_HISTORY[0]["parts"][0]["text"] == "t2"
        assert brain._history_chars(brain.CONVERSATION_HISTORY) <= 24000
    finally:
        brain.CONVERSATION_HISTORY = saved
    print("ok 17 history cap now counts tool results, not just text")


def test_17_local_budget_and_num_ctx():
    leg = pv.LocalOllamaProvider("ollama_local",
                                 "http://localhost:11434/v1", "qwen2.5:7b")
    assert leg._payload_options() == {"num_ctx": pv.OLLAMA_NUM_CTX}, (
        leg._payload_options())
    cloud = pv.OpenAICompatProvider("ollama_cloud",
                                    "https://example.invalid/v1", "k", "m")
    assert cloud._payload_options() == {}
    budget = pv.local_prompt_char_budget()
    assert budget == max(1024, (pv.OLLAMA_NUM_CTX - 2048) * 4), budget
    assert budget >= 1024
    print(f"ok 17 local leg sends num_ctx={pv.OLLAMA_NUM_CTX}, "
          f"prompt budget {budget} chars")


def test_17_fit_trims_history_to_local_budget():
    import aria.agent.brain as brain
    saved_budget = pv.local_prompt_char_budget
    pv.local_prompt_char_budget = lambda: 1000
    try:
        contents = [
            {"role": "model", "parts": [{"text": "Systems online."}]},
            {"role": "user", "parts": [{"text": "a" * 400}]},
            {"role": "model", "parts": [{"text": "b" * 400}]},
            {"role": "user", "parts": [{"text": "c" * 400}]},
            {"role": "user", "parts": [{"text": "latest question"}]},
        ]
        brain._fit_contents_to_local_context("s" * 200, contents, [])
        # 200 + 3*400 + 15 = 1,415 > 1,000: oldest history drops
        # until it fits; primer + latest user entry always survive.
        assert contents[0]["parts"][0]["text"] == "Systems online."
        assert contents[-1]["parts"][0]["text"] == "latest question"
        assert brain._history_chars(contents) + 200 <= 1000, contents
        assert len(contents) == 3, contents
    finally:
        pv.local_prompt_char_budget = saved_budget
    print("ok 17 local compaction trims oldest history to the budget")


def test_17_fit_never_drops_primer_or_user():
    import aria.agent.brain as brain
    saved_budget = pv.local_prompt_char_budget
    pv.local_prompt_char_budget = lambda: 10  # smaller than any prompt
    try:
        contents = [
            {"role": "model", "parts": [{"text": "Systems online."}]},
            {"role": "user", "parts": [{"text": "old turn"}]},
            {"role": "user", "parts": [{"text": "latest question"}]},
        ]
        brain._fit_contents_to_local_context("s" * 200, contents, [])
        assert len(contents) == 2, contents
        assert contents[0]["parts"][0]["text"] == "Systems online."
        assert contents[-1]["parts"][0]["text"] == "latest question"
    finally:
        pv.local_prompt_char_budget = saved_budget
    print("ok 17 local compaction never drops the primer or latest turn")
