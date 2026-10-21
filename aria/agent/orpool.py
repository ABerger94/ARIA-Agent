"""
ARIA background LLM pool (Merger Phase 2 — blueprint SS2.2, SS3.1).

Adapted from or_client.py in Mark-XXXIX-OR by FatihMakes
(https://github.com/FatihMakes/Mark-XXXIX-OR), licensed CC BY-NC 4.0.
ARIA is a personal, non-commercial system; attribution is retained
here and in the commit message. His model pools, cooldown semantics
(60 s per-model 429 registry), attempt cap (2 per model), timeout
(60 s), and fallback walk are carried over. Adaptations:

  * API key comes from aria.config (aria_keys.json) instead of his
    config/api_keys.json, and NO client is constructed at import
    time — his module builds one at import and raises without a
    key; here a missing key disables the pool cleanly.
  * The 429 registry is the shared aria.agent.cooldown mechanism
    (blueprint SS3.1: one mechanism, two scopes), not a private dict.
  * Identifies as ARIA-Agent in request headers, not MARK XXV.
  * Failures log via the standard logging module AND the event ring
    (429s and pool exhaustion); total failure raises OrPoolError.
  * The HTTP transport is injectable (tests drive a fake; production
    uses requests, already a pinned ARIA dependency).

PRIVACY POSTURE (blueprint SS2.2, approved recommendation): this
pool serves BACKGROUND work only — async memory extraction,
delegated-task planning and summaries, background failure
classification, future file/vision helpers. It is NEVER called from
the live conversation path (aria.agent.brain + providers own that).
Kill switch: ARIA_ORPOOL=0 (config.ORPOOL_ENABLED).
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from aria import config as _config
from aria.config import add_log
from aria.agent.cooldown import CooldownRegistry

logger = logging.getLogger("aria.orpool")

API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7
REQUEST_TIMEOUT = 60        # seconds per request (his REQUEST_TIMEOUT)
MAX_RETRIES_PER_MODEL = 2   # attempts before moving to the next model
RETRY_DELAY = 2             # seconds between attempts on one model
RATE_LIMIT_COOLDOWN = 60    # seconds before retrying a rate-limited model

# His free-tier pools, carried over verbatim (or_client.py).
TEXT_MODELS: List[str] = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "minimax/minimax-m2.5:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "qwen/qwen3-coder:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-3-27b-it:free",
    "arcee-ai/trinity-large-preview:free",
    "z-ai/glm-4.5-air:free",
    "nvidia/nemotron-nano-30b-a3b:free",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    "google/gemma-3-12b-it:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "google/gemma-3-4b-it:free",
    "google/gemma-3n-e4b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemma-3n-e2b-it:free",
    "liquid/lfm-2.5-1.2b-instruct:free",
    "liquid/lfm-2.5-1.2b-thinking:free",
]

VISION_MODELS: List[str] = [
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "nvidia/llama-nemotron-embed-vl-1b-v2:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-3n-e4b-it:free",
    "google/gemma-3n-e2b-it:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]


class OrPoolError(RuntimeError):
    """Every model in the pool failed or is rate-limited."""


class OrPoolUnavailable(OrPoolError):
    """Pool disabled (kill switch) or no OpenRouter key configured."""


def _default_transport(url, headers=None, json=None, timeout=None):
    import requests
    return requests.post(url, headers=headers, json=json, timeout=timeout)


class OrPool:
    """OpenRouter free-pool client for background work. Constructing
    one is cheap and never raises; availability is checked per call."""

    def __init__(self, api_key: Optional[str] = None, *,
                 transport: Optional[Callable] = None,
                 text_models: Optional[List[str]] = None,
                 vision_models: Optional[List[str]] = None):
        self._api_key = api_key  # None = resolve from aria.config per call
        self._transport = transport or _default_transport
        self._text_models = list(text_models) if text_models else list(TEXT_MODELS)
        self._vision_models = list(vision_models) if vision_models else list(VISION_MODELS)
        self._cooldowns = CooldownRegistry(RATE_LIMIT_COOLDOWN)

    # -- availability -------------------------------------------------
    @property
    def api_key(self) -> str:
        if self._api_key is not None:
            return self._api_key
        key = getattr(_config, "OPENROUTER_API_KEY", "") or ""
        return "" if key == "INSERT" else key

    def available(self) -> bool:
        if not getattr(_config, "ORPOOL_ENABLED", True):
            return False
        return bool(self.api_key)

    def _require_available(self) -> None:
        if not getattr(_config, "ORPOOL_ENABLED", True):
            raise OrPoolUnavailable("background pool disabled (ARIA_ORPOOL=0)")
        if not self.api_key:
            raise OrPoolUnavailable("no OpenRouter API key configured")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ABerger94/ARIA-Agent",
            "X-Title": "ARIA-Agent",
        }

    # -- cooldown (shared mechanism, blueprint SS3.1) ------------------
    def _is_rate_limited(self, model: str) -> bool:
        return self._cooldowns.in_cooldown(model)

    def _mark_rate_limited(self, model: str) -> None:
        self._cooldowns.mark_failed(model)
        logger.warning("[orpool] Rate limited: %s — cooling down for %ss",
                       model, RATE_LIMIT_COOLDOWN)
        add_log(f"orpool: {model} rate-limited (429), cooling down "
                f"{RATE_LIMIT_COOLDOWN}s")

    # -- one model ------------------------------------------------------
    def _call(self, model: str, messages: List[dict],
              max_tokens: int = DEFAULT_MAX_TOKENS,
              temperature: float = DEFAULT_TEMPERATURE,
              response_format: Optional[dict] = None) -> Optional[str]:
        payload: Dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format

        for attempt in range(1, MAX_RETRIES_PER_MODEL + 1):
            try:
                resp = self._transport(
                    API_URL, headers=self._headers(), json=payload,
                    timeout=REQUEST_TIMEOUT)
                if resp.status_code == 429:
                    self._mark_rate_limited(model)
                    return None
                if resp.status_code == 200:
                    data = resp.json()
                    content = ((data.get("choices") or [{}])[0]
                               .get("message", {}).get("content", ""))
                    return content.strip() if content else None
                logger.warning("[orpool] %s → HTTP %s (attempt %s/%s)",
                               model, resp.status_code, attempt,
                               MAX_RETRIES_PER_MODEL)
            except Exception as e:
                # His client distinguished Timeout for the log line;
                # the recovery is identical (retry once, then move on).
                logger.warning("[orpool] %s → %s (attempt %s/%s)",
                               model, type(e).__name__, attempt,
                               MAX_RETRIES_PER_MODEL)
            if attempt < MAX_RETRIES_PER_MODEL:
                time.sleep(RETRY_DELAY)
        return None

    def _call_with_fallback(self, pool: List[str], messages: List[dict],
                            model: Optional[str] = None,
                            max_tokens: int = DEFAULT_MAX_TOKENS,
                            temperature: float = DEFAULT_TEMPERATURE,
                            response_format: Optional[dict] = None) -> str:
        self._require_available()
        if model and not self._is_rate_limited(model):
            result = self._call(model, messages, max_tokens, temperature,
                                response_format)
            if result:
                return result
            logger.info("[orpool] Requested model failed, falling back "
                        "to pool: %s", model)
        for m in pool:
            if self._is_rate_limited(m):
                continue
            logger.info("[orpool] Trying: %s", m)
            result = self._call(m, messages, max_tokens, temperature,
                                response_format)
            if result:
                logger.info("[orpool] Success: %s", m)
                return result
        add_log("orpool: all models failed or are rate-limited")
        raise OrPoolError(
            "[orpool] All models failed or are rate-limited. "
            "Check the API key and network connection.")

    # -- public API (his surface, ARIA's defaults) ----------------------
    def chat(self, prompt: str, system: str = (
            "You are a background component of ARIA, a personal AI "
            "assistant. Be concise, helpful, and precise."),
             model: Optional[str] = None,
             max_tokens: int = DEFAULT_MAX_TOKENS,
             temperature: float = DEFAULT_TEMPERATURE) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        return self._call_with_fallback(self._text_models, messages, model,
                                        max_tokens, temperature)

    def chat_json(self, prompt: str,
                  system: str = ("Return ONLY valid JSON. "
                                 "No markdown fences, no extra text, "
                                 "no explanation."),
                  model: Optional[str] = None,
                  max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        raw = self._call_with_fallback(self._text_models, messages, model,
                                       max_tokens, temperature=0.2)
        clean = raw.strip()
        if clean.startswith("```"):
            parts = clean.split("```")
            clean = parts[1] if len(parts) > 1 else clean
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip().rstrip("`").strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError as e:
            logger.error("[orpool] JSON parse failed: %s", e)
            raise ValueError(
                f"Model returned unparseable JSON: {e}\n"
                f"Raw output: {raw[:200]}")

    def vision(self, prompt: str, image_b64: str, mime: str = "image/png",
               system: str = ("Analyze the image and describe what you "
                              "see clearly and concisely."),
               model: Optional[str] = None,
               max_tokens: int = 1024) -> str:
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            },
        ]
        return self._call_with_fallback(self._vision_models, messages, model,
                                        max_tokens, temperature=0.2)

    def vision_from_file(self, prompt: str, image_path: str,
                         system: str = ("Analyze the image and describe "
                                        "what you see clearly and "
                                        "concisely."),
                         model: Optional[str] = None,
                         max_tokens: int = 1024) -> str:
        path = Path(image_path)
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        mime = mime_map.get(path.suffix.lower(), "image/png")
        with open(path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")
        return self.vision(prompt, image_b64, mime, system, model, max_tokens)

    def multi_turn(self, messages: List[dict],
                   model: Optional[str] = None,
                   max_tokens: int = DEFAULT_MAX_TOKENS,
                   temperature: float = DEFAULT_TEMPERATURE) -> str:
        return self._call_with_fallback(self._text_models, messages, model,
                                        max_tokens, temperature)

    def available_models(self) -> dict:
        return {
            "text_models": self._text_models,
            "vision_models": self._vision_models,
            "rate_limited": self._cooldowns.cooling_keys(),
            "total_text": len(self._text_models),
            "total_vision": len(self._vision_models),
        }


# -- lazy module singleton (his module built one at import; we don't) ---
_POOL: Optional[OrPool] = None
_POOL_LOCK = threading.Lock()


def get_pool() -> OrPool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = OrPool()
        return _POOL


def pool_available() -> bool:
    try:
        return get_pool().available()
    except Exception:
        return False


def chat(prompt: str, **kwargs) -> str:
    return get_pool().chat(prompt, **kwargs)


def chat_json(prompt: str, **kwargs) -> dict:
    return get_pool().chat_json(prompt, **kwargs)
