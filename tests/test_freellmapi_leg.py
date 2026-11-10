"""Unit tests for the opt-in FreeLLMAPI provider leg (Alek, 2026-11-04).

The leg fronts a locally-running FreeLLMAPI router (one OpenAI-compatible
endpoint over many free tiers) through the generic OpenAICompatProvider.
It is opt-in ONLY: the default PROVIDER_CHAIN must never include it, and
it serves solely when the chain (or a tier's light_provider) names
"freellmapi". No network.

Run:  python3 tests/test_freellmapi_leg.py
"""
import os
import sys
import types

# Bypass aria/__init__.py's eager hardware imports (same pattern as
# test_turn_tier_routing.py)
PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg

import aria.config as config
import aria.agent.providers as providers
from aria.agent.providers import (
    OpenAICompatProvider,
    resolve_tier_chain_start,
    _rotate_chain,
)


def _names(chain):
    return [p.name for p in chain]


# --- config plumbing ---

def test_config_defaults():
    # No FREELLMAPI_* in env or keys file in the test environment:
    # model defaults to the router's "auto", the base URL to the
    # desktop app's port, and no key is configured.
    assert config.FREELLMAPI_MODEL == "auto"
    assert config.FREELLMAPI_BASE_URL == "http://127.0.0.1:31415/v1"
    assert config.FREELLMAPI_API_KEY == "INSERT"
    print("ok config_defaults")


# --- default behaviour: the leg is absent ---

def test_default_chain_excludes_freellmapi():
    assert "freellmapi" not in config.PROVIDER_CHAIN
    # ollama_local joined the default chain (last, 2026-11-09);
    # freellmapi stays opt-in only.
    assert config.PROVIDER_CHAIN == [
        "gemini", "ollama_cloud", "groq", "openrouter", "mistral",
        "ollama_local"]
    assert "freellmapi" not in _names(providers._build_chain())
    print("ok default_chain_excludes_freellmapi")


# --- registry construction when the chain names it ---

def test_registry_construction(monkeypatch=None):
    # _build_chain reads the providers-module copies of the config
    # values; patch those (the pattern test_gemini_pool.py uses).
    saved = (providers.FREELLMAPI_API_KEY, providers.FREELLMAPI_MODEL,
             providers.FREELLMAPI_BASE_URL, providers.PROVIDER_CHAIN)
    try:
        providers.FREELLMAPI_API_KEY = "fla_test_key"
        providers.FREELLMAPI_MODEL = "auto"
        providers.FREELLMAPI_BASE_URL = "http://127.0.0.1:31415/v1/"
        providers.PROVIDER_CHAIN = ["gemini", "freellmapi", "mistral"]
        chain = providers._build_chain()
        names = _names(chain)
        assert "freellmapi" in names, names
        # Position: after the gemini expansion, before mistral.
        assert names.index("freellmapi") < names.index("mistral")
        leg = chain[names.index("freellmapi")]
        assert isinstance(leg, OpenAICompatProvider)
        assert leg.name == "freellmapi"
        # Trailing slash is stripped by the provider constructor.
        assert leg.base_url == "http://127.0.0.1:31415/v1"
        assert leg.api_key == "fla_test_key"
        assert leg.model == "auto"
        assert leg.is_available()
    finally:
        (providers.FREELLMAPI_API_KEY, providers.FREELLMAPI_MODEL,
         providers.FREELLMAPI_BASE_URL, providers.PROVIDER_CHAIN) = saved
    print("ok registry_construction")


def test_unconfigured_leg_is_unavailable():
    saved = (providers.FREELLMAPI_API_KEY, providers.PROVIDER_CHAIN)
    try:
        providers.FREELLMAPI_API_KEY = "INSERT"
        providers.PROVIDER_CHAIN = ["freellmapi"]
        chain = providers._build_chain()
        assert _names(chain) == ["freellmapi"]
        # No key configured -> the walk skips it like any other
        # unconfigured provider; nothing raises.
        assert not chain[0].is_available()
    finally:
        providers.FREELLMAPI_API_KEY, providers.PROVIDER_CHAIN = saved
    print("ok unconfigured_leg_is_unavailable")


def test_unknown_names_still_dropped_and_leg_resolves():
    saved = providers.PROVIDER_CHAIN
    try:
        providers.PROVIDER_CHAIN = ["nope_not_a_provider", "freellmapi"]
        assert _names(providers._build_chain()) == ["freellmapi"]
    finally:
        providers.PROVIDER_CHAIN = saved
    print("ok unknown_names_still_dropped_and_leg_resolves")


# --- tier routing can start at the leg ---

def test_tier_routing_can_start_at_freellmapi():
    real_settings = providers.usage.settings
    try:
        providers.usage.settings = lambda: {
            "turn_tiers": {"light_provider": "freellmapi"}}
        assert resolve_tier_chain_start("light") == "freellmapi"
        # Default (no settings) still starts light turns at groq.
        providers.usage.settings = lambda: {}
        assert resolve_tier_chain_start("light") == "groq"
    finally:
        providers.usage.settings = real_settings
    # Rotation moves the leg to the front, rest wrap as fallbacks.
    saved = (providers.FREELLMAPI_API_KEY, providers.PROVIDER_CHAIN)
    try:
        providers.FREELLMAPI_API_KEY = "fla_test_key"
        providers.PROVIDER_CHAIN = ["gemini", "groq", "freellmapi"]
        chain = providers._build_chain()
        rotated = _rotate_chain(chain, "freellmapi")
        assert rotated[0].name == "freellmapi"
        assert _names(rotated)[1:] == _names(chain)[:-1]
    finally:
        providers.FREELLMAPI_API_KEY, providers.PROVIDER_CHAIN = saved
    print("ok tier_routing_can_start_at_freellmapi")


if __name__ == "__main__":
    test_config_defaults()
    test_default_chain_excludes_freellmapi()
    test_registry_construction()
    test_unconfigured_leg_is_unavailable()
    test_unknown_names_still_dropped_and_leg_resolves()
    test_tier_routing_can_start_at_freellmapi()
    print("ALL PASS")
