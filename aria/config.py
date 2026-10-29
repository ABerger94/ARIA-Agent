"""
ARIA Configuration and environment management.
Centralized paths, keys, pool rotation, secret redaction, and global parameters.
"""

import os
import json
import re
import secrets
from collections import deque
from datetime import datetime

# Root paths
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    """Persist _KEYS back to aria_keys.json (used when keys change at runtime)."""
    if _KEYS_BROKEN:
        print(f"[ARIA] Keys: NOT saving - {KEYS_FILE} has broken JSON. Fix it first.", flush=True)
        return False
    try:
        with open(KEYS_FILE, "w", encoding="utf-8") as _f:
            json.dump(_KEYS, _f, indent=2)
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
# Role-based model routing (Ollama Cloud tags). Empty string disables the
# role (falls back to OLLAMA_CLOUD_MODEL) — retirement-proof overrides.
OLLAMA_VISION_MODEL, _ = key_get("OLLAMA_VISION_MODEL", "gemma4:31b-cloud")
OLLAMA_CODE_MODEL, _ = key_get("OLLAMA_CODE_MODEL", "qwen3-coder:480b-cloud")
OLLAMA_HOST, _ = key_get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL, _ = key_get("OLLAMA_MODEL", "qwen2.5:7b")

# Chain: gemini -> ollama_cloud -> groq -> openrouter -> mistral (Gemini
# promoted to primary 2026-10-27, Alek's call — the old fallbacks were
# the weak path). Reorder any time via the PROVIDER_CHAIN override,
# e.g. PROVIDER_CHAIN="groq,ollama_cloud,mistral,openrouter".
_PROVIDER_CHAIN_RAW, _ = key_get("PROVIDER_CHAIN", "")
if _PROVIDER_CHAIN_RAW and _PROVIDER_CHAIN_RAW != "INSERT":
    PROVIDER_CHAIN = [p.strip().lower() for p in _PROVIDER_CHAIN_RAW.split(",") if p.strip()]
else:
    PROVIDER_CHAIN = ["gemini", "ollama_cloud", "groq", "openrouter", "mistral"]

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
    for name in ("GITHUB_TOKEN", "GMAIL_APP_PASSWORD",
                 "BRIDGE_TOKEN", "bridge_token"):
        v = os.environ.get(name) or _KEYS.get(name) or ""
        if v:
            vals.append(v)
    return sorted({v for v in vals if len(v) > 6 and v != "INSERT"},
                  key=len, reverse=True)


def redact(text):
    if not text or not isinstance(text, str):
        return text
    for v in _secret_values():
        text = text.replace(v, "[redacted]")
    return text


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
        return True
    except Exception as _e_silent:
        log_silent("settings_save", _e_silent)
        return False

def get_setting(key: str, default=None):
    return settings_load().get(key, default)

def set_setting(key: str, value):
    d = settings_load()
    d[key] = value
    return settings_save(d)


# ---------------------------------------------------------------- safe mode
# When enabled, blocks shell execution, GUI control, and email — for demos
# or new users. Enabled via ARIA_SAFE_MODE=1 env var or the "safe_mode"
# setting. Checked in dispatch.execute_tool before anything runs.
SAFE_MODE_BLOCKED_TOOLS = frozenset({
    "run_python_code",
    "manage_background_job",
    "gui_click",
    "gui_type",
    "close_window",
    "open_app_or_url",
    "send_email",
    "drive_wheels",
    "move_head_servos",
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
})


def is_safe_mode() -> bool:
    if os.environ.get("ARIA_SAFE_MODE", "").strip().lower() in ("1", "true", "yes"):
        return True
    try:
        return bool(get_setting("safe_mode", False))
    except Exception:
        return False


# ---------------------------------------------------------------- silent-error log
# Replaces bare `except Exception: pass` — failures get recorded to
# ~/ARIA/logs/silent-errors.log (capped at 200KB) instead of vanishing.
# Debug visibility without spamming the user-facing event ring.
_SILENT_LOG_MAX = 200 * 1024
_log_silent_active = False


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
        logdir = os.path.join(os.path.expanduser("~"), "ARIA", "logs")
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
