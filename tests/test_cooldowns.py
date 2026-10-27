"""Gap fill 5 — per-(provider, model) cooldowns + failure classes.

Pins:
  * _classify_error buckets: 400 bad_request, 429 rate_limit,
    401/403 auth, 5xx server, everything else transient;
  * durations differ by class on both axes (a 429 parks the
    MODEL for 300 s but the provider for 60; an auth failure
    parks the provider for 300; a blip only 30);
  * HTTP 400 cools NEITHER axis;
  * the walk skips a provider whose (provider, model) pair is
    cooling even when the provider axis is clear — and walks
    anyway when every candidate's pair is cooling;
  * a success heals the pair.
"""
import importlib
import os
import sys
import time
import types

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


def _reset():
    providers.clear_provider_cooldown()
    providers.clear_model_cooldown()


def _remaining(reg, key):
    return reg.until(key) - time.time()


def test_classify_error():
    c = providers._classify_error
    assert c("HTTP 400: bad payload") == "bad_request"
    assert c("HTTP 429: slow down") == "rate_limit"
    assert c("HTTP 401: nope") == "auth"
    assert c("HTTP 403: forbidden") == "auth"
    assert c("HTTP 503: down") == "server"
    assert c("HTTP 500") == "server"
    assert c("raised: ConnectionError") == "transient"
    assert c("timeout") == "transient"
    assert c(None) == "transient" and c("") == "transient"
    print("ok classify_error")


def test_class_durations_both_axes():
    _reset()
    providers._note_provider_failed("gemini", "HTTP 429: quota")
    providers._note_model_failed("gemini", "m-a", "HTTP 429: quota")
    p = _remaining(providers._PROVIDER_COOLDOWN, "gemini")
    m = _remaining(providers._MODEL_COOLDOWN, "gemini:m-a")
    assert 55 < p <= 60, p
    assert 295 < m <= 300, m
    providers._note_provider_failed("groq", "HTTP 401: bad key")
    assert _remaining(providers._PROVIDER_COOLDOWN, "groq") > 290
    providers._note_provider_failed("groq2", "raised: boom")
    r = _remaining(providers._PROVIDER_COOLDOWN, "groq2")
    assert 25 < r <= 30, r
    _reset()
    print("ok class_durations_both_axes")


def test_400_cools_neither_axis():
    _reset()
    providers._note_provider_failed("gemini", "HTTP 400: bad payload")
    providers._note_model_failed("gemini", "m-a", "HTTP 400: bad payload")
    assert not providers._provider_in_cooldown("gemini")
    assert not providers._model_in_cooldown("gemini", "m-a")
    print("ok 400_cools_neither_axis")


class _FakeProvider:
    def __init__(self, name, model, outcomes):
        self.name = name
        self.model = model
        self.api_key = "k"
        self.last_error = None
        self._outcomes = list(outcomes)
        self.calls = 0

    def is_available(self):
        return True

    def call(self, system, contents, tool_decls=None, on_text_chunk=None):
        self.calls += 1
        out = self._outcomes.pop(0) if len(self._outcomes) > 1 \
            else self._outcomes[0]
        if isinstance(out, str):        # error marker
            self.last_error = out
            return None
        return out


_OK = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}


def _walk(providers_list):
    orig = providers._build_chain
    providers._build_chain = lambda: providers_list
    try:
        return providers.provider_call("sys", [{"role": "user", "parts": []}])
    finally:
        providers._build_chain = orig


def test_walk_skips_cooling_pair_and_heals_on_success():
    _reset()
    a = _FakeProvider("gemini", "m-a", [_OK])
    b = _FakeProvider("groq", "m-b", [_OK])
    providers._note_model_failed("gemini", "m-a", "HTTP 429: quota")
    out = _walk([a, b])
    assert out is _OK and a.calls == 0 and b.calls == 1, (a.calls, b.calls)
    # pair expired/cleared -> A serves again, and serving heals
    providers.clear_model_cooldown()
    out = _walk([a, b])
    assert a.calls == 1
    assert not providers._model_in_cooldown("gemini", "m-a")
    _reset()
    print("ok walk_skips_cooling_pair_and_heals_on_success")


def test_walk_anyway_when_all_pairs_cool():
    _reset()
    a = _FakeProvider("gemini", "m-a", [_OK])
    b = _FakeProvider("groq", "m-b", [_OK])
    providers._note_model_failed("gemini", "m-a", "HTTP 429: q")
    providers._note_model_failed("groq", "m-b", "HTTP 429: q")
    out = _walk([a, b])
    assert out is _OK and a.calls == 1  # dead turn worse than sick pair
    _reset()
    print("ok walk_anyway_when_all_pairs_cool")


def test_walk_failure_notes_the_pair():
    _reset()
    a = _FakeProvider("gemini", "m-a", ["HTTP 429: quota"])
    b = _FakeProvider("groq", "m-b", [_OK])
    out = _walk([a, b])
    assert out is _OK and b.calls == 1
    assert providers._model_in_cooldown("gemini", "m-a")
    assert providers._provider_in_cooldown("gemini")
    _reset()
    print("ok walk_failure_notes_the_pair")


if __name__ == "__main__":
    test_classify_error()
    test_class_durations_both_axes()
    test_400_cools_neither_axis()
    test_walk_skips_cooling_pair_and_heals_on_success()
    test_walk_anyway_when_all_pairs_cool()
    test_walk_failure_notes_the_pair()
    print("ALL COOLDOWN CHECKS PASSED")
