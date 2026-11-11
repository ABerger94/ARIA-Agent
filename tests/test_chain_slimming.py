"""Chain slimming (Alek, 2026-11-10): per-turn cloud attempt cap,
Gemini pool leg cap, fail-fast network bench.

Pins:
  * CHAIN_MAX_CLOUD_ATTEMPTS (default 2): a walk makes at most
    that many real network attempts on cloud legs; legs past the
    cap are never invoked and are recorded as 'attempt_cap';
    ollama_local is ALWAYS attempted; only_provider walks are
    exempt; cooldown/unavailable skips don't consume the cap;
  * GEMINI_POOL_MAX_LEGS (default 2): 4 pool keys expand to
    exactly 2 gemini providers — in _gemini_providers() itself,
    so the chain inherits it;
  * the 'network' failure class (split out of 'transient'):
    "network drop: ..." / timeout strings bench the provider
    AND its (provider, model) pair for NETWORK_BENCH_S
    (default 900), and bench every same-endpoint sibling on the
    provider axis — a later walk skips the whole family with
    zero network calls;
  * HTTP 400 (bad_request) still benches nothing, on any axis;
  * rate_limit durations are unchanged (60 provider / 300 model);
  * 'transient' (raised/empty/unknown) is now a modest (60, 60).
"""
import importlib
import os
import sys
import time
import types

import pytest

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

providers = importlib.import_module("aria.agent.providers")
from aria import runtime

_OK = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}


class _FakeProvider:
    def __init__(self, name, outcomes, base_url=""):
        self.name = name
        self.model = "m-" + name
        self.base_url = base_url
        self.api_key = "k-" + name
        self.last_error = None
        self._outcomes = list(outcomes)
        self.calls = 0

    def is_available(self):
        return True

    def call(self, system, contents, tool_decls=None, on_text_chunk=None):
        self.calls += 1
        out = self._outcomes.pop(0) if len(self._outcomes) > 1 \
            else self._outcomes[0]
        if isinstance(out, str):        # error marker -> last_error
            self.last_error = out
            return None
        return out


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    providers.clear_provider_cooldown()
    providers.clear_model_cooldown()
    providers.clear_role_model_cooldown()
    providers._PROVIDER_HEALTH.clear()
    providers._RECENT_FOREGROUND_PROVIDER.clear()
    monkeypatch.setattr(providers.usage, "order", lambda chain: chain)
    monkeypatch.setattr(providers, "CHAIN_MAX_CLOUD_ATTEMPTS", 2)
    monkeypatch.setattr(providers, "GEMINI_POOL_MAX_LEGS", 2)
    monkeypatch.setattr(providers, "NETWORK_BENCH_S", 900)
    yield
    providers.clear_provider_cooldown()
    providers.clear_model_cooldown()


def _walk(chain, **kwargs):
    orig = providers._build_chain
    providers._build_chain = lambda: chain
    try:
        with runtime.execution("delegated"):
            out = providers.provider_call(
                "sys", [{"role": "user", "parts": []}], **kwargs)
            kinds = [f["kind"] for f in runtime.current().provider_failures]
        return out, kinds
    finally:
        providers._build_chain = orig


def _remaining(reg, key):
    return reg.until(key) - time.time()


# --- (a) attempt cap ---

def test_attempt_cap_third_cloud_leg_never_invoked_local_still_tried():
    chain = [
        _FakeProvider("gemini", ["HTTP 503: down"], "https://g.example/v1"),
        _FakeProvider("groq", ["HTTP 503: down"], "https://q.example/v1"),
        _FakeProvider("openrouter", ["HTTP 503: down"], "https://o.example/v1"),
        _FakeProvider("mistral", ["HTTP 503: down"], "https://m.example/v1"),
        _FakeProvider("ollama_local", [_OK], "http://localhost:11434/v1"),
    ]
    out, kinds = _walk(chain)
    assert out is _OK
    assert [p.calls for p in chain] == [1, 1, 0, 0, 1]
    assert kinds.count("attempt_cap") == 2, kinds


def test_attempt_cap_only_provider_exempt():
    chain = [
        _FakeProvider("gemini", ["HTTP 503: down"], "https://g.example/v1"),
        _FakeProvider("groq", ["HTTP 503: down"], "https://q.example/v1"),
        _FakeProvider("openrouter", [_OK], "https://o.example/v1"),
    ]
    out, _kinds = _walk(chain, only_provider="openrouter")
    assert out is _OK
    assert [p.calls for p in chain] == [0, 0, 1]


def test_attempt_cap_cooldown_skips_do_not_consume():
    cooled = _FakeProvider("gemini", [_OK], "https://g.example/v1")
    providers._note_provider_failed("gemini", "HTTP 503: down")
    chain = [
        cooled,
        _FakeProvider("groq", ["HTTP 503: down"], "https://q.example/v1"),
        _FakeProvider("openrouter", [_OK], "https://o.example/v1"),
    ]
    out, _kinds = _walk(chain)
    assert out is _OK
    # gemini skipped for cooldown (free), groq attempt 1 (fails),
    # openrouter attempt 2 (serves) — the cap was never the reason
    # anything was skipped.
    assert [p.calls for p in chain] == [0, 1, 1]


# --- (b) pool cap ---

def test_pool_cap_four_keys_yield_two_legs(monkeypatch):
    monkeypatch.setattr(providers, "GEMINI_API_KEYS",
                        ["k1", "k2", "k3", "k4"])
    legs = providers._gemini_providers()
    assert [p.name for p in legs] == ["gemini", "gemini_2"]
    assert [p.api_key for p in legs] == ["k1", "k2"]
    # …and the chain inherits the cap from _gemini_providers().
    monkeypatch.setattr(providers, "PROVIDER_CHAIN", ["gemini", "groq"])
    names = [p.name for p in providers._build_chain()]
    assert names == ["gemini", "gemini_2", "groq"], names


# --- (c) network bench ---

def test_network_class_and_durations():
    assert providers._classify_error("network drop: timed out") == "network"
    assert providers._classify_error(
        "network drop: [Errno 111] Connection refused") == "network"
    assert providers._classify_error("TimeoutError: slow") == "network"
    assert providers._classify_error("No usable model response") == "transient"
    providers._note_provider_failed("gemini", "network drop: timed out")
    providers._note_model_failed("gemini", "m-gemini", "network drop: timed out")
    p = _remaining(providers._PROVIDER_COOLDOWN, "gemini")
    m = _remaining(providers._MODEL_COOLDOWN, "gemini:m-gemini")
    assert 890 < p <= 900, p
    assert 890 < m <= 900, m


def test_network_failure_benches_endpoint_family_and_next_walk_skips():
    url = "https://generativelanguage.example/v1beta/openai"
    a = _FakeProvider("gemini", ["network drop: timed out"], url)
    b = _FakeProvider("gemini_2", [_OK], url)      # same endpoint
    c = _FakeProvider("groq", [_OK], "https://q.example/v1")
    out, _kinds = _walk([a, b, c])
    assert out is _OK
    # Within the turn: a attempted, b deduped by failed_endpoints,
    # groq served. Across turns: BOTH a and b are benched ~900 s.
    assert (a.calls, b.calls, c.calls) == (1, 0, 1)
    assert _remaining(providers._PROVIDER_COOLDOWN, "gemini") > 800
    assert _remaining(providers._PROVIDER_COOLDOWN, "gemini_2") > 800
    # Family bench is provider-axis only: b's pair is untouched.
    assert not providers._model_in_cooldown("gemini_2", "m-gemini_2")
    # Next walk: the whole Gemini family is skipped with ZERO
    # network calls; groq serves again.
    out, _kinds = _walk([a, b, c])
    assert out is _OK
    assert (a.calls, b.calls, c.calls) == (1, 0, 2)


# --- (d) bad_request still benches nothing ---

def test_bad_request_walk_benches_nothing():
    bad = _FakeProvider("gemini", ["HTTP 400: bad payload"],
                        "https://g.example/v1")
    good = _FakeProvider("groq", [_OK], "https://q.example/v1")
    out, _kinds = _walk([bad, good])
    assert out is _OK and bad.calls == 1 and good.calls == 1
    assert not providers._provider_in_cooldown("gemini")
    assert not providers._model_in_cooldown("gemini", "m-gemini")


# --- (e) rate_limit preserved; transient is now (60, 60) ---

def test_rate_limit_durations_unchanged():
    providers._note_provider_failed("gemini", "HTTP 429: quota")
    providers._note_model_failed("gemini", "m-gemini", "HTTP 429: quota")
    p = _remaining(providers._PROVIDER_COOLDOWN, "gemini")
    m = _remaining(providers._MODEL_COOLDOWN, "gemini:m-gemini")
    assert 55 < p <= 60, p
    assert 295 < m <= 300, m


def test_transient_durations_now_60_60():
    providers._note_provider_failed("groq", "raised: boom")
    providers._note_model_failed("groq", "m-groq", "raised: boom")
    p = _remaining(providers._PROVIDER_COOLDOWN, "groq")
    m = _remaining(providers._MODEL_COOLDOWN, "groq:m-groq")
    assert 55 < p <= 60, p
    assert 55 < m <= 60, m


# --- config defaults ---

def test_config_keys_exist_with_defaults():
    from aria import config
    assert config.CHAIN_MAX_CLOUD_ATTEMPTS >= 1
    assert config.GEMINI_POOL_MAX_LEGS >= 1
    assert config.NETWORK_BENCH_S >= 1
    assert config._key_int("NO_SUCH_KEY_XYZ", 7) == 7
