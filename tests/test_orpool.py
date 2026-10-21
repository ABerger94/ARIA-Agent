"""Unit tests for aria/agent/cooldown.py + aria/agent/orpool.py, and
the providers.py cooldown refactor — Merger Phase 2 (blueprint SS3.1,
SS2.2). The pool is driven through an injected fake transport (the
request layer is mocked; no network).

Run:  python3 tests/_run_suite.py tests/test_orpool.py
"""
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
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

import importlib

config = importlib.import_module("aria.config")
cooldown = importlib.import_module("aria.agent.cooldown")
providers = importlib.import_module("aria.agent.providers")
orpool = importlib.import_module("aria.agent.orpool")
orpool.RETRY_DELAY = 0  # tests don't sleep


# ---------------------------------------------------------------- cooldown
def test_registry_fake_clock():
    now = [1000.0]
    reg = cooldown.CooldownRegistry(60, time_fn=lambda: now[0])
    assert not reg.in_cooldown("a")
    reg.mark_failed("a")
    assert reg.in_cooldown("a")
    assert reg.until("a") == 1060.0
    now[0] = 1061.0
    assert not reg.in_cooldown("a")
    reg.mark_failed("b")
    reg.mark_ok("b")
    assert not reg.in_cooldown("b")
    reg.mark_failed("c")
    assert reg.cooling_keys() == ["c"]
    reg.clear()
    assert reg.cooling_keys() == []
    print("ok registry_fake_clock")


def test_providers_cooldown_refactor_behaviour():
    # A5 behaviour preserved through the shared registry: failure
    # cools the provider, success clears, HTTP 400 never cools.
    providers.clear_provider_cooldown()
    assert not providers._provider_in_cooldown("groq")
    providers._note_provider_failed("groq", "HTTP 503: down")
    assert providers._provider_in_cooldown("groq")
    providers._note_provider_ok("groq")
    assert not providers._provider_in_cooldown("groq")
    providers._note_provider_failed("groq", "HTTP 400: bad payload")
    assert not providers._provider_in_cooldown("groq"), \
        "HTTP 400 must never cool a provider"
    providers.clear_provider_cooldown()
    print("ok providers_cooldown_refactor_behaviour")


# ---------------------------------------------------------------- pool
class _Resp:
    def __init__(self, status_code, content=None, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {
            "choices": [{"message": {"content": content or ""}}]}

    def json(self):
        return self._payload


class _ScriptedTransport:
    """model -> list of responses/exceptions, consumed in order.
    Records every call's model."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        model = (json or {}).get("model", "")
        self.calls.append(model)
        queue = self.script.get(model) or []
        if not queue:
            return _Resp(500)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _pool(transport, models=("m1", "m2", "m3")):
    return orpool.OrPool(api_key="test-key", transport=transport,
                         text_models=list(models),
                         vision_models=["v1"])


def test_pool_429_cools_and_next_model_answers():
    transport = _ScriptedTransport({
        "m1": [_Resp(429)],
        "m2": [_Resp(200, "answer from m2"), _Resp(200, "m2 again")],
        "m3": [_Resp(200, "answer from m3")],
    })
    pool = _pool(transport)
    out = pool.chat("hello")
    assert out == "answer from m2", out
    # The 429'd model is in the shared cooldown registry...
    assert pool._cooldowns.in_cooldown("m1")
    assert "m1" in pool.available_models()["rate_limited"]
    # ...and is SKIPPED on the next call while m2 answers again.
    transport.calls.clear()
    out2 = pool.chat("hello again")
    assert out2 == "m2 again", out2
    assert "m1" not in transport.calls, transport.calls
    print("ok pool_429_cools_and_next_model_answers")


def test_pool_two_attempts_per_model():
    transport = _ScriptedTransport({
        "m1": [_Resp(500), _Resp(200, "second attempt worked")],
    })
    pool = _pool(transport, models=("m1",))
    out = pool.chat("hi")
    assert out == "second attempt worked", out
    assert transport.calls == ["m1", "m1"], transport.calls
    print("ok pool_two_attempts_per_model")


def test_pool_all_down_raises():
    transport = _ScriptedTransport({
        "m1": [_Resp(429)],
        "m2": [_Resp(500), _Resp(500)],
    })
    pool = _pool(transport, models=("m1", "m2"))
    try:
        pool.chat("hi")
        raise AssertionError("expected OrPoolError")
    except orpool.OrPoolError:
        pass
    print("ok pool_all_down_raises")


def test_pool_no_key_disables_cleanly():
    pool = orpool.OrPool(api_key="", transport=_ScriptedTransport({}))
    assert not pool.available()
    try:
        pool.chat("hi")
        raise AssertionError("expected OrPoolUnavailable")
    except orpool.OrPoolUnavailable:
        pass
    print("ok pool_no_key_disables_cleanly")


def test_pool_kill_switch():
    transport = _ScriptedTransport({"m1": [_Resp(200, "x")]})
    pool = _pool(transport, models=("m1",))
    saved = config.ORPOOL_ENABLED
    try:
        config.ORPOOL_ENABLED = False
        assert not pool.available()
        try:
            pool.chat("hi")
            raise AssertionError("expected OrPoolUnavailable")
        except orpool.OrPoolUnavailable:
            pass
    finally:
        config.ORPOOL_ENABLED = saved
    assert transport.calls == [], "kill switch must stop all traffic"
    print("ok pool_kill_switch")


def test_pool_chat_json_strips_fences():
    transport = _ScriptedTransport({
        "m1": [_Resp(200, '```json\n{"a": 1}\n```')],
    })
    pool = _pool(transport, models=("m1",))
    data = pool.chat_json("give me json")
    assert data == {"a": 1}, data
    print("ok pool_chat_json_strips_fences")


def test_pool_specific_model_first():
    transport = _ScriptedTransport({
        "m2": [_Resp(200, "from requested")],
        "m1": [_Resp(200, "from m1")],
    })
    pool = _pool(transport)
    out = pool.chat("hi", model="m2")
    assert out == "from requested", out
    assert transport.calls[0] == "m2"
    print("ok pool_specific_model_first")
