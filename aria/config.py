"""
ARIA Configuration and environment management.
Centralized paths, keys, pool rotation, secret redaction, and global parameters.
"""

import os
import json
import secrets
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
            json.dump({"GEMINI_API_KEY": "INSERT",
                       "GEMINI_API_KEYS": [],
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


def _gemini_key_pool():
    _pool = []
    _env = os.environ.get("GEMINI_API_KEYS", "").strip()
    if _env:
        _pool = [k.strip() for k in _env.split(",") if k.strip() and k.strip() != "INSERT"]
    elif isinstance(_KEYS.get("GEMINI_API_KEYS"), list):
        _pool = [k for k in _KEYS["GEMINI_API_KEYS"] if k and k != "INSERT"]
    g_key, _ = key_get("GEMINI_API_KEY")
    if not _pool and g_key and g_key != "INSERT":
        _pool = [g_key]
    _seen, _out = set(), []
    for _k in _pool:
        if _k not in _seen:
            _seen.add(_k)
            _out.append(_k)
    return _out


GEMINI_API_KEY, _GEMINI_SOURCE = key_get("GEMINI_API_KEY")
GEMINI_KEY_POOL = _gemini_key_pool()
_GEMINI_KEY_INDEX = 0
_KEY_QUARANTINE_UNTIL = {}
_KEY_QUARANTINE_CODE = {}
KEY_QUARANTINE_DURATION_S = 3600


def key_mask(key):
    if not key or key == "INSERT":
        return "none"
    return key[:6] + "..." + key[-4:] if len(key) >= 12 else key[:3] + "..."


def get_gemini_key():
    global _GEMINI_KEY_INDEX
    if not GEMINI_KEY_POOL:
        return GEMINI_API_KEY
    now = datetime.now().timestamp()
    n = len(GEMINI_KEY_POOL)
    for offset in range(n):
        idx = (_GEMINI_KEY_INDEX + offset) % n
        k = GEMINI_KEY_POOL[idx]
        if _KEY_QUARANTINE_UNTIL.get(k, 0) <= now:
            _GEMINI_KEY_INDEX = (idx + 1) % n
            return k
    earliest = min(_KEY_QUARANTINE_UNTIL.values())
    wait_m = max(1, int((earliest - now) / 60))
    print(f"[ARIA] All {n} Gemini keys are rate-limited or rejected. Earliest unblocks in ~{wait_m}m.")
    k = GEMINI_KEY_POOL[_GEMINI_KEY_INDEX]
    _GEMINI_KEY_INDEX = (_GEMINI_KEY_INDEX + 1) % n
    return k


def quarantine_key(key, duration_s=KEY_QUARANTINE_DURATION_S, code=429):
    if not key:
        return
    _KEY_QUARANTINE_UNTIL[key] = datetime.now().timestamp() + duration_s
    _KEY_QUARANTINE_CODE[key] = code
    if key in GEMINI_KEY_POOL:
        return GEMINI_KEY_POOL.index(key)
    return None


def get_quarantined_keys_info():
    now = datetime.now().timestamp()
    active = {}
    for i, k in enumerate(GEMINI_KEY_POOL):
        until = _KEY_QUARANTINE_UNTIL.get(k, 0)
        if until > now:
            mins = int((until - now) / 60) + 1
            code = _KEY_QUARANTINE_CODE.get(k, 429)
            active[i] = (mins, code, key_mask(k))
    return active


def _secret_values():
    vals = list(GEMINI_KEY_POOL or [])
    for name in ("GEMINI_API_KEY", "GITHUB_TOKEN", "GMAIL_APP_PASSWORD",
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
        except Exception:
            pass
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
MODEL_NAME = "gemini-2.5-flash"
EMBED_MODEL = "models/gemini-embedding-001"
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
CHAT_PRUNE_DAYS = 30
VISION_SCREEN_SIZE = (800, 450)
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


# Global logging distribution
_LOG_LISTENERS = []

def register_log_listener(fn):
    if fn not in _LOG_LISTENERS:
        _LOG_LISTENERS.append(fn)

def add_log(msg: str):
    redacted = redact(str(msg))
    for listener in _LOG_LISTENERS:
        try:
            listener(redacted)
        except Exception:
            pass


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
    except Exception:
        pass
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
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass

def get_setting(key: str, default=None):
    return settings_load().get(key, default)

def set_setting(key: str, value):
    d = settings_load()
    d[key] = value
    settings_save(d)
