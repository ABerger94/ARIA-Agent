"""Gemini key pool (Alek, 2026-10-27).

aria_keys.json may carry GEMINI_API_KEYS as a JSON list. The
pool becomes one provider per key in the chain — 'gemini',
'gemini_2', 'gemini_3' — so the existing failover walk IS the
rotation and each key gets its own failure cooldowns. Pins:

  * config's loader: list form, comma-string form, dedupe,
    single-key fallback, INSERT placeholders ignored;
  * _build_chain expands 'gemini' into the pool, in chain
    position, with the right keys on the right providers;
  * a one-key pool is exactly the old single provider;
  * quarantining one key leaves its pool siblings available.
"""
import importlib
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

config = importlib.import_module("aria.config")
providers = importlib.import_module("aria.agent.providers")


def _pool_from(monkeypatch, keys_value, single=""):
    """Run config's loader against a controlled keys file."""
    store = {}
    if keys_value is not None:
        store["GEMINI_API_KEYS"] = keys_value
    if single:
        store["GEMINI_API_KEY"] = single
    monkeypatch.setattr(config, "_KEYS", store)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    single_now, _ = config.key_get("GEMINI_API_KEY")
    monkeypatch.setattr(config, "GEMINI_API_KEY", single_now)
    try:
        return config._load_gemini_keys()
    finally:
        importlib.reload(config)


def test_loader_list_dedupe_and_fallback(monkeypatch):
    assert _pool_from(monkeypatch, ["k1", "k2", "k1", " "]) == ["k1", "k2"]
    assert _pool_from(monkeypatch, "k1, k2 ,k3") == ["k1", "k2", "k3"]
    assert _pool_from(monkeypatch, ["INSERT"]) == []
    assert _pool_from(monkeypatch, None, single="solo") == ["solo"]
    assert _pool_from(monkeypatch, ["a", "b"], single="solo") == ["a", "b"]
    assert _pool_from(monkeypatch, None) == []


def test_chain_expands_the_pool_in_position(monkeypatch):
    monkeypatch.setattr(providers, "GEMINI_API_KEYS",
                        ["key-one", "key-two", "key-three"])
    monkeypatch.setattr(providers, "PROVIDER_CHAIN",
                        ["gemini", "ollama_cloud", "groq"])
    chain = providers._build_chain()
    names = [p.name for p in chain]
    assert names[:3] == ["gemini", "gemini_2", "gemini_3"]
    assert names[3] == "ollama_cloud"
    by_name = {p.name: p for p in chain}
    assert by_name["gemini"].api_key == "key-one"
    assert by_name["gemini_2"].api_key == "key-two"
    assert by_name["gemini_3"].api_key == "key-three"
    assert all(p.model == providers.GEMINI_MODEL
               for p in chain[:3])


def test_single_key_pool_is_the_old_provider(monkeypatch):
    monkeypatch.setattr(providers, "GEMINI_API_KEYS", ["only-key"])
    monkeypatch.setattr(providers, "PROVIDER_CHAIN", ["gemini", "groq"])
    chain = providers._build_chain()
    assert [p.name for p in chain][:1] == ["gemini"]
    assert chain[0].api_key == "only-key"


def test_quarantined_key_leaves_siblings_available(monkeypatch):
    monkeypatch.setattr(providers, "GEMINI_API_KEYS", ["k-a", "k-b"])
    monkeypatch.setattr(providers, "PROVIDER_CHAIN", ["gemini"])
    chain = providers._build_chain()
    config.quarantine_key("k-a", 300, "test")
    try:
        avail = {p.name: p.is_available() for p in chain}
        assert avail == {"gemini": False, "gemini_2": True}
    finally:
        config._KEY_QUARANTINE_UNTIL.pop("k-a", None)
