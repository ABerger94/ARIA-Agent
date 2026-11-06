"""Unit tests for turn-tier routing ("Jev") in aria/agent/providers.py:
the tier classifier, the settings resolution, chain rotation, and the
provider_call walk order with chain_start. No network.

Run:  python3 tests/test_turn_tier_routing.py
"""
import os
import sys
import types

# Bypass aria/__init__.py's eager hardware imports (same pattern as
# test_providers.py)
PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg

import aria.agent.providers as providers
from aria.agent.providers import (
    classify_turn_tier,
    resolve_tier_chain_start,
    _rotate_chain,
)


class _Stub:
    def __init__(self, name):
        self.name = name
        self.model = "stub-model"
        self.base_url = ""
        self.api_key = "stub"

    def is_available(self):
        return True


def _names(chain):
    return [p.name for p in chain]


# --- classifier: light ---

def test_light_questions_and_chat():
    for prompt in (
        "What's the capital of France?",
        "How many moons does Jupiter have?",
        "Tell me a joke",
        "Explain photosynthesis like I'm five",
        "What is a black hole?",
        "Thanks, that's helpful",
        "Who wrote Romeo and Juliet?",
    ):
        assert classify_turn_tier(prompt) == "light", prompt
    print("ok light_questions_and_chat")


def test_light_blockers_stay_standard():
    for prompt in (
        "Open Spotify and play my playlist",
        "Delete that file for me",
        "What's the weather in Dundalk?",
        "Send an email to Jordan",
        "Set a timer for ten minutes",
        "Check my calendar for tomorrow",
        "Turn off the lights",
        "Remind me about the interview",
        "Take a screenshot of this",
        "Take a photo of me",
        "Search for that article",
    ):
        assert classify_turn_tier(prompt) == "standard", prompt
    print("ok light_blockers_stay_standard")


def test_heavy_pins():
    assert classify_turn_tier("fix the bug", role="code") == "heavy"
    assert classify_turn_tier("What's up?", preauthorized=True) == "heavy"
    assert classify_turn_tier("What's up?", headless=True) == "heavy"
    print("ok heavy_pins")


def test_classifier_edges_are_standard():
    assert classify_turn_tier("") == "standard"
    assert classify_turn_tier(None) == "standard"
    assert classify_turn_tier("word " * 40 + "?") == "standard"  # too long
    assert classify_turn_tier("hmm interesting") == "standard"  # no opener
    print("ok classifier_edges_are_standard")


# --- chain rotation ---

def test_rotate_chain():
    chain = [_Stub(n) for n in ("gemini", "ollama_cloud", "groq", "openrouter", "mistral")]
    assert _names(_rotate_chain(chain, "groq")) == [
        "groq", "openrouter", "mistral", "gemini", "ollama_cloud"]
    assert _names(_rotate_chain(chain, "gemini")) == _names(chain)  # already first
    assert _rotate_chain(chain, "nope") is chain  # absent: unchanged object
    assert _rotate_chain(chain, None) is chain
    assert _rotate_chain([], "groq") == []
    print("ok rotate_chain")


# --- settings resolution ---

def test_resolve_tier_chain_start():
    real_settings = providers.usage.settings
    try:
        providers.usage.settings = lambda: {}
        assert resolve_tier_chain_start("light") == "groq"
        assert resolve_tier_chain_start("standard") is None
        assert resolve_tier_chain_start("heavy") is None
        providers.usage.settings = lambda: {"turn_tiers": {"enabled": False}}
        assert resolve_tier_chain_start("light") is None
        providers.usage.settings = lambda: {"turn_tiers": {"light_provider": "mistral"}}
        assert resolve_tier_chain_start("light") == "mistral"
    finally:
        providers.usage.settings = real_settings
    print("ok resolve_tier_chain_start")


# --- provider_call walk order ---

def test_provider_call_chain_start_order():
    attempts = []
    real_build = providers._build_chain
    real_attempt = providers._attempt_provider_call
    ok = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}

    def fake_attempt(provider, system_instruction, contents, tool_decls,
                     on_text_chunk, model_override):
        attempts.append(provider.name)
        # first attempted provider fails, the next one serves
        return (ok, False) if len(attempts) >= 2 else (None, False)

    try:
        providers._build_chain = lambda: [
            _Stub(n) for n in ("gemini", "ollama_cloud", "groq", "openrouter")]
        providers._attempt_provider_call = fake_attempt

        attempts.clear()
        data = providers.provider_call("sys", [{"role": "user", "parts": [{"text": "hi"}]}],
                                       chain_start="groq")
        assert data is ok
        assert attempts == ["groq", "openrouter"], attempts

        attempts.clear()
        data = providers.provider_call("sys", [{"role": "user", "parts": [{"text": "hi"}]}])
        assert data is ok
        assert attempts == ["gemini", "ollama_cloud"], attempts
    finally:
        providers._build_chain = real_build
        providers._attempt_provider_call = real_attempt
    print("ok provider_call_chain_start_order")


def test_provider_call_chain_start_opt_in_leg():
    # Pass 25 (pass 21 runner-up): light_provider="freellmapi"
    # with the default chain silently yielded the default order
    # — _rotate_chain cannot add an absent provider. Now the
    # tier path builds the opt-in leg from the registry and leads
    # with it; the configured chain stays as fallback.
    attempts = []
    real_build = providers._build_chain
    real_attempt = providers._attempt_provider_call
    real_by_name = providers._providers_by_name
    ok = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}

    def fake_attempt(provider, system_instruction, contents, tool_decls,
                     on_text_chunk, model_override):
        attempts.append(provider.name)
        return (ok, False) if len(attempts) >= 2 else (None, False)

    try:
        providers._build_chain = lambda: [
            _Stub(n) for n in ("gemini", "ollama_cloud", "groq")]
        providers._attempt_provider_call = fake_attempt
        providers._providers_by_name = lambda name: (
            [_Stub("freellmapi")] if name == "freellmapi" else [])
        # The earlier walk-order test parks gemini in failure
        # cooldown; clear it so the fallback order is observable.
        providers.clear_provider_cooldown()
        providers.clear_model_cooldown()

        attempts.clear()
        data = providers.provider_call("sys", [{"role": "user", "parts": [{"text": "hi"}]}],
                                       chain_start="freellmapi")
        assert data is ok
        assert attempts == ["freellmapi", "gemini"], attempts
    finally:
        providers._build_chain = real_build
        providers._attempt_provider_call = real_attempt
        providers._providers_by_name = real_by_name
    print("ok provider_call_chain_start_opt_in_leg")


def test_provider_call_chain_start_unknown_name_keeps_order():
    # An unknown chain start must not invent a leg — and must not
    # silently pretend the requested leg led, either: the
    # configured order serves, and the miss is logged.
    attempts = []
    real_build = providers._build_chain
    real_attempt = providers._attempt_provider_call
    real_by_name = providers._providers_by_name
    ok = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}

    def fake_attempt(provider, system_instruction, contents, tool_decls,
                     on_text_chunk, model_override):
        attempts.append(provider.name)
        return (ok, False) if len(attempts) >= 2 else (None, False)

    try:
        providers._build_chain = lambda: [
            _Stub(n) for n in ("gemini", "ollama_cloud", "groq")]
        providers._attempt_provider_call = fake_attempt
        providers._providers_by_name = lambda name: []
        # Same cooldown clearing as the opt-in test above.
        providers.clear_provider_cooldown()
        providers.clear_model_cooldown()

        attempts.clear()
        data = providers.provider_call("sys", [{"role": "user", "parts": [{"text": "hi"}]}],
                                       chain_start="nope")
        assert data is ok
        assert attempts == ["gemini", "ollama_cloud"], attempts
    finally:
        providers._build_chain = real_build
        providers._attempt_provider_call = real_attempt
        providers._providers_by_name = real_by_name
    print("ok provider_call_chain_start_unknown_name_keeps_order")


def test_providers_by_name_known_and_unknown():
    assert [p.name for p in providers._providers_by_name("freellmapi")] == ["freellmapi"]
    assert [p.name for p in providers._providers_by_name("groq")] == ["groq"]
    assert providers._providers_by_name("nope") == []
    assert providers._providers_by_name(None) == []
    assert providers._providers_by_name("") == []
    print("ok providers_by_name_known_and_unknown")


if __name__ == "__main__":
    test_light_questions_and_chat()
    test_light_blockers_stay_standard()
    test_heavy_pins()
    test_classifier_edges_are_standard()
    test_rotate_chain()
    test_resolve_tier_chain_start()
    test_provider_call_chain_start_order()
    test_provider_call_chain_start_opt_in_leg()
    test_provider_call_chain_start_unknown_name_keeps_order()
    test_providers_by_name_known_and_unknown()
