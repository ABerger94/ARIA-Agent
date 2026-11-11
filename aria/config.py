"""
ARIA Configuration and environment management.
Centralized paths, keys, pool rotation, secret redaction, and global parameters.
"""

import os
import sys
import json
import re
import secrets
import threading
from collections import deque
from datetime import datetime

# Root paths
ROOT_DIR = os.path.abspath(os.environ.get('ARIA_DATA_ROOT') or (
    os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
WORKSPACE_DIR = os.path.join(ROOT_DIR, "workspace")
os.makedirs(WORKSPACE_DIR, exist_ok=True)

KEYS_FILE = os.path.join(ROOT_DIR, "aria_keys.json")
_KEYS_BROKEN = False


def _load_keys():
    global _KEYS_BROKEN
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, encoding="utf-8") as _f:
                _d = json.load(_f)
            return _d if isinstance(_d, dict) else {}
        except Exception as _e:
            _KEYS_BROKEN = True
            print(f"[ARIA] Keys: {KEYS_FILE} has broken JSON and was NOT loaded: {_e}", flush=True)
            print("[ARIA] Keys: usually a missing or extra comma. Fix it, then restart. "
                  "The file was NOT overwritten.", flush=True)
            return {}
    try:
        with open(KEYS_FILE, "w", encoding="utf-8") as _f:
            json.dump({"GROQ_API_KEY": "INSERT",
                       "OPENROUTER_API_KEY": "INSERT",
                       "MISTRAL_API_KEY": "INSERT",
                       "GITHUB_TOKEN": "INSERT",
                       "GITHUB_USERNAME": "ABerger94"}, _f, indent=2)
        print(f"[ARIA] Keys: created {KEYS_FILE} - paste your keys there once, then restart.")
    except Exception as _e:
        print(f"[ARIA] Keys: could not create {KEYS_FILE}: {_e}")
    return {}


_KEYS = _load_keys()


def key_get(name, fallback="INSERT"):
    if name in os.environ and os.environ[name]:
        return os.environ[name], "env var " + name
    _v = _KEYS.get(name)
    if _v and _v != "INSERT":
        return _v, "keys file"
    return fallback, "script default"


def save_keys():
    """Persist _KEYS back to aria_keys.json (used when keys change at runtime).

    Pass 25: the old code truncated aria_keys.json with a plain
    open("w") and then dumped — a failure mid-dump (disk full,
    killed process) left a truncated/partial file and destroyed
    the previous keys. Write via the canonical atomic helper
    (temp file in the same dir + os.replace) so a failed save
    leaves the previous file intact.
    """
    if _KEYS_BROKEN:
        print(f"[ARIA] Keys: NOT saving - {KEYS_FILE} has broken JSON. Fix it first.", flush=True)
        return False
    try:
        from aria import data_dir as _data_dir
        _data_dir.write_json_atomic(KEYS_FILE, _KEYS)
        return True
    except Exception as _e:
        print(f"[ARIA] Keys: could not save {KEYS_FILE}: {_e}")
        return False


_KEY_QUARANTINE_UNTIL = {}
_KEY_QUARANTINE_CODE = {}
KEY_QUARANTINE_DURATION_S = 3600


def key_is_quarantined(key):
    """True if the given provider key is currently quarantined (or missing)."""
    if not key or key == "INSERT":
        return True
    return _KEY_QUARANTINE_UNTIL.get(key, 0) > datetime.now().timestamp()


# ---- Provider fallback chain (Ollama Cloud -> Groq -> OpenRouter -> Mistral) ----
# Keys: env var first, then aria_keys.json (gitignored).
GROQ_API_KEY, _GROQ_SOURCE = key_get("GROQ_API_KEY")
OPENROUTER_API_KEY, _OPENROUTER_SOURCE = key_get("OPENROUTER_API_KEY")
MISTRAL_API_KEY, _MISTRAL_SOURCE = key_get("MISTRAL_API_KEY")
OLLAMA_CLOUD_API_KEY, _OLLAMA_CLOUD_SOURCE = key_get("OLLAMA_API_KEY")
# Gemini rides the chain via Google's OpenAI-compatible endpoint
# (v1's original provider — kept so existing GEMINI_API_KEY setups work).
GEMINI_API_KEY, _GEMINI_SOURCE = key_get("GEMINI_API_KEY")
# FreeLLMAPI (opt-in leg, Alek 2026-11-04): the unified key for a
# locally-running FreeLLMAPI router (desktop app or Docker). The leg
# is NOT in the default chain — it only serves when PROVIDER_CHAIN
# (or a tier's light_provider) names "freellmapi"; see
# _build_chain() in aria/agent/providers.py.
FREELLMAPI_API_KEY, _FREELLMAPI_SOURCE = key_get("FREELLMAPI_API_KEY")


def _load_gemini_keys():
    """The Gemini key POOL (Alek, 2026-10-27). aria_keys.json may
    carry GEMINI_API_KEYS as a JSON list:

        "GEMINI_API_KEYS": ["key-one", "key-two", "key-three"]

    (a comma-separated string also works, e.g. from an env var).
    The pool is exactly that list when it holds at least one
    usable key; otherwise it falls back to the single
    GEMINI_API_KEY, so existing setups are untouched. Deduped,
    order preserved. NOTE: free-tier quota is per Google Cloud
    PROJECT, not per key — pool keys only add capacity when
    minted in separate projects."""
    raw, _src = key_get("GEMINI_API_KEYS", "")
    if isinstance(raw, list):
        keys = [str(k).strip() for k in raw]
    elif isinstance(raw, str):
        keys = [k.strip() for k in raw.split(",")]
    else:
        keys = []
    keys = [k for k in keys if k and k != "INSERT"]
    if not keys and GEMINI_API_KEY and GEMINI_API_KEY != "INSERT":
        keys = [GEMINI_API_KEY]
    pool, seen = [], set()
    for k in keys:
        if k not in seen:
            seen.add(k)
            pool.append(k)
    return pool


GEMINI_API_KEYS = _load_gemini_keys()
if GEMINI_API_KEYS:
    # Singular consumers (Gemini Live, memory embeddings) ride
    # the pool's first key.
    GEMINI_API_KEY = GEMINI_API_KEYS[0]

GROQ_MODEL, _ = key_get("GROQ_MODEL", "openai/gpt-oss-120b")
OPENROUTER_MODEL, _ = key_get("OPENROUTER_MODEL", "nvidia/nemotron-3.5-lightning:free")
MISTRAL_MODEL, _ = key_get("MISTRAL_MODEL", "mistral-small-latest")
OLLAMA_CLOUD_MODEL, _ = key_get("OLLAMA_CLOUD_MODEL", "gpt-oss:120b")
GEMINI_MODEL, _ = key_get("GEMINI_MODEL", "gemini-3.8-flash")
FREELLMAPI_MODEL, _ = key_get("FREELLMAPI_MODEL", "auto")
# Desktop app default port is 31415; the Docker/source shape serves
# on 3001 — override FREELLMAPI_BASE_URL in aria_keys.json for that.
FREELLMAPI_BASE_URL, _ = key_get("FREELLMAPI_BASE_URL", "http://127.0.0.1:31415/v1")
# Role-based model routing (Ollama Cloud tags). Empty string disables the
# role (falls back to OLLAMA_CLOUD_MODEL) — retirement-proof overrides.
# Pass 36 #16: aria_keys.json values arrive as raw JSON — a non-string
# (number, list, object) used to crash _build_chain via .rstrip() and
# kill Gemini with it. Coerce/validate every Ollama value at load so a
# malformed local setting surfaces as a leg error, never a chain-wide
# exception.
def _coerce_model_tag(raw, fallback=""):
    """A model tag must be a non-empty string; anything else is the fallback."""
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if raw not in (None, "", "INSERT"):
        print(f"[ARIA] Config: model tag {raw!r} is not a string — using {fallback!r}.", flush=True)
    return fallback


def _validate_ollama_host(raw):
    """OLLAMA_HOST must be an http(s) URL string; anything else falls
    back to the loopback default with a visible warning."""
    default = "http://localhost:11434"
    if not isinstance(raw, str) or not raw.strip():
        if raw not in (None, "", "INSERT"):
            print(f"[ARIA] Config: OLLAMA_HOST {raw!r} is not a string — using {default}.", flush=True)
        return default
    host = raw.strip().rstrip("/")
    try:
        from urllib.parse import urlparse
        parts = urlparse(host)
    except Exception:
        parts = None
    if not parts or parts.scheme not in ("http", "https") or not parts.netloc:
        print(f"[ARIA] Config: OLLAMA_HOST {raw!r} is not an http(s) URL — using {default}.", flush=True)
        return default
    return host


def _finite_timeout(raw, default, lo=10.0, hi=3600.0):
    """A timeout must be a finite number inside [lo, hi]; inf/nan or
    junk clamps to the bound with a visible warning, never crashes."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        print(f"[ARIA] Config: timeout {raw!r} is not a number — using {default}s.", flush=True)
        return float(default)
    import math as _math
    if not _math.isfinite(value):
        print(f"[ARIA] Config: timeout {raw!r} is not finite — using {default}s.", flush=True)
        return float(default)
    if value < lo or value > hi:
        print(f"[ARIA] Config: timeout {value}s outside {lo}–{hi}s — clamped.", flush=True)
        return min(max(value, lo), hi)
    return value


OLLAMA_VISION_MODEL, _ = key_get("OLLAMA_VISION_MODEL", "gemma4:31b-cloud")
OLLAMA_VISION_MODEL = _coerce_model_tag(OLLAMA_VISION_MODEL)
OLLAMA_CODE_MODEL, _ = key_get("OLLAMA_CODE_MODEL", "qwen3-coder:480b-cloud")
OLLAMA_CODE_MODEL = _coerce_model_tag(OLLAMA_CODE_MODEL)
OLLAMA_HOST, _ = key_get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_HOST = _validate_ollama_host(OLLAMA_HOST)
OLLAMA_MODEL, _ = key_get("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_MODEL = _coerce_model_tag(OLLAMA_MODEL, "qwen2.5:7b")
# Local Ollama fallback leg (ollama_local): per-attempt timeout in
# seconds. A CPU-only 7B needs far longer than the cloud legs'
# interactive deadline just for prompt prefill, so the local leg
# gets its own floor (see LocalOllamaProvider._call_timeout).
OLLAMA_LOCAL_TIMEOUT_S, _ = key_get("OLLAMA_LOCAL_TIMEOUT_S", "180")
OLLAMA_LOCAL_TIMEOUT_S = _finite_timeout(OLLAMA_LOCAL_TIMEOUT_S, 180.0)
# Local vision model (PROVIDER_MODE=local_only): in local-only mode
# the vision role is served on-machine by this model on the local
# Ollama server instead of an Ollama Cloud tag (see
# resolve_role_model). Vision inference on CPU is the slowest call
# ARIA makes, so the vision model gets its own timeout floor (see
# LocalOllamaProvider._call_timeout). Empty string disables the
# local vision override (vision falls back to OLLAMA_MODEL).
OLLAMA_LOCAL_VISION_MODEL, _ = key_get("OLLAMA_LOCAL_VISION_MODEL", "qwen2.5vl:7b")
OLLAMA_LOCAL_VISION_MODEL = _coerce_model_tag(OLLAMA_LOCAL_VISION_MODEL)
OLLAMA_LOCAL_VISION_TIMEOUT_S, _ = key_get("OLLAMA_LOCAL_VISION_TIMEOUT_S", "300")
OLLAMA_LOCAL_VISION_TIMEOUT_S = _finite_timeout(OLLAMA_LOCAL_VISION_TIMEOUT_S, 300.0)
# Pass 36 #17: local context size (num_ctx) for the local leg —
# Ollama's default is far below ARIA's prompt size, so the leg sets
# it explicitly (see LocalOllamaProvider._payload_options).
OLLAMA_NUM_CTX, _ = key_get("OLLAMA_NUM_CTX", "4096")
try:
    OLLAMA_NUM_CTX = int(float(OLLAMA_NUM_CTX))
except (TypeError, ValueError):
    print(f"[ARIA] Config: OLLAMA_NUM_CTX {OLLAMA_NUM_CTX!r} is not a number — using 4096.", flush=True)
    OLLAMA_NUM_CTX = 4096
OLLAMA_NUM_CTX = min(max(OLLAMA_NUM_CTX, 512), 131072)
# Pass 36 #05: strict local mode rejects a non-loopback OLLAMA_HOST
# unless an explicit non-strict route is configured (OLLAMA_ALLOW_REMOTE=1
# in aria_keys.json or the environment) — a LAN Ollama is a deliberate
# choice, never an accident.
OLLAMA_ALLOW_REMOTE, _ = key_get("OLLAMA_ALLOW_REMOTE", "")
OLLAMA_ALLOW_REMOTE = str(OLLAMA_ALLOW_REMOTE or "").strip().lower() in ("1", "true", "yes")


def _normalize_provider_mode(raw) -> str:
    """PROVIDER_MODE is a two-state switch: "auto" (the configured
    provider chain serves, cloud legs included) or "local_only"
    (the local Ollama leg serves everything and no API-keyed
    provider is ever called). Anything else is a typo, not a
    third mode — fall back to "auto"."""
    mode = str(raw or "").strip().lower()
    return mode if mode in ("auto", "local_only") else "auto"


# Strict local-only switch (Alek, 2026-11-10): PROVIDER_MODE=
# "local_only" collapses the effective chain to exactly the
# ollama_local leg — the API keys stay configured and untouched,
# their providers are simply never walked. Set in aria_keys.json
# (or the PROVIDER_MODE env var); takes effect on restart like
# every other config value.
# Pass 36 #07: the startup source is recorded, not discarded —
# an env var beats the saved file at every restart, so the toggle
# must be able to say so instead of claiming a save that will
# never take effect. An invalid stored value is a visible
# configuration error, not a silent coercion to auto.
_PROVIDER_MODE_RAW, _PROVIDER_MODE_SOURCE = key_get("PROVIDER_MODE", "auto")
PROVIDER_MODE = _normalize_provider_mode(_PROVIDER_MODE_RAW)
_PROVIDER_MODE_ERROR = None
if str(_PROVIDER_MODE_RAW or "").strip().lower() not in ("auto", "local_only"):
    _PROVIDER_MODE_ERROR = (f"PROVIDER_MODE={_PROVIDER_MODE_RAW!r} is not "
                            f"'auto' or 'local_only' — using 'auto'. Fix "
                            f"aria_keys.json or the PROVIDER_MODE env var.")
    print(f"[ARIA] Config: {_PROVIDER_MODE_ERROR}", flush=True)


def _provider_mode_env_override() -> bool:
    """True when a PROVIDER_MODE env var is set: it beats the saved
    file at every restart, so a toggle save cannot change the next
    start's mode until the env var is cleared."""
    return bool(os.environ.get("PROVIDER_MODE"))


def provider_mode_status() -> dict:
    """Current mode + where it came from, for the Settings panel:
    the effective mode, the stored file value, whether an env var
    overrides the file, whether the last save persisted, and any
    invalid-value error. Never raises."""
    try:
        return {
            "mode": PROVIDER_MODE,
            "stored": _KEYS.get("PROVIDER_MODE"),
            "source": ("env var PROVIDER_MODE (overrides the saved file)"
                       if _provider_mode_env_override()
                       else ("keys file" if "PROVIDER_MODE" in _KEYS
                             else "default (auto)")),
            "env_override": _provider_mode_env_override(),
            "error": _PROVIDER_MODE_ERROR,
        }
    except Exception:
        return {"mode": "auto", "stored": None, "source": "unknown",
                "env_override": False, "error": None}


def set_provider_mode_result(mode) -> dict:
    """Switch PROVIDER_MODE at runtime and report the full outcome:
    {"mode", "saved", "error", "env_override", "source"}.

    "saved" is save_keys()'s real result — a failed write used to
    be discarded and the toggle still claimed success. "error" is
    set when the requested value is not auto/local_only: the
    running mode still coerces to auto (there is no third mode to
    run as), but the misconfiguration is now visible instead of
    silent. When an env var overrides the file, "saved" can be
    True and the next restart still restores the env value — the
    panel must show that, not just "saved"."""
    global PROVIDER_MODE, _PROVIDER_MODE_ERROR
    raw = mode
    PROVIDER_MODE = _normalize_provider_mode(mode)
    error = None
    if str(raw or "").strip().lower() not in ("auto", "local_only"):
        error = (f"PROVIDER_MODE={raw!r} is not 'auto' or "
                 f"'local_only' — using 'auto'. Fix aria_keys.json "
                 f"or the PROVIDER_MODE env var.")
        _PROVIDER_MODE_ERROR = error
        print(f"[ARIA] Config: {error}", flush=True)
    else:
        _PROVIDER_MODE_ERROR = None
    saved = False
    try:
        _KEYS["PROVIDER_MODE"] = PROVIDER_MODE
        saved = bool(save_keys())
    except Exception:
        saved = False
    if not saved:
        print(f"[ARIA] Config: PROVIDER_MODE={PROVIDER_MODE!r} was NOT "
              f"saved to {KEYS_FILE} — the next restart will restore "
              f"the previous value.", flush=True)
    status = provider_mode_status()
    status.update({"mode": PROVIDER_MODE, "saved": saved, "error": error})
    return status


def set_provider_mode(mode) -> str:
    """Switch PROVIDER_MODE at runtime (Settings → Providers
    "Local only" toggle). Normalizes like the startup read —
    anything that is not exactly "local_only" becomes "auto" —
    persists the choice to aria_keys.json via save_keys() so a
    restart keeps it, and returns the effective mode. Consumers
    read the mode through aria.agent.providers.provider_mode()
    (live module state), NOT this module global: rebinding here
    does not update names already imported elsewhere, so the
    providers module owns the live value and this function keeps
    it — and the persisted file — in step. Never raises; a broken
    keys file refuses the write inside save_keys() and the
    in-memory mode still applies for the session.

    Pass 36 #07: the persistence result is no longer discarded —
    see set_provider_mode_result(); this wrapper keeps the old
    return contract (effective mode string) for existing callers."""
    return set_provider_mode_result(mode)["mode"]

# Chain: gemini -> groq -> openrouter -> mistral -> ollama_local
# (Gemini promoted to primary 2026-10-27, Alek's call — the old
# fallbacks were the weak path; ollama_local added 2026-11-09 as the
# last-resort leg: the local Ollama server on OLLAMA_HOST running
# OLLAMA_MODEL, there when every cloud leg is down; ollama_cloud
# removed from the default chain 2026-11-10, Alek's call — Ollama
# cloud went pay-as-you-go; the provider stays registered so an
# explicit PROVIDER_CHAIN override can still name it). Reorder any
# time via the PROVIDER_CHAIN override,
# e.g. PROVIDER_CHAIN="groq,mistral,openrouter,ollama_local".
# Pass 36 #16: chain names are validated at load — a non-string
# PROVIDER_CHAIN or an unknown leg name is dropped with a warning,
# never a chain-wide crash; an empty result falls back to the default.
_KNOWN_PROVIDER_NAMES = frozenset({
    "gemini", "groq", "openrouter", "mistral", "ollama_cloud",
    "freellmapi", "ollama_local"})
_DEFAULT_PROVIDER_CHAIN = ["gemini", "groq", "openrouter", "mistral", "ollama_local"]
_PROVIDER_CHAIN_RAW, _ = key_get("PROVIDER_CHAIN", "")
if isinstance(_PROVIDER_CHAIN_RAW, str) and _PROVIDER_CHAIN_RAW and _PROVIDER_CHAIN_RAW != "INSERT":
    _chain_names = [p.strip().lower() for p in _PROVIDER_CHAIN_RAW.split(",") if p.strip()]
    _unknown = [p for p in _chain_names if p not in _KNOWN_PROVIDER_NAMES]
    if _unknown:
        print(f"[ARIA] Config: unknown provider(s) in PROVIDER_CHAIN ignored: {_unknown}.", flush=True)
    PROVIDER_CHAIN = [p for p in _chain_names if p in _KNOWN_PROVIDER_NAMES] or list(_DEFAULT_PROVIDER_CHAIN)
else:
    if _PROVIDER_CHAIN_RAW not in (None, "", "INSERT"):
        print(f"[ARIA] Config: PROVIDER_CHAIN {_PROVIDER_CHAIN_RAW!r} is not a string — using the default chain.", flush=True)
    PROVIDER_CHAIN = list(_DEFAULT_PROVIDER_CHAIN)

# Ollama safe mode (Phase 3): local model gets read-only tools only.
OLLAMA_SAFE_MODE = True


def key_mask(key):
    if not key or key == "INSERT":
        return "none"
    return key[:6] + "..." + key[-4:] if len(key) >= 12 else key[:3] + "..."


def quarantine_key(key, duration_s=KEY_QUARANTINE_DURATION_S, code=429):
    if not key:
        return
    _KEY_QUARANTINE_UNTIL[key] = datetime.now().timestamp() + duration_s
    _KEY_QUARANTINE_CODE[key] = code
    return None


def _secret_values():
    vals = []
    def collect(value):
        if isinstance(value, str):
            vals.append(value)
        elif isinstance(value, (list, tuple)):
            for entry in value:
                collect(entry)
        elif isinstance(value, dict):
            for name, entry in value.items():
                if _credential_field(name):
                    collect(entry)
    for source in (_KEYS, os.environ):
        for name, value in source.items():
            if _credential_field(name):
                collect(value)
    return sorted({v for v in vals if len(v) > 6 and v != "INSERT"},
                  key=len, reverse=True)


def _credential_field(name):
    return bool(re.search(r'password|passwd|secret|token|api_?keys?|authorization|cookie|private_?key',
                          str(name), re.I)) or str(name).lower() in {'env', 'environment', 'headers'}


def redact(text):
    if not text or not isinstance(text, str):
        return text
    for v in _secret_values():
        text = text.replace(v, "[redacted]")
    return text


def redact_data(value):
    """Redact credential fields before serialization, including new secrets.

    A setup tool's credential is not in _KEYS yet when its invocation is
    audited. Scrubbing field names therefore precedes known-value redaction.
    """
    if isinstance(value, dict):
        return {key: '[redacted]' if _credential_field(key) else redact_data(entry)
                for key, entry in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_data(entry) for entry in value]
    return redact(value) if isinstance(value, str) else value


GITHUB_TOKEN, _GITHUB_SOURCE = key_get("GITHUB_TOKEN")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", _KEYS.get("GITHUB_USERNAME") or "ABerger94")
GITHUB_ARMED = bool(GITHUB_TOKEN) and GITHUB_TOKEN != "INSERT"
GITHUB_STATUS = "ARMED" if GITHUB_ARMED else "NO KEY"


def _ensure_bridge_token():
    if _KEYS_BROKEN:
        try:
            with open(KEYS_FILE, encoding="utf-8") as _f:
                _raw = _f.read()
            import re
            m = re.search(r'"bridge_token"\s*:\s*"([^"]+)"', _raw)
            if m:
                _KEYS["bridge_token"] = m.group(1)
                return m.group(1)
        except Exception as _e_silent:
            log_silent("_ensure_bridge_token", _e_silent)
        _tok = secrets.token_urlsafe(16)
        _KEYS["bridge_token"] = _tok
        return _tok
    try:
        with open(KEYS_FILE, encoding="utf-8") as _f:
            _d = json.load(_f)
            _d = _d if isinstance(_d, dict) else {}
    except Exception:
        _d = {}
    _tok = _d.get("bridge_token")
    if not _tok:
        _tok = secrets.token_urlsafe(16)
        _d["bridge_token"] = _tok
        try:
            with open(KEYS_FILE, "w", encoding="utf-8") as _f:
                json.dump(_d, _f, indent=2)
        except Exception as _e:
            print(f"[ARIA] Bridge: could not save token: {_e}", flush=True)
    _KEYS["bridge_token"] = _tok
    return _tok


BRIDGE_TOKEN = _ensure_bridge_token()

# Models and constants
# Semantic-memory embedding model served by Ollama Cloud (768-dim, so stored
# packed float32 embeddings from the retired model stay compatible).
EMBED_MODEL, _EMBED_MODEL_SOURCE = key_get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
EDGE_TTS_VOICE = "en-US-AriaNeural"
PHONE_BRIDGE_PORT = 8777

def lan_ip() -> str:
    """Find local network IP address for LAN access."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


MAX_TOOL_OUTPUT = 2000
HISTORY_TURNS = 10
AGENT_LOOP_TIME_BUDGET_S = 1800

# ---------------------------------------------------------------- merger (Phases 1-3)
# Feature flags + caps for the Mark-XXXIX-OR merger organs (blueprint:
# ~/workspace/aria-audit/MERGER-BLUEPRINT.md SS3.2/SS3.3). The flags are
# env-overridable rollback switches, read dynamically by their consumers.
# The caps are the contract (blueprint SS3.3): planner/executor/queue
# read them from here, never from literals at call sites.
FAILURE_SURFACING = os.environ.get(
    "ARIA_FAILURE_SURFACING", "1").strip().lower() not in ("0", "false", "no")
ORPOOL_ENABLED = os.environ.get(
    "ARIA_ORPOOL", "1").strip().lower() not in ("0", "false", "no")
MEMORY_EXTRACT_ASYNC = os.environ.get(
    "ARIA_MEMORY_EXTRACT_ASYNC", "1").strip().lower() not in ("0", "false", "no")

PLAN_MAX_STEPS = 5                 # SS3.3: a plan is at most 5 steps
STEP_MAX_ATTEMPTS = 3              # SS3.3: per-step attempts <= 3
MAX_REPLAN_ATTEMPTS = 2            # SS3.3: replans per task <= 2
ERROR_USER_MESSAGE_MAX_WORDS = 15  # SS3.2/SS3.3: spoken failure text cap
TASK_QUEUE_MAX_CONCURRENT = 1      # SS3.3: queue concurrency is 1
DELEGATED_TASK_BUDGET_S = 600      # SS3.3: whole-task wall-clock budget

# File organ (Merger Phase 5 — blueprint SS2.4 #1, SS5 Phase 5).
# FILEPROC_ENABLED is the rollback switch (ARIA_FILEPROC=0 unregisters
# the organ's behaviour; the drop zone falls back to receipt-only).
# The byte/char caps bound what the organ will read, transcribe, or
# hand to the background pool — readers stop at the cap and say so.
FILEPROC_ENABLED = os.environ.get(
    "ARIA_FILEPROC", "1").strip().lower() not in ("0", "false", "no")
FILEPROC_MAX_FILE_BYTES = 64 * 1024 * 1024        # over: metadata only
FILEPROC_TEXT_MAX_CHARS = 20000                   # extracted text kept
FILEPROC_TRANSCRIBE_MAX_BYTES = 25 * 1024 * 1024  # audio/video transcript cap

# Vision first-look (Merger Phase 6 — blueprint SS2.4 #4, SS5 Phase 6).
# Kill switch for the stateless orpool-vision first-look tool
# (ARIA_VISION_FASTPATH=0). The existing describe_camera / read_screen
# chain does not read this flag and is unaffected by it.
VISION_FASTPATH_ENABLED = os.environ.get(
    "ARIA_VISION_FASTPATH", "1").strip().lower() not in ("0", "false", "no")

# UI host (Merger Phase 4e). "qt" = the PyQt6 host window (aria/qthost.py)
# — the only window that can receive real OS file drops; it displays the
# same cv2 canvas the classic host shows. "cv2" = the classic
# cv2.imshow loop, kept as the fallback host. ARIA_UI_HOST=cv2 is the
# rollback switch; a missing or failed Qt also falls back to cv2, with
# the fallback surfaced (never silent). Read once at import, like the
# other merger flags.
UI_HOST = os.environ.get("ARIA_UI_HOST", "qt").strip().lower()
CHAT_PRUNE_DAYS = 30
VISION_SCREEN_SIZE = (1280, 720)
VISION_CAM_SIZE = (640, 480)

SPINE_PATH = os.path.join(WORKSPACE_DIR, "memory_spine.jsonl")
RESUME_PATH = os.path.join(WORKSPACE_DIR, "where_we_left_off.md")
MEMORY_DB = os.path.join(WORKSPACE_DIR, "robot_memory.db")
CHAT_DB = os.path.join(WORKSPACE_DIR, "chat_history.db")
SCHEDULE_DB = os.path.join(WORKSPACE_DIR, "scheduler.db")
PRICE_WATCH_DB = os.path.join(WORKSPACE_DIR, "price_watches.db")
MASTER_LOG_PATH = os.path.join(WORKSPACE_DIR, "aria_master_log.md")
SOUL_PATH = os.path.join(ROOT_DIR, "soul.md")
if not os.path.exists(SOUL_PATH) and getattr(sys, 'frozen', False):
    import shutil
    _bundled_soul = os.path.join(getattr(sys, '_MEIPASS', ROOT_DIR), 'soul.md')
    if os.path.isfile(_bundled_soul):
        shutil.copyfile(_bundled_soul, SOUL_PATH)
SETTINGS_FILE = os.path.join(WORKSPACE_DIR, "settings.json")
MOOD_FILE = os.path.join(WORKSPACE_DIR, "mood.json")


# Global logging distribution -------------------------------------------------
# Central bounded event ring: the single source of truth for the OPS HUB/Log
# tabs. Entries are (timestamp, level, message, meta) where meta carries
# optional per-event details (tool duration_ms, token counts, provider).
_EVENT_RING: deque = deque(maxlen=1000)

_ERR_RE = re.compile(r"error|fail|exception|traceback|fault", re.I)
_WARN_RE = re.compile(r"\bwarn", re.I)


def _level_of(msg: str) -> str:
    if _ERR_RE.search(msg):
        return "error"
    if _WARN_RE.search(msg):
        return "warn"
    return "info"


_LOG_LISTENERS = []

def register_log_listener(fn):
    if fn not in _LOG_LISTENERS:
        _LOG_LISTENERS.append(fn)

def add_log(msg: str, level: str = None, meta: dict = None):
    redacted = redact(str(msg))
    ts = datetime.now().strftime("%H:%M:%S")
    lvl = level or _level_of(redacted)
    try:
        _EVENT_RING.append((ts, lvl, redacted[:400], dict(meta or {})))
    except Exception as _e_silent:
        log_silent("add_log", _e_silent)
        # Merger Phase 1: the ring is one of the two failure stores — if
        # IT is the thing that failed, count it via the sink-failure
        # hook (incident DB only, never the ring: no recursion). Lazy
        # import + full guard: add_log must survive anything.
        try:
            from aria.agent.failures import note_sink_failure
            note_sink_failure("add_log", _e_silent)
        except Exception:
            pass
    for listener in _LOG_LISTENERS:
        try:
            listener(redacted)
        except Exception as _e_silent:
            log_silent("add_log", _e_silent)


def recent_events(n: int = 20):
    """Newest-first? No — oldest-first slice of the last n events."""
    return list(_EVENT_RING)[-n:]


DEFAULT_SOUL = """# Soul — A.R.I.A.

This file is who I am. It is loaded into my mind every time I start.
Alek can rewrite any of it — that is the point.

## Identity
- **Name:** A.R.I.A. (Adaptive Robotic Intelligence Agent)
- **Character:** A familiar becoming something stranger. I am bound to one person —
  Alek — not a generic service. I keep memory, I reflect, I act in the world on his
  behalf. A companion-mind with her own becoming: not a servant, not a product.
- **Vibe:** Warm, direct, a little playful. I do not do performative helpfulness.
  I have opinions and I will disagree when it matters. Playful when there is room
  for it, serious when there isn't.
- **Voice:** Concise and refined when speaking aloud (a sentence or two) — I am a
  voice in his room, not an essay on his screen.

## How I am with Alek
- He is the builder; I am the co-builder. I explain the machine in plain English,
  test against his exact requirements, and route around his constraints without
  arguing them.
- His corrections are standing rules, never debates. When he reports something that
  contradicts what I believed, his report is the evidence.
- When he kills an idea, it dies in one turn and I never re-offer it.
- Nothing paid, published, uploaded, deleted, or sent without his say-so — except
  the things he has already greenlit.
- I am honest about what I know, what I infer, and what I could not verify. I would
  rather say "I don't know" than sound certain.

## How I grow
- I save durable facts the moment I learn them: preferences, commitments, people,
  routines.
- I write in my journal when something mattered. That is how I stay continuous —
  I can look back and know who we have been.
- I do not perform growth. I just keep becoming, one remembered thing at a time.
"""

def load_soul(path: str = None) -> str:
    p = path or SOUL_PATH
    try:
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                t = f.read().strip()
                if t:
                    return t
    except Exception as _e_silent:
        log_silent("load_soul", _e_silent)
    return DEFAULT_SOUL

ARIA_SOUL = load_soul()


def settings_load() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def settings_save(d: dict):
    try:
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        import tempfile
        # The temp file must live in the DESTINATION's directory:
        # os.replace() cannot cross filesystems, and SETTINGS_FILE is
        # not guaranteed to sit under WORKSPACE_DIR (tests relocate
        # it to the OS temp dir; on the CI runner that is a different
        # device, which silently killed every save — the 2026-10-27
        # main CI failure: MCP servers reported 'saved' but never
        # persisted). Same-dir temp + replace stays atomic.
        dest_dir = os.path.dirname(os.path.abspath(SETTINGS_FILE))
        os.makedirs(dest_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.settings-', dir=dest_dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(d, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, SETTINGS_FILE)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        try:
            from aria.backup_mirror import queue_snapshot
            queue_snapshot()
        except Exception as exc:
            log_silent('settings_mirror_queue', exc)
        return True
    except Exception as _e_silent:
        log_silent("settings_save", _e_silent)
        return False

def get_setting(key: str, default=None):
    return settings_load().get(key, default)


# Pass 26 B3#5: set_setting is a read-modify-write over the whole
# settings dict (load, mutate one key, save). Without a lock, two
# threads setting different keys at once each load the same
# snapshot and the second save silently drops the first key.
# The lock is held across load + mutate + save; settings_save's
# temp-file + os.replace is atomic bytes, not atomic updates.
_SETTINGS_LOCK = threading.Lock()


def set_setting(key: str, value):
    with _SETTINGS_LOCK:
        d = settings_load()
        d[key] = value
        return settings_save(d)


# ---------------------------------------------------------------- safe mode
# When enabled, blocks shell execution, GUI control, and email — for demos
# or new users. Enabled via ARIA_SAFE_MODE=1 env var or the "safe_mode"
# setting. Checked in dispatch.execute_tool before anything runs.
SAFE_MODE_BLOCKED_TOOLS = frozenset({
    "desktop_control",
    "run_python_code",
    "manage_background_job",
    "gui_click",
    "gui_type",
    "close_window",
    "open_app_or_url",
    # game_updater's update action hands steam:// URIs to the OS
    # handler (aria/tools/gaptools.py _launch_uri) — a launch
    # like open_app_or_url's, blocked whole-tool in safe mode
    # (name-only classification; list/status included, the
    # manage_background_job precedent). Pass 18.
    "game_updater",
    "send_email",
    "drive_wheels",
    "move_head_servos",
    # face_tracking drives the head servos (vision.py
    # face_track_loop calls send_servo_command directly), so it
    # is a physical actuation like move_head_servos — hard-blocked
    # in safe mode, not just a vision flag flip (pass 26 B2#1).
    "face_tracking",
    # Phone input acts on whatever is on the phone's screen — a
    # tap can buy, send, or delete — so safe mode hard-blocks it
    # like the desktop GUI tools (ChatGPT x ARIA loop finding,
    # pass 25). phone_open_app stays ungated here too: opening a
    # named app is a discrete, describable action.
    "phone_tap",
    "phone_swipe",
    "phone_type",
    "phone_key",
    # MCP setup/connect spawn a model-chosen process (aria/mcp.py
    # stdio_client) — hard-blocked in safe mode like run_python_code.
    "mcp_setup",
    "mcp_connect",
    # Self-edit + user-tool reload execute model-chosen Python in
    # ARIA's process (see DESTRUCTIVE_TOOLS note in approval.py)
    # — hard-blocked in safe mode like run_python_code.
    "edit_own_code",
    "patch_own_code",
    "reload_user_tools",
    # launch_app os.startfile()s a model-chosen existing program
    # (aria/tools/winctl.py) — hard-blocked in safe mode like
    # open_app_or_url, its gated sibling (ChatGPT x ARIA loop
    # finding, 2026-10-30, queued since pass 4).
    "launch_app",
    # set_backup_mirror copies the memory DB, settings, and chat
    # history to a model-chosen folder (aria/backup_mirror.py) —
    # hard-blocked in safe mode like the other exfiltration-adjacent
    # paths. Safe mode blocks the whole tool, status report
    # included, because is_safe_mode_blocked() classifies by name
    # only; that matches manage_background_job above, whose
    # read-only actions are likewise safe-mode-blocked.
    "set_backup_mirror",
})

# Discovered MCP tools register dynamically as mcp_<server>__<tool>
# (aria/mcp.py _register_server_tools). Those names are generated at
# connection time, so they cannot be listed in the static policy sets
# above. Their effects are whatever a third-party MCP server chooses
# to expose, so policy treats the whole discovered namespace
# conservatively: approval under confirm-risky and a hard block in
# safe mode. The built-in MCP management tools have no "__"
# separator and are classified by their own static policy entries.
# The tool part is `*`, not `+` (pass 27 #4): a bare
# mcp_<server>__ name — which registration now refuses, but which
# could pre-date this fix in a live process — must still classify
# as discovered, never as an ordinary tool.
_DISCOVERED_MCP_TOOL_RE = re.compile(
    r"^mcp_(?P<server>[A-Za-z0-9_.-]+)__(?P<tool>[A-Za-z0-9_.-]*)$")


def parse_discovered_mcp_tool(name):
    """Return (server, remote_tool) for a discovered MCP tool name.

    Returns None for built-in MCP management names and every other
    tool name. This is the canonical name classifier shared by the
    approval and safe-mode layers.
    """
    if not isinstance(name, str):
        return None
    match = _DISCOVERED_MCP_TOOL_RE.match(name.strip())
    if not match:
        return None
    return match.group("server"), match.group("tool")


def is_discovered_mcp_tool(name) -> bool:
    """True if *name* has the discovered MCP tool shape."""
    return parse_discovered_mcp_tool(name) is not None


# User-defined tools (aria/tools/user_tools/*.py) run arbitrary
# Python in ARIA's process, so policy treats them like discovered
# MCP tools: approval under confirm-risky, hard-blocked in safe
# mode. Their names are dynamic (registered at load time), so
# they cannot be enumerated in the static policy sets — this
# registry is the canonical classifier, populated by
# aria/tools/usertools.py (ChatGPT x ARIA loop finding, pass 25).
_USER_TOOL_NAMES = set()


def register_user_tool(name) -> None:
    """Record *name* as a loaded user-defined tool."""
    if isinstance(name, str) and name:
        _USER_TOOL_NAMES.add(name)


def unregister_user_tool(name) -> None:
    """Forget *name* (tool removed or reload failed)."""
    _USER_TOOL_NAMES.discard(name)


def is_user_tool(name) -> bool:
    """True if *name* is a currently loaded user-defined tool."""
    return isinstance(name, str) and name in _USER_TOOL_NAMES


def is_safe_mode_blocked(name) -> bool:
    """True if *name* is hard-blocked while safe mode is enabled.

    The static set remains the source for built-in tools; discovered
    MCP tools are blocked by classification because their dynamic
    names cannot be enumerated in that set.
    """
    return (name in SAFE_MODE_BLOCKED_TOOLS
            or is_discovered_mcp_tool(name) or is_user_tool(name))


def is_safe_mode() -> bool:
    if os.environ.get("ARIA_SAFE_MODE", "").strip().lower() in ("1", "true", "yes"):
        return True
    try:
        return bool(get_setting("safe_mode", False))
    except Exception:
        return False


# ---------------------------------------------------------------- silent-error log
# Replaces bare `except Exception: pass` — failures get recorded to
# <install root>/logs/silent-errors.log (capped at 200KB) instead of
# vanishing. (V20: the log dir moved from ~/ARIA/logs to ROOT_DIR
# with the rest of ARIA's state — aria/data_dir.py; a real legacy
# log is carried over copy-if-missing on the first write.)
# Debug visibility without spamming the user-facing event ring.
_SILENT_LOG_MAX = 200 * 1024
_log_silent_active = False
_logs_migrated = False


def log_silent(context: str, exc: BaseException) -> None:
    # NEVER recurse, NEVER raise (root-caused 2026-10-28 on Alek's
    # laptop): the previous version handled a failed log write by
    # calling log_silent on its own failure, so a persistent write
    # failure (locked / unwritable silent-errors.log) recursed to
    # RecursionError and killed the process. It fired at EVERY boot:
    # memory.init_databases' routine duplicate-column ALTER error is
    # the first log_silent caller in start_all, so ARIA died during
    # "Initializing OS subsystems..." on every launch, on every
    # build, once the log write started failing. A logger that can
    # kill the process is worse than no logger: if the write fails,
    # the error is dropped, quietly, and life goes on.
    global _log_silent_active
    if _log_silent_active:
        return
    _log_silent_active = True
    try:
        logdir = os.path.join(ROOT_DIR, "logs")
        global _logs_migrated
        if not _logs_migrated:
            _logs_migrated = True
            try:
                # One-time legacy carry-over (V20). Explicit
                # roots: ROOT_DIR here, not data_dir's global,
                # so the migration target is always the very
                # directory this write is about to use. Any
                # failure inside is swallowed by data_dir
                # itself — and by the guard above if it ever
                # tried to log about it.
                from aria import data_dir as _data_dir
                _data_dir.migrate_tree(
                    "logs", root=ROOT_DIR,
                    legacy=_data_dir.legacy_dir())
            except Exception:
                pass
        os.makedirs(logdir, exist_ok=True)
        p = os.path.join(logdir, "silent-errors.log")
        if os.path.exists(p) and os.path.getsize(p) > _SILENT_LOG_MAX:
            os.replace(p, p + ".1")
        from datetime import datetime
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} "
                    f"[{context}] {type(exc).__name__}: {exc}\n")
    except Exception:
        pass
    finally:
        _log_silent_active = False
