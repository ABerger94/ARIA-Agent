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
