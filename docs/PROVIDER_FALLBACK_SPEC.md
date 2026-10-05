# ARIA Provider Fallback Chain — SPEC

**Status:** spec only, not built. Awaiting Alek's decisions on the open questions at the bottom.
**Date:** 2026-10-05
**Author:** Milk (from reading `aria/agent/brain.py`, `aria/config.py`, `aria/tools/schemas.py` at remote `6e0a60b3`)

---

## 1. Goal

When Gemini is unusable (all keys rate-limited, quota exhausted, auth dead, network down), ARIA keeps thinking by failing over through a chain of free providers instead of going dark:

**Gemini pool → Groq → OpenRouter → Mistral → local Ollama (final floor)**

## 2. Non-goals

- Not a rewrite of the agent loop. `run_agent`'s tool-call loop stays untouched.
- Not a quality upgrade. Fallback brains are dumber brains; the goal is *alive*, not *peak*.
- Embeddings stay Gemini-only (see §9).
- No paid tiers, no keys in the repo, no new dependencies beyond stdlib (`urllib`, already used).

## 3. Current state (grounded)

- `run_agent` (brain.py:314) builds Gemini-native `contents` (parts-based), calls `gemini_call(system_instruction, contents, tool_decls, on_text_chunk)`, and gets back `{"candidates": [{"content": {"parts": [...]}}]}` where parts carry `functionCall` or `text`.
- The tool loop executes `functionCall` parts via `execute_tool`, appends `functionResponse` parts, and re-calls. This loop is provider-agnostic *as long as the response shape is preserved*.
- `gemini_call` (brain.py:167) already does per-key rotation: `get_gemini_key()` / `quarantine_key(key, duration_s, code)` (config.py:103/123), with retries on 429/5xx/network drops.
- Tool declarations come from `get_toolkit_declarations()` (schemas.py:478) in Gemini `function_declarations` format.
- Streaming drives live speech: SSE `data:` lines → `on_text_chunk` callbacks → sentence extraction → speech queue.
- Vision: `run_agent` accepts `image_bytes`, base64'd into `inline_data` parts (brain.py:353-356).

## 4. Core design: normalize, don't fork

All providers return the **existing internal shape**: `{"candidates": [{"content": {"parts": [...]}}]}` with `functionCall` / `text` parts.

New module `aria/agent/providers.py`:

- `class Provider` — interface: `name`, `is_available() -> bool`, `call(system_instruction, contents, tool_decls, on_text_chunk) -> Optional[parts-dict]`.
- `GeminiProvider` — thin wrapper around the existing `gemini_call`. Zero behavior change.
- `OpenAICompatProvider(base_url, api_key, model)` — serves Groq, OpenRouter, Mistral. Translates (see §5), POSTs to `{base_url}/chat/completions`, parses SSE or JSON, normalizes back to parts.
- `OllamaProvider(OpenAICompatProvider)` — same thing with `base_url` default `http://localhost:11434/v1` (Ollama's OpenAI-compatible endpoint). No separate protocol code.

The **only** change in `run_agent`: `data = gemini_call(...)` becomes `data = provider_call(...)`, where `provider_call` walks the chain. Everything downstream (tool execution, history, HUD, speech) is untouched.

## 5. Translation layers (in `providers.py`)

**Outbound — Gemini contents → OpenAI messages:**
- `system_instruction` → `{"role": "system", "content": ...}` (first message).
- `{"role": "user", "parts": [...]}` → `{"role": "user", "content": <joined text>}`; multiple text parts joined with newlines.
- `{"role": "model", "parts": [...]}` → `{"role": "assistant", ...}`; `functionCall` parts become `tool_calls: [{"id": <synth-id>, "type": "function", "function": {"name", "arguments": <json>}}]`; text parts become `content`.
- `functionResponse` parts → `{"role": "tool", "tool_call_id": <synth-id>, "content": <json output>}`. A per-turn dict maps synthetic IDs both ways.
- `inline_data` (images) → **dropped** on fallback providers, with an `add_log("vision unavailable on {provider}, text-only")`. Vision is Gemini-only (see §8).

**Outbound — tools:** Gemini `function_declarations` (name/description/parameters) → OpenAI `tools: [{"type": "function", "function": {...}}]`. Straight field mapping; parameters are already JSON Schema.

**Inbound — OpenAI response → parts:**
- `choices[0].message.content` → `{"text": ...}` part.
- `choices[0].message.tool_calls[]` → `{"functionCall": {"name", "args": <parsed dict>}}` parts, in order.
- Streaming: parse OpenAI SSE (`data:` lines, `choices[0].delta.content` / `delta.tool_calls` accumulation) and feed the **same** `on_text_chunk` callback contract, so live speech keeps working identically.

## 6. Failover & recovery policy

- `provider_call` tries providers **in chain order**. A provider is skipped for the turn when `is_available()` is false (no key configured, or all its keys quarantined).
- Inside a provider, existing retry/quarantine behavior applies (HTTP 429/5xx → quarantine key, try next key; then next provider).
- `ACTIVE_PROVIDER` global tracks who's serving. On change, `add_log(f"provider now serving: {name} (fallback|recovered)")` plus a HUD subtitle note ("Running on Groq (fallback)") so Alek can see it.
- **Recovery needs no timer:** the chain is always walked in configured order, and `is_available()` skips quarantined keys without network traffic. A rate-limited provider is skipped while quarantined and retried automatically once the quarantine expires (60s for 429s, 1h for hard rejects) — the existing `_KEY_QUARANTINE_UNTIL` mechanism, reused via the new `key_is_quarantined()` helper. No re-probe timer, no sticky state.
- A turn that exhausts the chain returns the parts-dict equivalent of nothing → `run_agent`'s existing `"API connection dropped. Standing by."` path fires. No new crash modes.

## 7. Chain details

| # | Provider | Endpoint | Model (default) | Key source | Free-tier shape |
|---|----------|----------|-----------------|------------|-----------------|
| 1 | Gemini pool | existing | `MODEL_NAME` (existing) | `GEMINI_KEY_POOL` (existing) | unchanged |
| 2 | Groq | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` | `GROQ_API_KEY` | ~30 req/min, generous daily |
| 3 | OpenRouter | `https://openrouter.ai/api/v1` | configurable `:free` model (default TBD) | `OPENROUTER_API_KEY` | ~20 req/min, ~50/day |
| 4 | Mistral | `https://api.mistral.ai/v1` | `mistral-small-latest` | `MISTRAL_API_KEY` | ~1 req/sec, 1B tok/mo cap |
| 5 | Ollama | `http://localhost:11434/v1` | configurable (default TBD, e.g. `qwen2.5:7b`) | none (local) | offline floor |

Chain order itself is configurable (`PROVIDER_CHAIN` list in config); any provider with no key configured (or Ollama unreachable) is silently skipped.

## 8. Degraded capability matrix

| Capability | Gemini | Groq | OpenRouter | Mistral | Ollama |
|---|---|---|---|---|---|
| Text + tools | ✅ | ✅ | ✅ | ✅ | ✅ (reduced set) |
| Streaming speech | ✅ | ✅ | ✅ | ✅ | ✅ |
| Vision (image input) | ✅ | ❌ text-only | ❌ text-only | ❌ text-only | ❌ text-only |
| Full tool set | ✅ | ✅ | ✅ | ✅ | ⚠️ read-only default |

- **Ollama safe mode (default ON, configurable):** a 7–8B local model hallucinating tool arguments against irreversible tools (network, system, trading-adjacent) is the sharp edge. Default: Ollama tier exposes read-only tools only (`get_toolkit_declarations` filtered by a `READ_ONLY_TOOLS` allowlist). Full tools available via config flag for those who accept the risk.
- Fallback tiers announce themselves once per session in the HUD subtitle; no per-turn nagging.

## 9. Embeddings stay Gemini-only (deliberate)

`memory._embed` keeps its current Gemini-only path. Rationale: embeddings are infrequent, translating embedding models across providers adds a second matrix of subtle breakage, and the code already degrades gracefully — `_pack_embedding(None)` stores NULL and retrieval falls back to recency/text match. If Gemini is fully down, memories save without vectors. No change needed.

## 10. Config additions (config.py, following the existing `key_get` pattern)

- `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY` — env first, then settings.json, never the repo. Missing = provider skipped.
- `OPENROUTER_MODEL`, `MISTRAL_MODEL`, `OLLAMA_MODEL`, `OLLAMA_HOST` — with sane defaults.
- `PROVIDER_CHAIN` — ordered list, default `["gemini", "groq", "openrouter", "mistral", "ollama"]`.
- `OLLAMA_SAFE_MODE` — default True.
- All key values go through the existing `redact()` / `key_mask()` paths in logs and spine. No exceptions.

## 11. Rollout (each phase independently shippable)

- **Phase 1:** `providers.py` + `GeminiProvider` + `OpenAICompatProvider` + Groq only. Proves the translation pattern end-to-end. Ship bar: one real tool-calling turn on Groq, byte-identical behavior downstream.
- **Phase 2:** + OpenRouter, + Mistral. Config-only additions on the proven path.
- **Phase 3:** Ollama floor + safe-mode tool filtering + HUD failover indicator.

## 12. Testing (no quota burned)

- Translator unit tests (pure functions, no network): contents→messages→parts round-trip; tool-decl mapping; SSE chunk accumulation; synthetic tool-call ID mapping both directions.
- `--provider-ping`: dry-run mode sending one "ping" turn per configured provider, reporting latency + tool-call parse success. Run by Alek on the laptop.
- Failover simulation: temporarily quarantine all Gemini keys, confirm a turn completes on Groq and `ACTIVE_PROVIDER` flips; wait out `PROVIDER_REPROBE_S`, confirm recovery to Gemini.
- Existing audit (`audit_v7x.py` equivalent) runs on every phase — the standing rule.

## 13. Open questions for Alek

1. **Groq model:** `llama-3.3-70b-versatile` default, or something else?
2. **OpenRouter model:** which `:free` model? (Options: a Llama 3.3 70B free variant, Qwen 3 235B free, or leave configurable with no default.)
3. **Ollama model + safe mode:** which local model, and is read-only-by-default on the Ollama floor the right call?
4. **Recovery:** auto re-probe to Gemini every 15 min (spec default), or sticky fallback until restart?
5. **Keys:** env vars, `workspace/settings.json`, or both (spec default: env first, settings.json fallback, matching existing pattern)?
6. Does this spec go into the repo as `docs/PROVIDER_FALLBACK_SPEC.md`, or stay out until Phase 1 ships?
