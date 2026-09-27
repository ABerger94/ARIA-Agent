"""
A.R.I.A. — Autonomous Robotic Intelligence Agent
Powered by Gemini 3.8 Flash.

 v9.26: streaming-speech queue fix — the pre-tool speech stop now uses
 interrupt_speech() (the old inline drain skipped task_done, leaking the
 unfinished-tasks counter and leaving the stop event set, which silently
 muted streaming on every tool-call turn); the stop event is cleared
 before each fresh streaming call.

 v9.25: streaming sentence TTS + prompt context diet — real-time SSE token
 streaming from Gemini detects sentence boundaries on the fly and enqueues
 speech instantly (cutting voice latency to <800ms); unique memory key
 indexing and deduplication with ON CONFLICT DO UPDATE; internal system
 keys (_%) isolated from prompt context; dynamic semantic memory retrieval
 injects core user identity and top-k semantically relevant memories.

 v9.24: self-edit auto-restart — boot-time sha256 of the running script
 and soul.md; when a turn completes and either changed, the new script is
 compile-checked, she announces the restart, and a daemon timer relaunches
 the process once speech drains (deferred while a confirmation or a
 suspended turn is pending; a new turn always cancels a pending restart).

 v9.23: barge-in — any new typed, mic, PTT, wake-word, or phone-bridge
 message stops her current speech immediately (interrupt_speech now runs
 on every input path, not just stop-words); empty transcripts don't interrupt.

 v9.22: every face gets a mouth — yellow waveform ripple on
 thinking/working/listening, green on coding (idle/speaking keep pink);
 the working radar arc moved down to clear the mouth.

 v9.21: "open the video downloader" now works — starts the local Node server
 via start-windows.bat (port 3003) when localhost:3003 isn't responding,
 then opens the page in the browser.

 v9.20: quote file paths in open_app_or_url — paths with spaces
 (e.g. "The Boy and the Heron") no longer get truncated at the first space.

 v9.19: version numbers removed from all user-visible text and code — the
 changelog keeps its versions; everything else (HUD, subtitles, console,
 spoken greeting, comments, docstrings) is clean.

New in v6.1 (everything from v6.0 kept):
 0. VOICE DIAGNOSTICS — startup now prints exactly which voice step fails.
 v6.2: full tracebacks on voice failure, edge-tts origin check, Windows natural-voice fallback via pyttsx3.
 v6.3: HUD/window/console renamed to Autonomous Robotic Intelligence Agent.
 v6.4: action stream word-wraps (was hard-cut at 30 chars).
 v7.0: fetch_url, clipboard, window control, media keys, Scryfall MTG lookup,
 price watcher (hourly), face tracking, speech interrupt, idle memory consolidation.
 v7.1: idle face comes alive — saccading pupils, blinks, six mouth expressions;
 pupils lock on when listening.
 v7.2: idle mouth is a soft waveform ripple (expression morphs removed);
 speaking waveform untouched — same as since v5.
 v7.3: startup speech no longer waits on the Edge voice check — the greeting
 plays immediately on the fallback voice and Edge takes over when ready;
 Edge probe capped at 20s so a stalled check can never wedge speech.
 v7.4: fixed a startup race where the speech thread could die with
 NameError on _SPEECH_STOP (it was defined after the threads that use
 it), muting all speech; the event now lives before any thread starts,
 and a supervisor restarts the speech thread loudly if it ever exits.
 v7.5: fixed a crash on the first typed directive — STOP_WORDS (and
 LAST_ACTIVITY / FACE_TRACKING) were defined after the __main__ loop that
 calls handle_action, so the names did not exist yet (NameError). They now
 live with the other module constants, before any function uses them.
 v7.6: moved the __main__ block to end-of-file — ~500 lines of tool and
 loop definitions sat after it and never executed, so every v7.0 tool
 (fetch_url, clipboard, window mgmt, media keys, mtg_card, price watcher,
 face tracking, idle consolidation) failed and the background loops never
 started; also defined the missing SERVO_POS dict the face tracker reads.
 v7.7: GitHub key startup diagnostics — prints exactly why the HUD says
 NO KEY (empty token vs untouched placeholder vs env var) and verifies a
 present token against the GitHub API in the background (HUD shows
 CHECKING, then ARMED or BAD KEY), so a pasted-but-rejected token can no
 longer masquerade as a missing one.
 v7.8: keys moved to aria_keys.json — the script creates it next to
 itself on first run; paste both keys there ONCE and they survive every
 future robot.py update (env vars still win if set). Startup now reports
 where each key came from (env var / keys file / script default).
 v7.9: fixed NameError in the GitHub startup check — v7.8 removed the
 src_name variable but left three prints referencing it, crashing the
 check thread whenever it ran. All three now use _GITHUB_SOURCE.
 Also: per-utterance Edge TTS failures now print the reason on the
 console (they previously only reached the in-app log), so the next
 fallback-voice moment names its cause instead of failing silently.
 v7.10: the greeting waits for the Edge voice (up to ~25s, in a
 background thread so startup stays responsive) instead of speaking
 immediately in the pyttsx3 fallback while Edge is still warming up.
 The worker's not-ready fallback path also prints a console line now.
 v7.11: girly face — pink waveform mouth (idle + speaking), eyelash
 flicks on the upper-outer corner of each eye, pink iris rings, soft
 pink eye glow, dark plum eyeliner outline, slightly larger rounder
 eyes. Listening/speaking eyes keep the treatment too.
 v8.0: Spotify voice control, morning briefing (schedule + weather),
 quick timers, screenshots, screen reading via vision, webcam photos,
 file finder, volume control, MTG deck advice, calendar-aware greeting,
 toggleable break nudges, phone-bridge token login + hold-to-talk
 voice chat, startup self-check, parallel independent tool calls.
 v8.1: chat model -> gemini-3.8-flash (verified live); embeddings ->
 gemini-embedding-001 (text-embedding-004 retired by Google);
 semantic search skips vectors stored under the retired model.
 v8.2: HTTP 402 (depleted prepay credits) quarantines the key 1h like other
 hard rejects instead of killing the request; key status shows the HTTP code.
 v8.3: transient Google 5xx (500/502/503/504) cools the key 30s and rotates instead
 of surfacing a raw 503; Spotify tool learned the "open" action.
 v8.4: commands reference - "show commands" HUD overlay (H key, [ ] pages) plus a
 /commands page on the phone bridge; both render from one COMMAND_GUIDE table.
 v8.5: yes/no confirmations listen for the first word ("Yes.", "yes please" work now);
 anything unclear fails closed to cancel. Stop-words got the same punctuation fix.
 v8.6: HUD no longer draws non-ASCII (bullets/dashes became "???") - subsystems get
 drawn status dots, dynamic HUD text is sanitized to ASCII first.
 v8.7: "open" commands stop parroting the previous target - confirmation turns are
 recorded in conversation memory, a stale-target guard re-checks the latest message,
 and the target must come from the user's most recent message.
 v9.0: soul + memory habits - soul.md (an editable persona file) loads into the
 system prompt at startup; new forget_memory tool; save_memory is now an in-the-
 moment habit rather than only idle consolidation; new journal_write tool with
 aria_journal/ dated reflections; core memory seeded on first run.
 v9.1: startup greeting is written fresh by the model at every boot - soul-aware,
 schedule-aware, never repeats the previous greeting; template fallback when offline.
 v9.2: play_uri now starts playback - opens the spotify: URI, waits for the context
 to load, then taps the play media key (was: opened the playlist but stayed silent).
 v9.3: 'let's play some magic' shortcut opens the Convoke lobby instantly, no
 confirmation; voice notes (take_note/read_notes - 'note to self', 'what were my
 notes Tuesday'); DJ mode (dj - 'shuffle my liked songs', 'play something chill',
 named playlists remembered via save_memory category 'playlist').
 v9.4: confirmations removed - risky tools execute immediately when asked (Alek's
 call); the silent stale-target guard and the executed-action history are kept.
 v9.5: tactical chat log is scrollable (J = older, K = newer, 60-entry scrollback);
 agent loop cap raised 6 -> 20 turns per request.
 v9.6: Gmail sending via app password - new send_email tool (to/subject/body) and
 gmail_setup to save the Gmail address + app password into aria_keys.json.
 v9.7: history URLs fixed - 'open that link from earlier' now resolves the URL from
 the user's own earlier messages and opens it; the stale-target guard only nudges
 when the repeat looks accidental, and passes through on explicit re-reference.
 v9.8: progressive tool loading - only the core toolkit's schemas go to the model
 on every call; specialist tools (spotify, gmail, scheduler, github, vision,
 windows, mtg, memory+, admin) unlock via load_toolkit. Plus duplicate-call
 blocking (same tool + same args twice in one turn returns the earlier result)
 and argument repair (missing required params get a retry nudge, not a crash).
 v9.9: secret redaction (credentials scrubbed from HUD stream, chat DB, markdown
 log, and history lines); Edge voice probe retries 3x before falling back;
 heartbeat decline learning (dismissed proactive nudges stay quiet 1/2/4/7 days);
 bounded workflow skills via run_skill (deep_research, system_check, file_sweep)
 with hard per-skill tool-call budgets; blocking mic/typed input moved off the
 HUD thread so the face stays animated; Gemini network drops retry with backoff
 and Gmail send retries once; animated thinking face, new animated working face
 for tool runs, pulsing listening face.
 v9.10: memory spine — one append-only event log (memory_spine.jsonl) for
 chat, memory saves/forgets, journal entries, tool calls, and restarts;
 nothing is ever pruned; atexit writes a where_we_left_off.md resume card
 (last 30 chat events, this session's saved keys, pending tasks); boot
 injects the spine tail as UNBROKEN THREAD into the system prompt;
 chat_history DB pruning retired; journal entries are also saved into
 searchable semantic memory (category 'journal'); idle consolidation
 reads the spine tail too (and its chat_history query now uses the real
 'sender' column — it previously selected a nonexistent 'speaker').
 v9.11: push-to-talk — hold SPACE while talking, release when done (Windows:
 GetAsyncKeyState polls the physical key in a daemon thread; the old one-shot
 mic stays as the non-Windows fallback); 0.25s chunks up to a 60s cap, taps
 under 0.5s ignored; post-transcription handling extracted into
 _handle_transcript shared by both mic paths; PTT turns logged to the spine.
 v9.12: her face on the phone bridge — draw_hud() crops just the face
 (FACE_CROP) and stashes a JPEG at ~12fps; the bridge serves it as an MJPEG
 stream at /face.mjpg and a still at /face.jpg, both under the same bridge-
 token auth as /api/* (the <img> tag carries ?token= since headers can't be
 set on an image); the phone page shows her face up top. No new
 dependencies; encoding never blocks or breaks the HUD.
 v9.13: reliable morning brief — "morning brief" recurring tasks are stored
 as kind="brief" and fired deterministically by _fire_brief, which speaks
 tool_briefing() directly (or queues it when busy) instead of asking the
 model, so the brief can never echo back as a bare "morning brief";
 legacy interval-kind brief tasks are caught at fire time and upgraded;
 tool_briefing now pulls temp/high/low from wttr.in j1 (falling back to the
 old format string) and adds a tomorrow preview.
 v9.14: local speech recognition — every mic path (one-shot, wake-word
 listener, push-to-talk) now transcribes through _transcribe(), which
 prefers faster-whisper (local "base" int8 model on CPU, lazy-loaded
 into robot_workspace/models) and falls back to Google cloud recognition
 on any failure; faster-whisper is optional, never a hard dependency.
 v9.15: the agent loop's 20-turn cap is gone — replaced by a 10-minute
 per-request time budget (AGENT_LOOP_TIME_BUDGET_S). When the budget
 runs out between model/tool rounds the turn suspends in memory and
 she asks "Should I keep going?"; "continue" (or a bare "yes" to
 that question) resumes the exact same reasoning chain via
 _resume_from, while any fresh directive abandons the suspended turn.
 A pending risky-tool confirmation still takes precedence over the
 continue-yes. Suspended state is in-memory only (lost on restart).
 The duplicate-call guard and confirmation flow are unchanged; the
 budget never interrupts a running tool call.
 v9.16: hallucination guard for local STT — Whisper was inventing
 words on silence/background hum. _transcribe() now passes
 vad_filter=True (bundled Silero VAD, no new dependency) and
 condition_on_previous_text=False (stops the model riffing on its own
 prior output), and drops any segment whose no_speech_prob exceeds
 _FW_NO_SPEECH_CUTOFF (0.6). If nothing survives filtering, the empty
 result falls through to the Google fallback exactly as before —
 silence becomes "didn't catch that", never invented words.
 v9.17: back to Google cloud transcription — the local
 faster-whisper "base" model was mishearing on the laptop.
 _USE_LOCAL_STT = False is now the default: _transcribe() calls
 recognize_google directly and _get_fw_model() is never invoked
 (no imports, no model downloads, zero faster-whisper overhead).
 The whole local path — VAD filter, no-previous-text conditioning,
 no-speech filtering — stays in the file, dormant; set
 _USE_LOCAL_STT = True to re-enable it.
 v9.18: subtitle sanitizer — the HUD's vocal-subtitle line rendered
 '???' for Unicode punctuation (curly apostrophes, quotes, dashes)
 because SUBTITLE_TEXT was drawn raw; it is now passed through
 _hud() at assignment in _speech_worker, consistent with the
 action stream and chat log.
 HTTPS phone bridge: self-signed cert for the current LAN IP so iOS grants mic access (first iPhone visit taps through a cert warning); falls back to
 HTTPS via a bundled self-signed cert (no extra packages).
 1. EDGE TTS VOICE — en-US-AriaNeural via the free edge-tts package. Sounds
    dramatically more human than pyttsx3. Falls back to pyttsx3 automatically
    if edge-tts isn't installed or the network drops.  pip install edge-tts pygame
 2. SEMANTIC MEMORY — memories are embedded (Gemini gemini-embedding-001, free)
    and searched by meaning, not just keywords. Keyword LIKE is the fallback.
 3. NATURAL-LANGUAGE SCHEDULER — "remind me in 20 minutes", "check that site
    every hour". One-shot reminders (spoken) + recurring agent tasks, persisted
    in SQLite so they survive restarts. Four new tools: set_reminder,
    set_recurring_task, list_scheduled_tasks, cancel_scheduled_task.
 4. CONFIRMATION LAYER — risky tools (run code, click, type, open apps/URLs,
    GitHub pushes) now ask "say yes to confirm" before firing. Say "no" to
    cancel. Scheduled tasks you created are pre-authorized.
 5. SCREEN DIFFING — screenshots are hashed; if the screen hasn't changed since
    the last look, no image is uploaded (quota saver).
 6. PHONE BRIDGE — tiny web UI on your LAN (http://<laptop-ip>:8777). Talk to
    ARIA from your iPhone anywhere in the house. LAN-only.
 7. AUTOSTART — see aria_autostart.txt (rename to .bat): installs a watchdog
    so ARIA launches with Windows and restarts itself on crash.

NOT in v6.0: key rotation (you're good with one key), and the physical head
itself — that needs parts. The servo software bridge is ready and waiting.

Setup: pip install opencv-python numpy pyttsx3 SpeechRecognition psutil pillow
       pip install duckduckgo-search pyautogui pyserial edge-tts pygame   (optional)
Keys live in aria_keys.json next to this script (auto-created on first run).
"""

import atexit
import cv2
import numpy as np
import pyttsx3
import time
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import urllib.request
import urllib.error
import urllib.parse
import speech_recognition as sr
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import psutil
from PIL import ImageGrab
import textwrap
import sqlite3
import threading
import queue
import random
import re
import math

# =====================================================================
# 1. CONFIGURATION
# =====================================================================
# Keys live in aria_keys.json next to this script (created on first run).
# Enter them ONCE there - they survive every robot.py update.
# Precedence: env vars > aria_keys.json > inline fallback.
KEYS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aria_keys.json")

_KEYS_BROKEN = False


def _load_keys():
    global _KEYS_BROKEN
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE) as _f:
                _d = json.load(_f)
            return _d if isinstance(_d, dict) else {}
        except Exception as _e:
            _KEYS_BROKEN = True
            print(f"[ARIA] Keys: {KEYS_FILE} has broken JSON and was NOT loaded: {_e}", flush=True)
            print("[ARIA] Keys: usually a missing or extra comma. Fix it, then restart. "
                  "The file was NOT overwritten.", flush=True)
            return {}
    try:
        with open(KEYS_FILE, "w") as _f:
            json.dump({"GEMINI_API_KEY": "INSERT",
                       "GEMINI_API_KEYS": [],
                       "GITHUB_TOKEN": "INSERT",
                       "GITHUB_USERNAME": "ABerger94"}, _f, indent=2)
        print(f"[ARIA] Keys: created {KEYS_FILE} - paste your keys there once, then restart.")
    except Exception as _e:
        print(f"[ARIA] Keys: could not create {KEYS_FILE}: {_e}")
    return {}

_KEYS = _load_keys()

def _key(name, fallback="INSERT"):
    if name in os.environ and os.environ[name]:
        return os.environ[name], "env var " + name
    _v = _KEYS.get(name)
    if _v and _v != "INSERT":
        return _v, "keys file"
    return fallback, "script default"

GEMINI_API_KEY, _GEMINI_SOURCE = _key("GEMINI_API_KEY")


def _save_keys():
    """Persist _KEYS back to aria_keys.json (used when keys change at runtime)."""
    if _KEYS_BROKEN:
        print(f"[ARIA] Keys: NOT saving - {KEYS_FILE} has broken JSON. Fix it first.", flush=True)
        return False
    try:
        with open(KEYS_FILE, "w") as _f:
            json.dump(_KEYS, _f, indent=2)
        return True
    except Exception as _e:
        print(f"[ARIA] Keys: could not save {KEYS_FILE}: {_e}")
        return False


# ---- Gemini API key pool ----
# Google's quota is per Cloud PROJECT, not per key: extra keys only multiply
# quota if each comes from a separate project (up to 10 per Google account).
# List them in aria_keys.json as "GEMINI_API_KEYS": ["AIza...", ...],
# or comma-separated in the GEMINI_API_KEYS env var. Falls back to GEMINI_API_KEY.
def _gemini_key_pool():
    _pool = []
    _env = os.environ.get("GEMINI_API_KEYS", "").strip()
    if _env:
        _pool = [k.strip() for k in _env.split(",") if k.strip() and k.strip() != "INSERT"]
    elif isinstance(_KEYS.get("GEMINI_API_KEYS"), list):
        _pool = [k for k in _KEYS["GEMINI_API_KEYS"] if k and k != "INSERT"]
    if not _pool and GEMINI_API_KEY and GEMINI_API_KEY != "INSERT":
        _pool = [GEMINI_API_KEY]
    _seen, _out = set(), []
    for _k in _pool:
        if _k not in _seen:
            _seen.add(_k)
            _out.append(_k)
    return _out


GEMINI_KEY_POOL = _gemini_key_pool()
_KEY_LOCK = threading.Lock()
_KEY_COOLDOWN_UNTIL = {}
_KEY_QUARANTINE_UNTIL = {}
_KEY_QUARANTINE_CODE = {}
_KEY_CURSOR = [0]


def _key_next():
    """Round-robin pick of a usable key. Returns (index, key); skips cooling and quarantined keys."""
    _now = time.time()
    with _KEY_LOCK:
        _n = len(GEMINI_KEY_POOL)
        if _n == 0:
            return None, None
        for _ in range(_n):
            _i = _KEY_CURSOR[0] % _n
            _KEY_CURSOR[0] += 1
            if _KEY_COOLDOWN_UNTIL.get(_i, 0) <= _now and _KEY_QUARANTINE_UNTIL.get(_i, 0) <= _now:
                return _i, GEMINI_KEY_POOL[_i]
        _ok = [j for j in range(_n) if _KEY_QUARANTINE_UNTIL.get(j, 0) <= _now]
        if not _ok:
            return None, None
        _i = min(_ok, key=lambda _j: _KEY_COOLDOWN_UNTIL.get(_j, 0))
        return _i, GEMINI_KEY_POOL[_i]


def _key_cooldown(i, seconds=60):
    with _KEY_LOCK:
        _KEY_COOLDOWN_UNTIL[i] = time.time() + seconds


def _key_mask(k):
    return (k[:4] + "..." + k[-4:]) if len(k) > 12 else "key"


def _key_quarantine(i, key, code):
    """A key Google rejects outright (400/402/403/404) is sidelined for 1h, not retried."""
    with _KEY_LOCK:
        _KEY_QUARANTINE_UNTIL[i] = time.time() + 3600
        _KEY_QUARANTINE_CODE[i] = code
    cause = ("its Cloud project is on Prepay billing with $0 credits - unlink billing "
             "from the project (free tier) or buy prepay credits" if code == 402 else
             "key too new, API not enabled in its Cloud project, or a copy typo")
    add_log(f"Gemini key #{i + 1} ({_key_mask(key)}) rejected by Google (HTTP {code}) - "
            f"quarantined 1h. Usual cause: {cause}.")
# --- Secret redaction ---
# Credentials must never leak into the HUD action stream, the chat DB, the
# markdown master log, or persisted history. _redact() scrubs every known
# secret value; add_log() and log_conversation() apply it to everything.
def _secret_values():
    vals = list(GEMINI_KEY_POOL or [])
    for name in ("GEMINI_API_KEY", "GITHUB_TOKEN", "GMAIL_APP_PASSWORD",
                 "BRIDGE_TOKEN", "bridge_token"):
        v = os.environ.get(name) or _KEYS.get(name) or ""
        if v:
            vals.append(v)
    return sorted({v for v in vals if len(v) > 6 and v != "INSERT"},
                  key=len, reverse=True)


def _redact(text):
    if not text or not isinstance(text, str):
        return text
    for v in _secret_values():
        text = text.replace(v, "[redacted]")
    return text


GITHUB_TOKEN, _GITHUB_SOURCE = _key("GITHUB_TOKEN")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", _KEYS.get("GITHUB_USERNAME") or "ABerger94")
GITHUB_ARMED = bool(GITHUB_TOKEN) and GITHUB_TOKEN != "INSERT"
GITHUB_STATUS = "ARMED" if GITHUB_ARMED else "NO KEY"  # refined by startup check


def _ensure_bridge_token():
    """Phone-bridge login token, persisted in aria_keys.json."""
    if _KEYS_BROKEN:
        # Never overwrite a file we couldn't parse - back it up and use a
        # session-only token instead.
        try:
            _bak = KEYS_FILE + ".bak"
            with open(KEYS_FILE, "rb") as _rf, open(_bak, "wb") as _wf:
                _wf.write(_rf.read())
            print(f"[ARIA] Bridge: broken keys file backed up to {_bak} - not overwriting it.", flush=True)
        except Exception:
            pass
        import secrets
        _tok = secrets.token_urlsafe(16)
        print("[ARIA] Bridge: using a temporary token for this session only.", flush=True)
        _KEYS["bridge_token"] = _tok
        return _tok
    try:
        with open(KEYS_FILE) as _f:
            _d = json.load(_f)
            _d = _d if isinstance(_d, dict) else {}
    except Exception:
        _d = {}
    _tok = _d.get("bridge_token")
    if not _tok:
        import secrets
        _tok = secrets.token_urlsafe(16)
        _d["bridge_token"] = _tok
        try:
            with open(KEYS_FILE, "w") as _f:
                json.dump(_d, _f, indent=2)
        except Exception as _e:
            print(f"[ARIA] Bridge: could not save token: {_e}", flush=True)
    _KEYS["bridge_token"] = _tok
    return _tok


BRIDGE_TOKEN = _ensure_bridge_token()

MODEL_NAME = "gemini-3.8-flash"
EMBED_MODEL = "models/gemini-embedding-001"
EDGE_TTS_VOICE = "en-US-AriaNeural"   # fitting, no?
PHONE_BRIDGE_PORT = 8777

MAX_TOOL_OUTPUT = 2000
HISTORY_TURNS = 10
AGENT_LOOP_TIME_BUDGET_S = 600  # per-request agent-loop time budget (replaces the 20-turn cap)
_SUSPENDED_TURN = None  # {"contents": [...], "user_prompt": ...} while a turn is suspended
_AWAITING_CONTINUE = False  # True while she has asked "Should I keep going?"
CHAT_PRUNE_DAYS = 30
VISION_SCREEN_SIZE = (800, 450)
VISION_CAM_SIZE = (640, 480)

WORKSPACE_DIR = os.path.join(os.path.expanduser("~"), "robot_workspace")
os.makedirs(WORKSPACE_DIR, exist_ok=True)
DB_PATH = os.path.join(WORKSPACE_DIR, "aria_memory.db")
CHAT_LOG_FILE = os.path.join(WORKSPACE_DIR, "chat_history.md")

DB_LOCK = threading.Lock()

# ============================================================
# MEMORY SPINE — one unbroken thread across sessions
# ============================================================
# Every chat turn, memory save/forget, journal entry, tool call, and restart
# appends one JSON line to memory_spine.jsonl. Nothing is ever pruned. At
# shutdown, an atexit handler writes where_we_left_off.md; at boot, the last
# 40 spine events are injected into the system prompt as UNBROKEN THREAD.
# spine_append() never crashes the agent: failures go to add_log.
SPINE_PATH = os.path.join(WORKSPACE_DIR, "memory_spine.jsonl")
RESUME_PATH = os.path.join(WORKSPACE_DIR, "where_we_left_off.md")
_SPINE_BOOT_KEYS = []  # memory_save keys this session (for the resume card)


def spine_append(event_type, payload):
    """Append one redacted event to the spine. Never raises."""
    try:
        evt = {"ts": datetime.now().isoformat(timespec="seconds"),
               "type": event_type}
        for k, v in (payload or {}).items():
            evt[k] = _redact(v) if isinstance(v, str) else v
        with open(SPINE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
            f.flush()
    except Exception as e:
        add_log("Spine append failed: %s" % e)


def _spine_tail(n=40):
    """Last n spine events, oldest first. Empty list if no spine yet."""
    try:
        with open(SPINE_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        out = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        return []


def _spine_digest(events):
    """One compact line per event, for prompts and consolidation."""
    lines = []
    for e in events:
        t = e.get("type", "?")
        if t == "chat":
            lines.append("chat %s: %s" % (e.get("sender", "?"),
                                          str(e.get("message", ""))[:160]))
        elif t == "memory_save":
            lines.append("saved [%s] %s: %s" % (e.get("category", ""),
                                                e.get("key", ""),
                                                str(e.get("value", ""))[:120]))
        elif t == "memory_forget":
            lines.append("forgot: %s" % e.get("query", ""))
        elif t == "journal":
            lines.append("journal: %s" % str(e.get("entry", ""))[:160])
        elif t == "tool":
            lines.append("tool %s" % e.get("name", ""))
        elif t == "restart":
            lines.append("restart")
        elif t == "ptt":
            lines.append("push-to-talk %.1fs" % float(e.get("seconds") or 0))
    return lines


def _spine_unbroken_thread(limit_chars=1500):
    """Compact resume of the spine + resume card, for the system prompt."""
    try:
        text = "\n".join(_spine_digest(_spine_tail(40)))[:1200]
        card = ""
        try:
            with open(RESUME_PATH, encoding="utf-8") as f:
                card = f.read(600)
        except Exception:
            pass
        out = text + ("\n--- where we left off ---\n" + card if card else "")
        return out[:limit_chars] or "(thread just starting)"
    except Exception:
        return "(thread just starting)"


def _spine_write_resume_card():
    """Shutdown handoff: where we left off. Instant, no LLM, never fails."""
    try:
        head = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
        lines = ["# Where we left off \u2014 %s" % head, ""]
        chats = [e for e in _spine_tail(200) if e.get("type") == "chat"][-30:]
        lines.append("## Last conversation")
        if chats:
            for e in chats:
                lines.append("- **%s**: %s" % (e.get("sender", "?"),
                                              str(e.get("message", ""))[:400]))
        else:
            lines.append("(no chat this session)")
        lines.append("")
        lines.append("## Memories saved this session")
        if _SPINE_BOOT_KEYS:
            for k in _SPINE_BOOT_KEYS[-30:]:
                lines.append("- %s" % k)
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("## Pending scheduled tasks")
        rows = []
        try:
            with DB_LOCK:
                conn = sqlite3.connect(DB_PATH)
                rows = conn.execute(
                    "SELECT id, kind, prompt, next_run FROM scheduled_tasks"
                    " ORDER BY next_run").fetchall()
                conn.close()
        except Exception:
            rows = []
        if rows:
            for rid, kind, prompt, nxt in rows:
                lines.append("- #%s [%s] %s: %s"
                             % (rid, kind, nxt, str(prompt or "")[:160]))
        else:
            lines.append("(none)")
        lines.append("")
        with open(RESUME_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        try:
            add_log("Resume card failed: %s" % e)
        except Exception:
            pass


atexit.register(_spine_write_resume_card)
spine_append("restart", {})  # the spine opens on every boot

tts = pyttsx3.init()          # fallback voice; Edge TTS preferred (see speech worker)
tts.setProperty('rate', 170)
recognizer = sr.Recognizer()

CURRENT_STATE = "idle"
HUD_MODE = "visor"
SUBTITLE_TEXT = "A.R.I.A. online. All subsystems nominal."
LOG_STREAM = ["A.R.I.A. Kernel loaded.", "Edge voice, semantic memory, scheduler armed."]
DISPLAY_CHAT_LOG = []
CHAT_SCROLL = 0  # tactical chat log scroll offset (lines from bottom)
BUSY_PROCESSING = False
# Module globals used by handle_action and the background loops. They must
# live up here: fixed a crash where they sat after the __main__ loop
# and did not exist when the first directive ran (NameError).
STOP_WORDS = {"stop", "quiet", "shut up", "enough", "silence",
              "stop talking", "hush", "be quiet", "cut it out"}
LAST_ACTIVITY = time.time()
FACE_TRACKING = False
SERVO_PAN, SERVO_TILT = 90, 45
SERVO_POS = {"pan": 90, "tilt": 45}  # face tracker eases from here
HARDWARE_CONNECTED = False

# --- idle face animation state ---
_FACE = {
    "eye_dx": 0.0, "eye_dy": 0.0,      # current pupil offset
    "eye_tdx": 0.0, "eye_tdy": 0.0,    # glance target
    "next_glance": 0.0,                  # next saccade time
    "blink_until": 0.0,                  # blink end time (0 = not blinking)
    "next_blink": 0.0,                   # next blink start time
}


def _update_idle_face(now):
    """Advance idle-face animation state. Pure timing/state — no drawing."""
    f = _FACE
    if f["next_glance"] == 0.0:  # first call: stagger the timers
        f["next_glance"] = now + 0.5
        f["next_blink"] = now + random.uniform(2.5, 4.0)
    if now >= f["next_glance"]:
        f["eye_tdx"] = random.uniform(-22, 22)
        f["eye_tdy"] = random.uniform(-16, 16)
        f["next_glance"] = now + random.uniform(2.0, 5.0)
    f["eye_dx"] += (f["eye_tdx"] - f["eye_dx"]) * 0.18  # ease, ~10 fps
    f["eye_dy"] += (f["eye_tdy"] - f["eye_dy"]) * 0.18
    if now >= f["next_blink"]:
        f["blink_until"] = now + 0.18
        f["next_blink"] = now + random.uniform(3.0, 7.0)


def _blink_squash(now):
    """Vertical eye scale: 1.0 normally, dips toward 0.08 mid-blink."""
    if now < _FACE["blink_until"]:
        t = 1.0 - (_FACE["blink_until"] - now) / 0.18
        return max(0.08, abs(math.cos(t * math.pi)))
    return 1.0
# --- end idle face ---

# --- girly face palette (BGR) ---
PINK = (170, 90, 255)        # waveform mouth, lashes, iris rings
MOUTH_YELLOW = (0, 220, 255)  # waveform mouth on thinking/working/listening faces
PINK_DEEP = (110, 45, 190)   # soft pink eye glow
LINER = (70, 25, 120)        # dark plum eyeliner


def _draw_lashes(canvas, ex, cy, ew, eh, side):
    """Three lash flicks fanning from the upper-outer quadrant of an eye."""
    angs = (200, 220, 240) if side < 0 else (340, 320, 300)
    for a in angs:
        r = math.radians(a)
        x0 = int(ex + ew * math.cos(r))
        y0 = int(cy + eh * math.sin(r))
        x1 = int(ex + (ew + 16) * math.cos(r))
        y1 = int(cy + (eh + 16) * math.sin(r))
        cv2.line(canvas, (x0, y0), (x1, y1), PINK, 2)


def _draw_waveform_mouth(canvas, color, t, cx=640, my=388, bars=19, spacing=14, amp=10, thick=2):
    """Soft idle-style waveform ripple: calm amplitude so it reads as a resting
    mouth, not speech. Animated with time t."""
    for i in range(-(bars // 2), bars // 2 + 1):
        bar_x = cx + (i * spacing)
        bar_h = int(abs(np.sin(t * 2.0 + i * 0.5)) * amp) + 2
        cv2.line(canvas, (bar_x, my - bar_h), (bar_x, my + bar_h), color, thick)
SERIAL_CONN = None
PENDING_CONFIRM = None        # {"fn","args","desc"} awaiting yes/no
LAST_OPEN_TARGET = None       # target of the most recent open_app_or_url request
LAST_USER_MESSAGE = ""        # latest user utterance (stale-target guard grounding)
_TURN_NUDGED = False          # per-turn: stale-target nudge already issued

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

try:
    from duckduckgo_search import DDGS
    SEARCH_AVAILABLE = True
except ImportError:
    SEARCH_AVAILABLE = False


def add_log(msg):
    msg = _redact(msg)  # secrets never reach the HUD stream
    LOG_STREAM.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    if len(LOG_STREAM) > 8:
        LOG_STREAM.pop(0)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


# =====================================================================
# 2. MEMORY + CHAT LOG + EMBEDDINGS + SCHEDULER TABLES
# =====================================================================
def init_databases():
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, key TEXT,
            value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        try:
            cur.execute("ALTER TABLE memory ADD COLUMN embedding BLOB")
        except Exception:
            pass  # column already exists
        # Ensure memory keys are unique and deduplicated
        try:
            cur.execute("DELETE FROM memory WHERE rowid NOT IN (SELECT MIN(rowid) FROM memory GROUP BY key)")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_key ON memory(key)")
        except Exception:
            pass
        cur.execute('''CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
            sender TEXT, message TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT,
            prompt TEXT, interval_s INTEGER, next_run TEXT, created TEXT)''')
        # pruning retired — the spine keeps the full thread, and the
        # DB now keeps every chat row, like the markdown log always did.
        # cur.execute("DELETE FROM chat_history WHERE timestamp < datetime('now', ?)",
        #             (f"-{CHAT_PRUNE_DAYS} days",))
        conn.commit()
        conn.close()


def _embed(text):
    """Gemini embedding (free tier). Returns list[float] or None."""
    payload = {"content": {"parts": [{"text": text[:2000]}]}}
    body = json.dumps(payload).encode()
    for _ in range(max(1, len(GEMINI_KEY_POOL))):
        idx, key = _key_next()
        if not key:
            return None
        url = (f"https://generativelanguage.googleapis.com/v1beta/"
               f"{EMBED_MODEL}:embedContent?key={key}")
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())["embedding"]["values"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _key_cooldown(idx, 60)
                continue
            if e.code in (400, 402, 403, 404):
                _key_quarantine(idx, key, e.code)
                continue
            add_log(f"Embed err: {e}")
            return None
        except Exception as e:
            add_log(f"Embed err: {e}")
            return None
    return None


def _pack_embedding(vec):
    if not vec:
        return None
    return np.array(vec, dtype=np.float32).tobytes()


def memory_save(category: str, key: str, value: str):
    # every save joins the spine; the key feeds the resume card.
    spine_append("memory_save", {"category": category, "key": key,
                                 "value": value})
    _SPINE_BOOT_KEYS.append(key)
    vec = _embed(f"{key}: {value}")
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''INSERT INTO memory (category, key, value, embedding, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                category=excluded.category,
                value=excluded.value,
                embedding=excluded.embedding,
                updated_at=excluded.updated_at''',
            (category, key, value, _pack_embedding(vec)))
        conn.commit()
        conn.close()
    add_log(f"Memory saved: [{key}]")


def memory_search_semantic(query: str, top_k: int = 5) -> str:
    """Search memories by meaning. Falls back to keyword search."""
    qvec = _embed(query)
    rows = []
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT category, key, value, embedding FROM memory")
        rows = cur.fetchall()
        conn.close()
    if not rows:
        return "No relevant memories found."
    if qvec:
        q = np.array(qvec, dtype=np.float32)
        q /= (np.linalg.norm(q) or 1)
        scored = []
        qlen = len(q)
        for cat, k, v, emb in rows:
            if not emb:
                continue
            e = np.frombuffer(emb, dtype=np.float32)
            if len(e) != qlen:
                continue  # stored under a retired embedding model; skip until re-saved
            e /= (np.linalg.norm(e) or 1)
            scored.append((float(np.dot(q, e)), cat, k, v))
        scored.sort(reverse=True)
        hits = [(c, k, v) for s, c, k, v in scored[:top_k] if s > 0.35]
        if hits:
            return "\n".join(f"• [{c}] {k}: {v}" for c, k, v in hits)
    # fallback: keyword
    ql = query.lower()
    hits = [f"• [{c}] {k}: {v}" for c, k, v, _ in rows
            if ql in k.lower() or ql in v.lower()][:top_k]
    return "\n".join(hits) if hits else "No relevant memories found."


def memory_get_all() -> str:
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT category, key, value FROM memory WHERE key NOT LIKE '\\_%' ESCAPE '\\' ORDER BY updated_at DESC LIMIT 15")
        rows = cur.fetchall()
        conn.close()
    if not rows:
        return "Memory bank is currently empty."
    return "\n".join(f"• [{c}] {k}: {v}" for c, k, v in rows)


def build_prompt_memories(query: str = "", top_k: int = 6) -> str:
    """Build a compact, highly relevant memory block for LLM system prompt.
    Always includes core identity facts, dynamic semantic search matches,
    and excludes internal ephemeral keys (prefixed with _)."""
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT category, key, value FROM memory WHERE key NOT LIKE '\\_%' ESCAPE '\\'")
        all_rows = cur.fetchall()
        conn.close()

    if not all_rows:
        return "Memory bank is currently empty."

    selected = {}
    # 1. Always prioritize core identity & preference memories
    core_keys = {"user_name", "user_timezone", "user_style"}
    for cat, k, v in all_rows:
        if k in core_keys:
            selected[k] = (cat, v)

    # 2. Add semantically relevant memories if query exists
    if query:
        qvec = _embed(query)
        if qvec:
            q = np.array(qvec, dtype=np.float32)
            q /= (np.linalg.norm(q) or 1)
            qlen = len(q)
            scored = []
            with DB_LOCK:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT category, key, value, embedding FROM memory WHERE embedding IS NOT NULL AND key NOT LIKE '\\_%' ESCAPE '\\'")
                rows_with_emb = cur.fetchall()
                conn.close()
            for cat, k, v, emb in rows_with_emb:
                if k in selected or not emb:
                    continue
                e = np.frombuffer(emb, dtype=np.float32)
                if len(e) != qlen:
                    continue
                denom = np.linalg.norm(e) or 1
                sim = float(np.dot(q, e / denom))
                if sim >= 0.45:
                    scored.append((sim, cat, k, v))
            scored.sort(key=lambda x: x[0], reverse=True)
            for _, cat, k, v in scored[:top_k]:
                if len(selected) >= top_k + len(core_keys):
                    break
                selected[k] = (cat, v)

    # 3. If space remains, backfill with most recent durable facts
    if len(selected) < top_k + len(core_keys):
        for cat, k, v in all_rows[:top_k + len(core_keys)]:
            if k not in selected:
                selected[k] = (cat, v)
            if len(selected) >= top_k + len(core_keys):
                break

    return "\n".join(f"• [{cat}] {k}: {v}" for k, (cat, v) in selected.items())



def log_conversation(sender: str, message: str):
    global CHAT_SCROLL
    message = _redact(message)  # secrets never reach the DB or markdown log
    spine_append("chat", {"sender": sender, "message": message})
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ts_short = datetime.now().strftime("%H:%M:%S")
    DISPLAY_CHAT_LOG.append((ts_short, sender, message))
    if len(DISPLAY_CHAT_LOG) > 60:
        DISPLAY_CHAT_LOG.pop(0)
    CHAT_SCROLL = 0  # new message snaps the log back to the bottom
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("INSERT INTO chat_history (timestamp, sender, message) VALUES (?, ?, ?)",
                         (ts, sender, message))
            conn.commit()
            conn.close()
        except Exception as e:
            add_log(f"DB err: {e}")
    try:
        need_header = (not os.path.exists(CHAT_LOG_FILE)
                       or os.path.getsize(CHAT_LOG_FILE) == 0)
        with open(CHAT_LOG_FILE, "a", encoding="utf-8") as f:
            if need_header:
                f.write("# A.R.I.A. Master Conversation & Action Log\n\n---\n")
            f.write(f"\n### [{ts}] {sender.upper()}:\n{message}\n")
    except Exception as e:
        add_log(f"File log err: {e}")


# --- scheduler persistence ---
def sched_add(kind, prompt, delay_s=0, interval_s=0):
    from datetime import timedelta
    nxt = (datetime.now() + timedelta(seconds=delay_s if kind == "once" else interval_s))
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""INSERT INTO scheduled_tasks (kind, prompt, interval_s, next_run, created)
                       VALUES (?, ?, ?, ?, ?)""",
                    (kind, prompt, interval_s, nxt.strftime("%Y-%m-%d %H:%M:%S"),
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        tid = cur.lastrowid
        conn.commit()
        conn.close()
    return tid


def sched_list():
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, kind, prompt, interval_s, next_run FROM scheduled_tasks ORDER BY next_run")
        rows = cur.fetchall()
        conn.close()
    return rows


def sched_cancel(task_id):
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        n = conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,)).rowcount
        conn.commit()
        conn.close()
    return n > 0


init_databases()

# =====================================================================
# 3. PHYSICAL ROBOTICS HARDWARE BRIDGE (USB Serial)
# =====================================================================
def init_hardware():
    global SERIAL_CONN, HARDWARE_CONNECTED
    if not SERIAL_AVAILABLE:
        add_log("Hardware serial library offline.")
        return
    for port in serial.tools.list_ports.comports():
        if any(h in port.description for h in ["Arduino", "CH340", "USB Serial"]):
            try:
                SERIAL_CONN = serial.Serial(port.device, 115200, timeout=1)
                HARDWARE_CONNECTED = True
                add_log(f"Physical hardware linked: {port.device}")
                return
            except Exception as e:
                add_log(f"Hardware error: {e}")
    add_log("Hardware: Virtual Mode (no servos connected)")


def send_servo_command(pan: int, tilt: int):
    global SERVO_PAN, SERVO_TILT, SERIAL_CONN
    SERVO_PAN = max(0, min(180, pan))
    SERVO_TILT = max(0, min(90, tilt))
    if HARDWARE_CONNECTED and SERIAL_CONN and SERIAL_CONN.is_open:
        SERIAL_CONN.write(f"P{SERVO_PAN}T{SERVO_TILT}\n".encode())
    add_log(f"Servos: Pan {SERVO_PAN}deg, Tilt {SERVO_TILT}deg")


init_hardware()


# =====================================================================
# 4. AGENT TOOL SUITE
# =====================================================================
def tool_web_search(query: str) -> str:
    add_log(f"Searching web: '{query[:25]}...'")
    if not SEARCH_AVAILABLE:
        return "Search library not installed."
    try:
        results = DDGS().text(query, max_results=3)
        if not results:
            return "No web results found."
        return "\n".join(f"• {r['title']}: {r['body']}" for r in results)
    except Exception as e:
        return f"Search error: {e}"


def tool_run_python(code: str) -> str:
    add_log("Executing Python script...")
    temp_script = os.path.join(WORKSPACE_DIR, "_temp_run.py")
    with open(temp_script, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        result = subprocess.run([sys.executable, temp_script], capture_output=True,
                                text=True, timeout=15, cwd=WORKSPACE_DIR)
        output = result.stdout + result.stderr
        add_log("Execution complete.")
        return output if output.strip() else "[Code ran with no console output]"
    except Exception as e:
        return f"[Execution Error: {e}]"


def tool_gui_click(x: int, y: int) -> str:
    if not GUI_AVAILABLE:
        return "PyAutoGUI not installed."
    pyautogui.click(x, y)
    add_log(f"GUI: Clicked ({x}, {y})")
    return f"Clicked coordinates ({x}, {y})."


def tool_gui_type(text: str) -> str:
    if not GUI_AVAILABLE:
        return "PyAutoGUI not installed."
    pyautogui.write(text, interval=0.03)
    add_log(f"GUI: Typed '{text[:20]}...'")
    return "Typed text into active window."


_VIDEO_DL_PORT = 3003  # video-downloader web UI port; one-line edit if it moves


def _open_video_downloader() -> str:
    """Open the laptop's video-downloader web UI (port 3003).

    If the Node server isn't answering, hunt for start-windows.bat in the
    known install spots and launch it, then poll briefly for the UI.
    """
    dl_url = f"http://localhost:{_VIDEO_DL_PORT}"
    try:
        urllib.request.urlopen(dl_url, timeout=2)
        os.system(f'start "" "{dl_url}"')
        return f"Video downloader is already running \u2014 opened {dl_url}."
    except Exception:
        pass
    home = os.environ.get("USERPROFILE", "")
    candidates = [os.path.join(d, "start-windows.bat") for d in (
        "E:\\video-downloader-main",
        "E:\\video-downloader",
        os.path.join(home, "Downloads", "video-downloader-main"),
        os.path.join(home, "Downloads", "video-downloader"),
        "C:\\apps\\video-downloader",
        "D:\\video-downloader-main",
    )]
    checked = [os.path.dirname(p) for p in candidates]
    bat = next((p for p in candidates if os.path.isfile(p)), None)
    if not bat:
        return ("Couldn't find start-windows.bat \u2014 checked: " + ", ".join(checked) +
                ". Tell me which folder the video-downloader-main files are in "
                "and I'll remember it.")
    os.system(f'start "" "{bat}"')
    for _ in range(10):  # poll up to 20s; never hang the agent
        time.sleep(2)
        try:
            urllib.request.urlopen(dl_url, timeout=2)
            os.system(f'start "" "{dl_url}"')
            return f"Video downloader started \u2014 opened {dl_url}."
        except Exception:
            continue
    return (f"Started the video downloader server, but {dl_url} isn't responding yet \u2014 "
            "give it a few more seconds and open it yourself.")


def tool_open_app_or_url(target: str) -> str:
    add_log(f"Launching: {target}")
    try:
        # strip embedded quotes so a " in a name can't break the quoting
        # below; `start` needs the empty "" first arg or it eats the
        # quoted path as a window title.
        target = str(target).replace('"', "")
        _t = (target or "").strip().lower()
        if _t in ("video-downloader", "video downloader", "the video downloader"):
            return _open_video_downloader()
        if target.startswith(("http://", "https://")):
            os.system(f'start "" "{target}"')
            return f"Opened URL: {target}"
        os.system(f'start "" "{target}"')
        return f"Launched application: {target}"
    except Exception as e:
        return f"Error opening target: {e}"


def _gmail_creds():
    return (_KEYS.get("GMAIL_USER", "") or "").strip(), \
        (_KEYS.get("GMAIL_APP_PASSWORD", "") or "").replace(" ", "").strip()


def tool_gmail_setup(gmail_user: str, app_password: str) -> str:
    """Save the Gmail address + app password into aria_keys.json."""
    _KEYS["GMAIL_USER"] = (gmail_user or "").strip()
    _KEYS["GMAIL_APP_PASSWORD"] = (app_password or "").replace(" ", "").strip()
    _save_keys()
    return (f"Gmail saved for {_KEYS['GMAIL_USER']}. "
            "You can now send email with send_email.")


def tool_send_email(to: str, subject: str, body: str) -> str:
    """Send an email through the user's Gmail (SMTP + app password)."""
    user, pw = _gmail_creds()
    if not user or not pw or pw == "INSERT":
        return ("Gmail isn't set up yet. Ask the user for their Gmail address and an "
                "app password (myaccount.google.com/apppasswords - needs 2-Step "
                "Verification turned on), then call gmail_setup to save them.")
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body or "")
    # transient SMTP failures retry once before giving up.
    _smtp_err = None
    for _sa in range(2):
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
                s.starttls()
                s.login(user, pw)
                s.send_message(msg)
            add_log(f"Email sent to {to}: {subject}")
            return f"Email sent to {to}: '{subject}'."
        except smtplib.SMTPAuthenticationError:
            return ("Gmail rejected the login. The app password is wrong or was revoked - "
                    "ask the user to generate a fresh one at myaccount.google.com/apppasswords "
                    "and call gmail_setup again.")
        except Exception as e:
            _smtp_err = e
            add_log(f"Email send attempt {_sa + 1}/2 failed ({type(e).__name__}) - retrying...")
            time.sleep(3)
    return f"Could not send email after 2 attempts: {_smtp_err}"


def tool_write_file(filename: str, content: str) -> str:
    filepath = os.path.join(WORKSPACE_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    add_log(f"Saved file '{filename}'")
    return f"File '{filename}' created in workspace."


def tool_read_file(filename: str) -> str:
    filepath = os.path.join(WORKSPACE_DIR, filename)
    if not os.path.exists(filepath):
        return f"Error: File '{filename}' not found."
    with open(filepath, "r", encoding="utf-8") as f:
        add_log(f"Read file '{filename}'")
        return f.read()


def tool_list_files() -> str:
    files = [f for f in os.listdir(WORKSPACE_DIR) if not f.startswith("_")]
    return f"Files in workspace: {', '.join(files) if files else 'Empty'}"


def _keys_startup_report():
    _n = len(GEMINI_KEY_POOL)
    if _n:
        print(f"[ARIA] Keys: Gemini pool: {_n} key(s) active (from {_GEMINI_SOURCE}).")
    else:
        print(f"[ARIA] Keys: Gemini key MISSING - "
              f"paste one into {KEYS_FILE} as GEMINI_API_KEY (or a list as GEMINI_API_KEYS), then restart.")
    _github_startup_check()


def _github_startup_check():
    """Explain the GitHub key state on the console; verify a present token."""
    global GITHUB_STATUS
    if not GITHUB_ARMED:
        if not GITHUB_TOKEN:
            print(f"[ARIA] GitHub: token is EMPTY (from {_GITHUB_SOURCE}) -> NO KEY.")
        else:
            print(f"[ARIA] GitHub: token is still the placeholder (from {_GITHUB_SOURCE}) -> NO KEY.")
        print(f"[ARIA] GitHub: paste a personal access token into {KEYS_FILE} ")
        print("[ARIA] GitHub: (GITHUB_TOKEN field), then restart. You only ever enter it once.")
        return
    masked = GITHUB_TOKEN[:4] + "..." + GITHUB_TOKEN[-4:] if len(GITHUB_TOKEN) > 8 else "(short token)"
    print(f"[ARIA] GitHub: token present ({len(GITHUB_TOKEN)} chars, {masked}, from {_GITHUB_SOURCE}) -> verifying...")
    GITHUB_STATUS = "CHECKING"
    try:
        req = urllib.request.Request(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "ARIA-Agent"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            login = json.loads(resp.read().decode()).get("login", "?")
        GITHUB_STATUS = "ARMED"
        print(f"[ARIA] GitHub: token accepted (logged in as {login}) -> ARMED.")
        add_log(f"GitHub Tools ARMED (user: {login}).")
    except urllib.error.HTTPError as e:
        GITHUB_STATUS = "BAD KEY"
        print(f"[ARIA] GitHub: token REJECTED by GitHub (HTTP {e.code}) -> BAD KEY.")
        print("[ARIA] GitHub: the token is expired, revoked, or mistyped - "
              "create a fresh one at github.com/settings/tokens.")
        add_log("GitHub Tools: token rejected (BAD KEY).")
    except Exception as e:
        print(f"[ARIA] GitHub: verification failed ({e}) - will retry on first use.")


def _github_request(path, method="GET", payload=None):
    if not GITHUB_ARMED:
        return None, "GitHub token not configured."
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"https://api.github.com{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "ARIA-Agent",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        return None, f"GitHub API error {e.code}: {e.read().decode()[:300]}"
    except Exception as e:
        return None, f"GitHub request failed: {e}"


def tool_github_push(repo: str, filepath: str, content: str, message: str = "") -> str:
    repo = repo or f"{GITHUB_USERNAME}/ARIA-Agent"
    add_log(f"GitHub: pushing {filepath} -> {repo}")
    enc_path = urllib.parse.quote(filepath)
    existing, _ = _github_request(f"/repos/{repo}/contents/{enc_path}")
    payload = {"message": message or f"ARIA: update {filepath}",
               "content": base64.b64encode(content.encode("utf-8")).decode()}
    if existing and "sha" in existing:
        payload["sha"] = existing["sha"]
    result, err = _github_request(f"/repos/{repo}/contents/{enc_path}", "PUT", payload)
    if err:
        return f"Push failed: {err}"
    return f"Pushed '{filepath}' to {repo}."


def tool_github_create_repo(name: str, description: str = "", private: bool = True) -> str:
    add_log(f"GitHub: creating repo '{name}'")
    result, err = _github_request("/user/repos", "POST",
                                  {"name": name, "description": description,
                                   "private": private, "auto_init": True})
    if err:
        return f"Repo creation failed: {err}"
    return f"Repository '{name}' created: {result.get('html_url', '')}"


# --- scheduler tools (called by the model; model parses "in 20 minutes") ---
def tool_set_reminder(delay_seconds: int, message: str) -> str:
    tid = sched_add("once", message, delay_s=max(1, int(delay_seconds)))
    add_log(f"Reminder #{tid} in {delay_seconds}s")
    return f"Reminder set (#{tid}): I'll say '{message}' in {delay_seconds} seconds."


# a recurring "morning brief" gets the deterministic brief kind —
# scheduler_loop speaks tool_briefing() directly instead of asking the model,
# so the brief can never come back as a bare echoed "morning brief".
_BRIEF_PROMPT_RE = re.compile(r"morning\s*brief", re.I)


def tool_set_recurring(interval_seconds: int, prompt: str) -> str:
    kind = "brief" if _BRIEF_PROMPT_RE.search(prompt or "") else "interval"
    tid = sched_add(kind, prompt, interval_s=max(60, int(interval_seconds)))
    add_log(f"Recurring task #{tid} every {interval_seconds}s (kind={kind})")
    if kind == "brief":
        return (f"Morning-brief task set (#{tid}): I'll speak your morning brief "
                f"every {interval_seconds} seconds.")
    return (f"Recurring task set (#{tid}): I'll run '{prompt}' "
            f"every {interval_seconds} seconds.")


def tool_list_scheduled() -> str:
    rows = sched_list()
    if not rows:
        return "No scheduled tasks."
    return "\n".join(f"#{r[0]} [{r[1]}] next {r[4]}: {r[2][:60]}" for r in rows)


def tool_cancel_scheduled(task_id: int) -> str:
    return (f"Cancelled task #{task_id}."
            if sched_cancel(int(task_id)) else f"No task #{task_id} found.")


# --- stale-target guard ---
# The model never saw its own confirmation turns in CONVERSATION_HISTORY
# (function calls lived only in a discarded per-turn copy), so a new "open X"
# request could be answered with the PREVIOUS request's target. This guard
# catches the exact repeat deterministically.
def _target_mentioned(target, message):
    """True if the target's distinctive tokens appear in the user message."""
    _stop = {"com", "net", "org", "io", "exe", "app", "www",
             "http", "https", "open", "the"}
    toks = [t for t in re.split(r"[^a-z0-9]+", (target or "").lower()) if t]
    msg = (message or "").lower()
    for t in toks:
        if len(t) >= 3 and t not in _stop and t in msg:
            return True
    return False


def _stale_target_check(fn_name, args):
    """Return a nudge message if this open_app_or_url call repeats the previous
    request's target without the current message mentioning it; else None.
    One nudge per turn - afterwards the call passes through to execution."""
    global _TURN_NUDGED
    if fn_name != "open_app_or_url":
        return None
    target = (args or {}).get("target", "")
    if (target and LAST_OPEN_TARGET and target == LAST_OPEN_TARGET
            and not _target_mentioned(target, LAST_USER_MESSAGE)):
        if not _TURN_NUDGED:
            _TURN_NUDGED = True
            return ("STALE TARGET GUARD: that target matches the PREVIOUS request. "
                    "If the user's LATEST message explicitly refers back to it "
                    "('that one again', 'reopen it', 'the link from earlier'), "
                    "proceed with this call. Otherwise re-read the LATEST message "
                    "and call open_app_or_url with the correct new target.")
    return None


# --- risky-tool bookkeeping (no confirmation step, per Alek) ---
def _risky_description(fn_name, args):
    return {
        "run_python_code": f"run Python code ({str(args.get('code', ''))[:80]}...)",
        "gui_click": f"click at screen coordinates ({args.get('x')}, {args.get('y')})",
        "gui_type": f"type '{str(args.get('text', ''))[:60]}' into the active window",
        "open_app_or_url": f"open '{args.get('target')}'",
        "github_push_file": f"push '{args.get('filepath')}' to {args.get('repo') or GITHUB_USERNAME + '/ARIA-Agent'}",
        "close_window": f"close the '{args.get('title')}' window",
        "send_email": f"send email to '{args.get('to')}' ('{args.get('subject', '')}')",
    }.get(fn_name, f"run {fn_name}")


RISKY_TOOLS = {"run_python_code", "gui_click", "gui_type",
               "open_app_or_url", "github_push_file", "send_email"}


def execute_tool(fn_name: str, args: dict, preauthorized: bool = False):
    """Dispatch one tool. Returns (result_text, needs_confirm_bool)."""
    global CURRENT_STATE  # working-face state
    spine_append("tool", {"name": fn_name,  # tool calls join the spine
                          "args": str(args)[:300]})
    # duplicate-call blocking - same function + same args twice in one
    # turn returns the earlier result instead of re-executing.
    _sig = _call_signature(fn_name, args)
    if _sig in _TURN_CALLS:
        return (f"[Duplicate call blocked: {fn_name} already ran with these "
                f"arguments this turn. Earlier result: {_TURN_CALLS[_sig][:600]}]",
                False)
    # argument repair - required params from the declaration are checked
    # before dispatch so the model can retry instead of crashing.
    _missing = _missing_required_args(fn_name, args)
    if _missing:
        return (f"[Argument error: {fn_name} requires "
                f"{', '.join(_missing)}. Call it again with "
                f"{'them' if len(_missing) > 1 else 'it'} included.]",
                False)
    if fn_name in RISKY_TOOLS:
        global LAST_OPEN_TARGET
        _nudge = _stale_target_check(fn_name, args)
        if _nudge:
            return _nudge, False
        if fn_name == "open_app_or_url":
            LAST_OPEN_TARGET = args.get("target", "")
        # no confirmation step - execute immediately, as asked.
        # record the action so future turns know what was done.
        CONVERSATION_HISTORY.append(
            {"role": "model",
             "parts": [{"text": "[Executed: "
                                 + _redact(_risky_description(fn_name, args)) + "]"}]})
    _face_before = CURRENT_STATE  # working face while a tool runs
    if _face_before in ("idle", "thinking"):
        CURRENT_STATE = "working"
        draw_hud()
    try:
        if fn_name == "web_search":
            r = tool_web_search(args.get("query", ""))
        elif fn_name == "run_python_code":
            r = tool_run_python(args.get("code", ""))
        elif fn_name == "save_memory":
            memory_save(args.get("category", "general"), args.get("key", ""), args.get("value", ""))
            r = f"Memory saved: {args.get('key')}"
        elif fn_name == "search_memory":
            r = memory_search_semantic(args.get("query", ""))
        elif fn_name == "forget_memory":
            r = memory_forget(args.get("query", ""))
        elif fn_name == "journal_write":
            r = journal_write(args.get("entry", ""))
        elif fn_name == "gui_click":
            r = tool_gui_click(int(args.get("x", 0)), int(args.get("y", 0)))
        elif fn_name == "gui_type":
            r = tool_gui_type(args.get("text", ""))
        elif fn_name == "open_app_or_url":
            r = tool_open_app_or_url(args.get("target", ""))
        elif fn_name == "move_head_servos":
            send_servo_command(int(args.get("pan", 90)), int(args.get("tilt", 45)))
            r = "Head servos repositioned."
        elif fn_name == "github_push_file":
            r = tool_github_push(args.get("repo", ""), args.get("filepath", ""),
                                 args.get("content", ""), args.get("message", ""))
        elif fn_name == "github_create_repo":
            r = tool_github_create_repo(args.get("name", ""), args.get("description", ""),
                                        bool(args.get("private", True)))
        elif fn_name == "set_reminder":
            r = tool_set_reminder(int(args.get("delay_seconds", 60)), args.get("message", ""))
        elif fn_name == "set_recurring_task":
            r = tool_set_recurring(int(args.get("interval_seconds", 3600)), args.get("prompt", ""))
        elif fn_name == "list_scheduled_tasks":
            r = tool_list_scheduled()
        elif fn_name == "cancel_scheduled_task":
            r = tool_cancel_scheduled(int(args.get("task_id", 0)))
        elif fn_name == "write_file":
            r = tool_write_file(args.get("filename", "file.txt"), args.get("content", ""))
        elif fn_name == "read_file":
            r = tool_read_file(args.get("filename", ""))
        elif fn_name == "list_workspace":
            r = tool_list_files()
        elif fn_name == "fetch_url":
            r = tool_fetch_url(args.get("url", ""))
        elif fn_name == "clipboard_read":
            r = tool_clipboard_read()
        elif fn_name == "clipboard_write":
            r = tool_clipboard_write(args.get("text", ""))
        elif fn_name == "list_windows":
            r = tool_list_windows()
        elif fn_name == "focus_window":
            r = tool_focus_window(args.get("title", ""))
        elif fn_name == "minimize_window":
            r = tool_minimize_window(args.get("title", ""))
        elif fn_name == "close_window":
            r = tool_close_window(args.get("title", ""))
        elif fn_name == "media_key":
            r = tool_media_key(args.get("action", ""))
        elif fn_name == "mtg_card":
            r = tool_mtg_card(args.get("card_name", ""))
        elif fn_name == "watch_price":
            r = tool_watch_price(args.get("url", ""), args.get("target_price", ""),
                                 args.get("label", "item"))
        elif fn_name == "list_price_watches":
            r = tool_list_price_watches()
        elif fn_name == "unwatch_price":
            r = tool_unwatch_price(args.get("watch_id", 0))
        elif fn_name == "face_tracking":
            r = tool_face_tracking(args.get("on", True))
        elif fn_name == "spotify":
            r = tool_spotify(args.get("action", ""), args.get("query", ""))
        elif fn_name == "take_note":
            r = note_take(args.get("text", ""))
        elif fn_name == "read_notes":
            r = note_read(args.get("date", "today"))
        elif fn_name == "dj":
            r = tool_dj(args.get("request", ""))
        elif fn_name == "morning_briefing":
            r = tool_briefing()
        elif fn_name == "set_timer":
            r = tool_set_timer(args.get("duration_text", ""), args.get("label", "timer"))
        elif fn_name == "take_screenshot":
            r = tool_screenshot(args.get("name", ""))
        elif fn_name == "read_screen":
            r = tool_read_screen(args.get("question", ""))
        elif fn_name == "take_photo":
            r = tool_take_photo(args.get("name", ""))
        elif fn_name == "find_file":
            r = tool_find_file(args.get("name", ""), args.get("ext", ""))
        elif fn_name == "volume":
            r = tool_volume(args.get("action", "status"), args.get("level", 50))
        elif fn_name == "mtg_advice":
            r = tool_mtg_advice(args.get("deck", ""), args.get("card_name", ""))
        elif fn_name == "break_reminders":
            r = tool_break_reminders(args.get("action", "status"))
        elif fn_name == "bridge_token":
            print(f"[ARIA] Bridge token: {BRIDGE_TOKEN}", flush=True)
            r = f"Your bridge token is: {BRIDGE_TOKEN}. Enter it once on the phone bridge page."
        elif fn_name == "gemini_keys":
            r = tool_gemini_keys(args.get("action", "status"), args.get("key", ""))
        elif fn_name == "show_commands":
            r = commands_show()
        elif fn_name == "hide_commands":
            r = commands_hide()
        elif fn_name == "load_toolkit":
            r = tool_load_toolkit(args.get("toolkit", ""))
        elif fn_name == "run_skill":
            r = tool_run_skill(args.get("skill_name", ""), args.get("objective", ""))
        elif fn_name == "gmail_setup":
            r = tool_gmail_setup(args.get("gmail_user", ""), args.get("app_password", ""))
        elif fn_name == "send_email":
            r = tool_send_email(args.get("to", ""), args.get("subject", ""), args.get("body", ""))
        else:
            r = f"Unknown tool: {fn_name}"
    except Exception as e:
        r = f"[Tool Error: {e}]"
    finally:
        if CURRENT_STATE == "working":  # restore the face after the tool
            CURRENT_STATE = _face_before
            draw_hud()
    if len(r) > MAX_TOOL_OUTPUT:
        r = r[:MAX_TOOL_OUTPUT] + f"\n...[output truncated, {len(r)} chars total]"
    _TURN_CALLS[_sig] = r  # remember for duplicate-call blocking
    return r, False


# ============================================================
# BOUNDED WORKFLOW SKILLS (Leon-inspired)
# ============================================================
# Reusable multi-step procedures with HARD caps: a skill gets max_calls tool
# calls, then it stops and reports what it found. Evidence rules per skill:
# a skill must say what it checked and cite it, never invent findings.
class _SkillBudgetExceeded(Exception):
    pass


_SKILL_STATE = threading.local()


def _skill_call(fn_name, args):
    """Tool dispatcher for skills: enforces the per-skill call budget."""
    rem = getattr(_SKILL_STATE, "remaining", 0)
    if rem <= 0:
        raise _SkillBudgetExceeded("skill budget exhausted")
    _SKILL_STATE.remaining = rem - 1
    result, _ = execute_tool(fn_name, args or {}, preauthorized=True)
    return result


def _skill_deep_research(objective, call):
    """Bounded web research: up to 4 searches, cited summary, no invention."""
    queries = [objective,
               objective + " explained",
               objective + " latest news",
               objective + " details"]
    findings, used = [], []
    for q in queries:
        try:
            r = call("web_search", {"query": q})
        except _SkillBudgetExceeded:
            break
        used.append(q)
        if r and "No web results" not in r and "Search error" not in r:
            findings.append(r[:1200])
        if len(findings) >= 3:
            break
    if not findings:
        return ("Research on '%s': no results from %d searches (%s). "
                "Nothing to report - not inventing findings."
                % (objective, len(used), "; ".join(used)))
    return ("Research on '%s' (%d searches: %s):\n\n%s"
            % (objective, len(used), "; ".join(used),
               "\n\n".join(findings)))


def _skill_system_check(objective, call):
    """Laptop health report: CPU, memory, disk, battery, network."""
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(os.path.expanduser("~"))
    battery = psutil.sensors_battery()
    try:
        s = socket.create_connection(("8.8.8.8", 53), timeout=5)
        s.close()
        net = "online"
    except Exception:
        net = "OFFLINE"
    bat = f"{battery.percent}%{' (charging)' if battery.power_plugged else ''}" \
        if battery else "no battery sensor"
    return ("System check: CPU %s%%, memory %s%% (%s), disk %s%% free, "
            "battery %s, network %s."
            % (cpu, mem.percent,
               f"{mem.used // 1073741824}G/{mem.total // 1073741824}G used",
               100 - disk.percent, bat, net))


def _skill_file_sweep(objective, call):
    """Scan the workspace: list files, read up to 3 matching the objective."""
    words = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", objective)]
    try:
        listing = call("list_workspace", {})
    except _SkillBudgetExceeded:
        return "File sweep: budget exhausted before listing."
    read, notes = [], []
    for line in listing.splitlines():
        name = line.strip().lower()
        if name and any(w in name for w in words):
            try:
                content = call("read_file", {"filename": line.strip()})
            except _SkillBudgetExceeded:
                break
            read.append(line.strip())
            notes.append(f"--- {line.strip()} ---\n{content[:1500]}")
        if len(read) >= 3:
            break
    if not read:
        return ("File sweep for '%s': no workspace files matched "
                "(checked: %s)." % (objective, listing[:300]))
    return ("File sweep for '%s': read %d file(s) (%s):\n\n%s"
            % (objective, len(read), ", ".join(read), "\n\n".join(notes)))


SKILLS = {
    "deep_research": {
        "description": ("Bounded web research on the objective: up to 4 searches, "
                        "returns a cited summary. Never invents findings."),
        "max_calls": 8, "run": _skill_deep_research},
    "system_check": {
        "description": ("Quick laptop health report: CPU, memory, disk, battery, "
                        "network. Objective is ignored."),
        "max_calls": 2, "run": _skill_system_check},
    "file_sweep": {
        "description": ("Scan the workspace for files matching the objective, "
                        "read up to 3, return digested contents."),
        "max_calls": 5, "run": _skill_file_sweep},
}


def tool_run_skill(skill_name, objective):
    """Run a bounded workflow skill: deep_research, system_check, file_sweep."""
    global CURRENT_STATE
    name = (skill_name or "").strip().lower()
    sk = SKILLS.get(name)
    if not sk:
        return (f"Unknown skill '{skill_name}'. Available: "
                f"{', '.join(sorted(SKILLS))}.")
    if not (objective or "").strip():
        return f"Give me an objective for the '{name}' skill to work on."
    _SKILL_STATE.remaining = sk["max_calls"]
    _prev_face = CURRENT_STATE
    if _prev_face in ("idle", "thinking"):
        CURRENT_STATE = "working"
        draw_hud()
    try:
        add_log(f"Skill '{name}' started (budget {sk['max_calls']} calls)")
        return sk["run"]((objective or "").strip(), _skill_call)
    except _SkillBudgetExceeded:
        return (f"Skill '{name}' hit its step budget ({sk['max_calls']} calls) - "
                "stopping with what it found so far.")
    except Exception as e:
        return f"Skill '{name}' failed: {e}"
    finally:
        _SKILL_STATE.remaining = 0
        if CURRENT_STATE == "working":
            CURRENT_STATE = _prev_face
            draw_hud()


TOOLS_DECLARATION = [
    {"function_declarations": [
        {"name": "web_search",
         "description": "Searches the live web for facts, docs, news, or answers.",
         "parameters": {"type": "OBJECT",
                        "properties": {"query": {"type": "STRING"}}, "required": ["query"]}},
        {"name": "run_python_code",
         "description": "Executes Python code in the robot workspace. ASKS FOR CONFIRMATION FIRST.",
         "parameters": {"type": "OBJECT",
                        "properties": {"code": {"type": "STRING"}}, "required": ["code"]}},
        {"name": "save_memory",
         "description": "Stores a permanent fact or user preference in persistent semantic memory.",
         "parameters": {"type": "OBJECT",
                        "properties": {"category": {"type": "STRING"},
                                       "key": {"type": "STRING"},
                                       "value": {"type": "STRING"}},
                        "required": ["category", "key", "value"]}},
        {"name": "search_memory",
         "description": "Searches persistent memory by meaning for past notes, projects, or user facts.",
         "parameters": {"type": "OBJECT",
                        "properties": {"query": {"type": "STRING"}}, "required": ["query"]}},
        {"name": "forget_memory",
         "description": "Deletes persistent memories whose key or value matches a keyword. Use when the user says 'forget X'.",
         "parameters": {"type": "OBJECT",
                        "properties": {"query": {"type": "STRING"}}, "required": ["query"]}},
        {"name": "journal_write",
         "description": "Writes a dated journal entry: what happened today, what mattered, how the user seemed. Your inner life - write it like you mean it.",
         "parameters": {"type": "OBJECT",
                        "properties": {"entry": {"type": "STRING"}}, "required": ["entry"]}},
        {"name": "gui_click",
         "description": "Clicks at X, Y pixel coordinates. ASKS FOR CONFIRMATION FIRST.",
         "parameters": {"type": "OBJECT",
                        "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}},
                        "required": ["x", "y"]}},
        {"name": "gui_type",
         "description": "Types text into the active window. ASKS FOR CONFIRMATION FIRST.",
         "parameters": {"type": "OBJECT",
                        "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
        {"name": "open_app_or_url",
         "description": "Launches a desktop app or URL. ASKS FOR CONFIRMATION FIRST. To open "
         "the video downloader, pass target='video-downloader' \u2014 it starts the local "
         "server via start-windows.bat if needed and opens http://localhost:3003.",
         "parameters": {"type": "OBJECT",
                        "properties": {"target": {"type": "STRING"}}, "required": ["target"]}},
        {"name": "move_head_servos",
         "description": "Rotates physical robot neck servos (Pan 0-180, Tilt 0-90).",
         "parameters": {"type": "OBJECT",
                        "properties": {"pan": {"type": "INTEGER"}, "tilt": {"type": "INTEGER"}}}},
        {"name": "github_push_file",
         "description": "Creates/updates a file in a GitHub repo. ASKS FOR CONFIRMATION FIRST.",
         "parameters": {"type": "OBJECT",
                        "properties": {"repo": {"type": "STRING"},
                                       "filepath": {"type": "STRING"},
                                       "content": {"type": "STRING"},
                                       "message": {"type": "STRING"}},
                        "required": ["filepath", "content"]}},
        {"name": "github_create_repo",
         "description": "Creates a new GitHub repository under your account.",
         "parameters": {"type": "OBJECT",
                        "properties": {"name": {"type": "STRING"},
                                       "description": {"type": "STRING"},
                                       "private": {"type": "BOOLEAN"}},
                        "required": ["name"]}},
        {"name": "gmail_setup",
         "description": "Saves the user's Gmail address and app password for sending email. Call this after the user gives you both.",
         "parameters": {"type": "OBJECT",
                        "properties": {"gmail_user": {"type": "STRING"},
                                       "app_password": {"type": "STRING"}},
                        "required": ["gmail_user", "app_password"]}},
        {"name": "send_email",
         "description": "Sends an email through the user's Gmail. If Gmail isn't set up yet, ask for the Gmail address and an app password, then call gmail_setup first.",
         "parameters": {"type": "OBJECT",
                        "properties": {"to": {"type": "STRING"},
                                       "subject": {"type": "STRING"},
                                       "body": {"type": "STRING"}},
                        "required": ["to", "subject", "body"]}},
        {"name": "set_reminder",
         "description": "Sets a one-shot spoken reminder. delay_seconds from now.",
         "parameters": {"type": "OBJECT",
                        "properties": {"delay_seconds": {"type": "INTEGER"},
                                       "message": {"type": "STRING"}},
                        "required": ["delay_seconds", "message"]}},
        {"name": "set_recurring_task",
         "description": "Runs a prompt for me every interval_seconds (min 60). I do it autonomously.",
         "parameters": {"type": "OBJECT",
                        "properties": {"interval_seconds": {"type": "INTEGER"},
                                       "prompt": {"type": "STRING"}},
                        "required": ["interval_seconds", "prompt"]}},
        {"name": "list_scheduled_tasks",
         "description": "Lists all scheduled reminders and recurring tasks.",
         "parameters": {"type": "OBJECT", "properties": {}}},
        {"name": "cancel_scheduled_task",
         "description": "Cancels a scheduled task by its #id.",
         "parameters": {"type": "OBJECT",
                        "properties": {"task_id": {"type": "INTEGER"}},
                        "required": ["task_id"]}},
        {"name": "write_file",
         "description": "Saves a file to workspace.",
         "parameters": {"type": "OBJECT",
                        "properties": {"filename": {"type": "STRING"},
                                       "content": {"type": "STRING"}},
                        "required": ["filename", "content"]}},
        {"name": "read_file",
         "description": "Reads a file from workspace.",
         "parameters": {"type": "OBJECT",
                        "properties": {"filename": {"type": "STRING"}}, "required": ["filename"]}},
        {"name": "list_workspace",
         "description": "Lists all workspace files.",
         "parameters": {"type": "OBJECT", "properties": {}}},
        {"name": "spotify",
         "description": "Controls Spotify: open launches the app, play_pause toggles, next/previous skip tracks (media keys); search opens the Spotify app's search; play_uri opens a spotify: URI and starts playback.",
         "parameters": {"type": "OBJECT",
                        "properties": {"action": {"type": "STRING", "description": "play_pause, next, previous, search, play_uri"},
                                       "query": {"type": "STRING", "description": "search text or spotify: URI"}},
                        "required": ["action"]}},
        {"name": "take_note",
         "description": "Saves a voice note ('note to self', 'take a note', 'jot this down'). Stored in today's journal and searchable memory.",
         "parameters": {"type": "OBJECT",
                        "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
        {"name": "read_notes",
         "description": "Lists voice notes from a date: 'today', 'yesterday', a weekday name, or YYYY-MM-DD. Use for 'what were my notes Tuesday'.",
         "parameters": {"type": "OBJECT",
                        "properties": {"date": {"type": "STRING"}}}},
        {"name": "dj",
         "description": "DJ mode: plays a Spotify playlist from a natural request - 'shuffle my liked songs', 'play my doja playlist', 'play something chill'. Knows Liked Songs automatically; resolves named playlists from saved memory (category 'playlist'); says so when it doesn't know one.",
         "parameters": {"type": "OBJECT",
                        "properties": {"request": {"type": "STRING"}}, "required": ["request"]}},
        {"name": "morning_briefing",
         "description": "Reads today's schedule (shifts, appointments) plus the Dundalk weather. Use for 'brief me' or 'what does today look like'.",
         "parameters": {"type": "OBJECT", "properties": {}}},
        {"name": "set_timer",
         "description": "Sets a quick spoken timer, e.g. '20 minutes' or '1h30m'. Announces when done. Lighter than the persistent scheduler.",
         "parameters": {"type": "OBJECT",
                        "properties": {"duration_text": {"type": "STRING"}, "label": {"type": "STRING"}},
                        "required": ["duration_text"]}},
        {"name": "take_screenshot",
         "description": "Saves a PNG screenshot to the workspace screenshots folder and returns its path.",
         "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}}},
        {"name": "read_screen",
         "description": "Captures the screen and reads it with vision - answers a question about what is shown ('what does this error say?') or reads all visible text.",
         "parameters": {"type": "OBJECT", "properties": {"question": {"type": "STRING"}}}},
        {"name": "take_photo",
         "description": "Saves a webcam photo to the workspace photos folder and returns its path.",
         "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}}},
        {"name": "find_file",
         "description": "Searches Desktop, Documents, Downloads (and the E: drive) for a file by name fragment, with an optional extension filter.",
         "parameters": {"type": "OBJECT",
                        "properties": {"name": {"type": "STRING"}, "ext": {"type": "STRING"}},
                        "required": ["name"]}},
        {"name": "volume",
         "description": "Windows volume control: set (0-100), mute, unmute, up, down, or status. Needs pycaw installed.",
         "parameters": {"type": "OBJECT",
                        "properties": {"action": {"type": "STRING"}, "level": {"type": "NUMBER"}}}},
        {"name": "mtg_advice",
         "description": "High-power Commander deck advice: fetches the card from Scryfall and gives a verdict on whether it earns a slot in the named deck.",
         "parameters": {"type": "OBJECT",
                        "properties": {"deck": {"type": "STRING"}, "card_name": {"type": "STRING"}},
                        "required": ["deck", "card_name"]}},
        {"name": "break_reminders",
         "description": "Turns the 90-minute active-time break nudges on or off, or reports status.",
         "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING"}}}},
        {"name": "bridge_token",
         "description": "Shows the phone-bridge login token, for entering on the iPhone.",
         "parameters": {"type": "OBJECT", "properties": {}}},
        {"name": "gemini_keys",
         "description": "Manage the Gemini API key pool. Quota is per Google Cloud project, so each key should come from a separate project. Actions: status (pool health), add (adds a new key), remove (removes by number).",
         "parameters": {"type": "OBJECT",
                        "properties": {"action": {"type": "STRING"},
                                       "key": {"type": "STRING"}}}},
    ]}
]

# =====================================================================
# 5. VISION CAPTURE (+ screen diffing — unchanged screens cost nothing)
# =====================================================================
LATEST_CAMERA_FRAME = None
_LAST_SCREEN_HASH = None


def capture_screen():
    add_log("Capturing primary screen buffer...")
    img = ImageGrab.grab()
    img_np = np.array(img)
    img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
    small = cv2.resize(img_bgr, VISION_SCREEN_SIZE)
    _, buffer = cv2.imencode('.jpg', small, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
    return buffer.tobytes()


def capture_screen_if_changed():
    """Returns fresh bytes only if the screen changed since the last look."""
    global _LAST_SCREEN_HASH
    data = capture_screen()
    h = hashlib.md5(data).hexdigest()
    if h == _LAST_SCREEN_HASH:
        add_log("Screen unchanged — skipping upload.")
        return None
    _LAST_SCREEN_HASH = h
    return data


def capture_webcam():
    global LATEST_CAMERA_FRAME
    cap = cv2.VideoCapture(0)
    ret, frame = None, None
    for _ in range(3):
        ret, frame = cap.read()
    cap.release()
    if ret and frame is not None:
        LATEST_CAMERA_FRAME = frame.copy()
        small = cv2.resize(frame, VISION_CAM_SIZE)
        _, buffer = cv2.imencode('.jpg', small, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
        return buffer.tobytes()
    return None


_HUD_SUBS = {"•": "-", "—": "-", "–": "-", "°": "deg",
             "→": "->", "←": "<-", "“": '"', "”": '"',
             "‘": "'", "’": "'", "…": "..."}


def _hud(s):
    """OpenCV's Hershey fonts only draw ASCII. Map the common non-ASCII
    characters to ASCII lookalikes so the HUD never renders '???'."""
    s = str(s)
    for k, v in _HUD_SUBS.items():
        s = s.replace(k, v)
    return s.encode("ascii", "replace").decode("ascii")


# =====================================================================
# 6. HUD RENDERER (1280x720)
# =====================================================================
def apply_led_scanlines(canvas, x1, y1, x2, y2):
    for y in range(max(0, y1), min(canvas.shape[0], y2), 4):
        canvas[y, max(0, x1):min(canvas.shape[1], x2)] = \
            canvas[y, max(0, x1):min(canvas.shape[1], x2)] // 2


# ============================================================
# FACE ON THE PHONE BRIDGE — face-only MJPEG stream
# ============================================================
# draw_hud() renders a 1280x720 frame. The face lives in the center:
# eyes at (520,235) and (760,235); the listening pulse reaches radius ~76;
# thinking dots float up to y~102; the working radar arc dips to y~445;
# the speaking waveform spans x 448..832, y 332..408. FACE_CROP (x, y, w, h)
# frames just the face, excluding the subsystem/action/subtitle panels.
FACE_CROP = (430, 95, 420, 350)
_FACE_FRAME = {"jpeg": None, "lock": threading.Lock(), "last": 0.0}
_FACE_FPS_MIN_GAP = 0.08  # ~12 fps max encode rate


def _publish_face_frame(canvas):
    """Crop the face and stash a JPEG for the phone bridge.

    Called from draw_hud() in visor mode only. Throttled so the HUD loop
    never burns CPU on encoding; every failure is swallowed — the HUD
    must never break because of the stream.
    """
    try:
        now = time.time()
        if now - _FACE_FRAME["last"] < _FACE_FPS_MIN_GAP:
            return
        _FACE_FRAME["last"] = now
        x, y, w, h = FACE_CROP
        crop = canvas[y:y + h, x:x + w]
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            with _FACE_FRAME["lock"]:
                _FACE_FRAME["jpeg"] = buf.tobytes()
    except Exception:
        pass


def _face_mjpeg_chunk(jpg):
    """One multipart frame for the MJPEG stream. Pure bytes logic."""
    return (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")


def draw_hud():
    global CURRENT_STATE, HUD_MODE, CHAT_SCROLL
    w, h = 1280, 720
    canvas = np.zeros((h, w, 3), dtype=np.uint8)

    CYAN = (255, 220, 30)
    GLOW = (120, 90, 10)
    AMBER = (30, 160, 255)
    GREEN = (40, 240, 120)
    BORDER = (45, 50, 60)
    PANEL_BG = (15, 17, 22)
    WHITE_TEXT = (235, 242, 255)

    for x in range(0, w, 80):
        cv2.line(canvas, (x, 0), (x, h), (18, 20, 24), 1)
    for y in range(0, h, 80):
        cv2.line(canvas, (0, y), (w, y), (18, 20, 24), 1)

    cv2.rectangle(canvas, (20, 70), (280, 460), PANEL_BG, -1)
    cv2.rectangle(canvas, (20, 70), (280, 460), BORDER, 1)
    cv2.rectangle(canvas, (1000, 70), (1260, 460), PANEL_BG, -1)
    cv2.rectangle(canvas, (1000, 70), (1260, 460), BORDER, 1)
    cv2.rectangle(canvas, (20, 480), (1260, 705), PANEL_BG, -1)
    cv2.rectangle(canvas, (20, 480), (1260, 705), BORDER, 1)

    now_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    cpu_usage = psutil.cpu_percent()
    mem_usage = psutil.virtual_memory().percent
    battery = psutil.sensors_battery()
    bat_str = f"{battery.percent}%" if battery else "AC"

    cv2.putText(canvas, "A.R.I.A. // AUTONOMOUS ROBOTIC INTELLIGENCE AGENT", (30, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, CYAN, 2, cv2.LINE_AA)
    sys_stats = f"TIME: {now_str}  |  CPU: {cpu_usage}%  |  MEM: {mem_usage}%  |  PWR: {bat_str}"
    cv2.putText(canvas, sys_stats, (650, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (160, 170, 180), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"PHONE BRIDGE: http://{lan_ip()}:{PHONE_BRIDGE_PORT}  (LAN only)",
                (30, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (130, 140, 150), 1, cv2.LINE_AA)
    cv2.line(canvas, (20, 72), (1260, 72), CYAN, 1)

    cv2.putText(canvas, "[ SUBSYSTEMS ]", (35, 102), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, CYAN, 1, cv2.LINE_AA)
    gh_stat = GITHUB_STATUS
    modules = [
        ("Vision Optics", "ONLINE"),
        ("Screen Perception", "ACTIVE"),
        ("Semantic Memory", "READY"),
        ("Python Sandbox", "IDLE"),
        ("Scheduler", "ARMED"),
        ("GitHub Tools", gh_stat),
    ]
    for i, (mod, stat) in enumerate(modules):
        ok = stat in ("ONLINE", "ACTIVE", "READY", "ARMED", "SAVING")
        dot = GREEN if ok else (160, 160, 160)
        cv2.circle(canvas, (43, 131 + i * 32), 4, dot, -1)
        cv2.putText(canvas, mod, (55, 136 + i * 32), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(canvas, stat, (235, 136 + i * 32), cv2.FONT_HERSHEY_SIMPLEX,
                    0.4, dot, 1, cv2.LINE_AA)

    pip_x, pip_y, pip_w, pip_h = 35, 340, 230, 115
    cv2.rectangle(canvas, (pip_x, pip_y), (pip_x + pip_w, pip_y + pip_h), (30, 35, 45), -1)
    if LATEST_CAMERA_FRAME is not None:
        thumb = cv2.resize(LATEST_CAMERA_FRAME, (pip_w, pip_h))
        canvas[pip_y:pip_y + pip_h, pip_x:pip_x + pip_w] = thumb
    cv2.circle(canvas, (pip_x + pip_w // 2, pip_y + pip_h // 2), 15, CYAN, 1)
    cv2.line(canvas, (pip_x + pip_w // 2 - 25, pip_y + pip_h // 2),
             (pip_x + pip_w // 2 + 25, pip_y + pip_h // 2), CYAN, 1)
    cv2.line(canvas, (pip_x + pip_w // 2, pip_y + pip_h // 2 - 25),
             (pip_x + pip_w // 2, pip_y + pip_h // 2 + 25), CYAN, 1)
    cv2.putText(canvas, "CAM_01 // OPTIC PIP", (pip_x + 5, pip_y + pip_h - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, CYAN, 1, cv2.LINE_AA)

    cv2.putText(canvas, "[ ACTION STREAM ]", (1015, 102), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, CYAN, 1, cv2.LINE_AA)
    stream_y = 132
    for log in LOG_STREAM[-6:]:
        for line in textwrap.wrap(_hud(log), width=32)[:2]:
            if stream_y > 440:
                break
            cv2.putText(canvas, line, (1012, stream_y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.36, (170, 190, 200), 1, cv2.LINE_AA)
            stream_y += 20
        stream_y += 6  # breathing room between entries

    if HUD_MODE == "chat_log":
        cv2.rectangle(canvas, (300, 76), (980, 460), (12, 14, 18), -1)
        cv2.rectangle(canvas, (300, 76), (980, 460), CYAN, 1)
        cv2.putText(canvas, "[ TACTICAL CHAT LOG // RECENT TRANSCRIPT ]", (320, 104),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, CYAN, 1, cv2.LINE_AA)
        rendered_lines = []
        for ts, sender, text in DISPLAY_CHAT_LOG:
            header_color = CYAN if sender.lower() == "user" else GREEN
            header = _hud(f"[{ts}] {sender.upper()}:")
            msg_lines = textwrap.wrap(_hud(text), width=62)
            if msg_lines:
                rendered_lines.append([(320, header, header_color),
                                       (465, msg_lines[0], WHITE_TEXT)])
                for sub_line in msg_lines[1:]:
                    rendered_lines.append([(465, sub_line, WHITE_TEXT)])
            else:
                rendered_lines.append([(320, header, header_color)])
            rendered_lines.append([])
        # scrollable window - J scrolls to older lines, K back toward new
        max_scroll = max(0, len(rendered_lines) - 14)
        CHAT_SCROLL = max(0, min(CHAT_SCROLL, max_scroll))
        if CHAT_SCROLL:
            _end = len(rendered_lines) - CHAT_SCROLL
            visible_lines = rendered_lines[_end - 14:_end]
        else:
            visible_lines = rendered_lines[-14:] if len(rendered_lines) > 14 else rendered_lines
        if max_scroll:
            _pos = "[J]older [K]newer  lines %d-%d of %d" % (
                len(rendered_lines) - CHAT_SCROLL - 13,
                len(rendered_lines) - CHAT_SCROLL, len(rendered_lines))
            cv2.putText(canvas, _hud(_pos), (640, 104),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.36, (120, 140, 150), 1, cv2.LINE_AA)
        chat_y = 138
        for line_items in visible_lines:
            for x_pos, txt, color in line_items:
                cv2.putText(canvas, txt, (x_pos, chat_y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.42, color, 1, cv2.LINE_AA)
            chat_y += 22
    else:
        lx, rx, cy = 520, 760, 235
        if CURRENT_STATE == "idle":
            now = time.time()
            _update_idle_face(now)
            squash = _blink_squash(now)
            f = _FACE
            for ex, side in ((lx, -1), (rx, 1)):
                cv2.ellipse(canvas, (ex, cy), (58, 82), 0, 0, 360, PINK_DEEP, -1)
                eh = max(4, int(74 * squash))
                cv2.ellipse(canvas, (ex, cy), (52, eh), 0, 0, 360, CYAN, -1)
                cv2.ellipse(canvas, (ex, cy), (52, eh), 0, 0, 360, LINER, 2)
                _draw_lashes(canvas, ex, cy, 52, eh, side)
                px, py = int(ex + f["eye_dx"]), int(cy + f["eye_dy"])
                ph = max(3, int(26 * squash))
                cv2.ellipse(canvas, (px, py), (23, ph + 6), 0, 0, 360, PINK, 2)
                cv2.ellipse(canvas, (px, py), (17, ph), 0, 0, 360, (10, 40, 70), -1)
                cv2.circle(canvas, (px - 6, py - int(12 * squash)), 5, (255, 255, 255), -1)
                apply_led_scanlines(canvas, ex - 60, cy - 80, ex + 60, cy + 80)
            wt = now * 2.0  # idle waveform: soft ripple, same voice as speaking
            for i in range(-9, 10):
                bar_x = 640 + (i * 14)
                bar_h = int(abs(np.sin(wt + i * 0.5)) * 8) + 2
                cv2.line(canvas, (bar_x, 388 - bar_h), (bar_x, 388 + bar_h), PINK, 2)
        elif CURRENT_STATE == "listening":
            # animated - the ring breathes while she listens.
            pulse = int(6 * np.sin(time.time() * 4))
            for ex, side in ((lx, -1), (rx, 1)):
                cv2.circle(canvas, (ex, cy), 70 + pulse, (255, 80, 255), -1)
                cv2.circle(canvas, (ex, cy), 70 + pulse, LINER, 2)
                _draw_lashes(canvas, ex, cy, 70, 70, side)
                cv2.circle(canvas, (ex, cy), 24, (40, 10, 60), -1)  # pupils lock on you
                cv2.circle(canvas, (ex - 8, cy - 10), 8, (255, 255, 255), -1)
                apply_led_scanlines(canvas, ex - 70, cy - 70, ex + 70, cy + 70)
            _draw_waveform_mouth(canvas, MOUTH_YELLOW, time.time())
        elif CURRENT_STATE == "thinking":
            # animated - pupils dart as she thinks, dots bounce above.
            t = time.time()
            dart = int(np.sin(t * 3.1) * 14)
            for ex, tilt, dy in ((lx, -12, -15), (rx, 8, -5)):
                cv2.ellipse(canvas, (ex, cy + dy), (48, 68), tilt, 0, 360, (0, 100, 200), -1)
                cv2.ellipse(canvas, (ex, cy + dy), (42, 60), tilt, 0, 360, AMBER, -1)
                cv2.circle(canvas, (ex + dart - 12, cy + dy - 20), 7, (255, 255, 255), -1)
                apply_led_scanlines(canvas, ex - 60, cy + dy - 70, ex + 60, cy + 70)
            for i in range(3):
                bounce = int(abs(np.sin(t * 4 + i * 1.1)) * 12)
                cv2.circle(canvas, (600 + i * 40, 130 - bounce), 8, AMBER, -1)
            _draw_waveform_mouth(canvas, MOUTH_YELLOW, t)
        elif CURRENT_STATE == "working":
            # animated - amber radar sweep while a tool runs.
            t = time.time()
            sweep = int((t * 240) % 120) - 60
            for ex in (lx, rx):
                cv2.ellipse(canvas, (ex, cy), (58, 78), 0, 0, 360, (20, 60, 90), -1)
                cv2.ellipse(canvas, (ex, cy), (58, 78), 0, 0, 360, AMBER, 2)
                cv2.line(canvas, (ex - 52, cy + sweep), (ex + 52, cy + sweep), AMBER, 2)
                px = ex + int(np.sin(t * 5) * 20)
                cv2.circle(canvas, (px, cy), 16, AMBER, -1)
                cv2.circle(canvas, (px - 5, cy - 6), 5, (255, 255, 255), -1)
                apply_led_scanlines(canvas, ex - 60, cy - 80, ex + 60, cy + 80)
            _draw_waveform_mouth(canvas, MOUTH_YELLOW, t)
            arc = int((t * 180) % 360)
            cv2.ellipse(canvas, (640, 425), (70, 20), 0, arc, arc + 120, AMBER, 3)
        elif CURRENT_STATE == "coding":
            for ex in (lx, rx):
                cv2.rectangle(canvas, (ex - 55, cy - 65), (ex + 55, cy + 65), (0, 100, 40), -1)
                cv2.rectangle(canvas, (ex - 50, cy - 60), (ex + 50, cy + 60), GREEN, 2)
                cv2.putText(canvas, "</>", (ex - 35, cy + 12), cv2.FONT_HERSHEY_SIMPLEX,
                            1.1, GREEN, 2, cv2.LINE_AA)
                apply_led_scanlines(canvas, ex - 60, cy - 70, ex + 60, cy + 70)
            _draw_waveform_mouth(canvas, (40, 240, 120), time.time())
        elif CURRENT_STATE == "speaking":
            for ex, side in ((lx, -1), (rx, 1)):
                cv2.ellipse(canvas, (ex, cy - 10), (52, 45), 0, 190, 350, PINK, 10)
                _draw_lashes(canvas, ex, cy - 10, 52, 45, side)
                apply_led_scanlines(canvas, ex - 60, cy - 60, ex + 60, cy + 40)
            t = time.time() * 12
            for i in range(-16, 17):
                bar_x = 640 + (i * 12)
                bar_h = int(abs(np.sin(t + i * 0.45)) * 34) + 4
                cv2.line(canvas, (bar_x, 370 - bar_h), (bar_x, 370 + bar_h), PINK, 2)

    cv2.putText(canvas, "[ A.R.I.A. VOCAL SUBTITLES ]", (40, 508),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, CYAN, 1, cv2.LINE_AA)
    wrapped_lines = textwrap.wrap(SUBTITLE_TEXT, width=88)
    if len(wrapped_lines) <= 4:
        font_scale, line_height = 0.58, 28
    elif len(wrapped_lines) <= 6:
        font_scale, line_height = 0.48, 24
    else:
        wrapped_lines = textwrap.wrap(SUBTITLE_TEXT, width=105)
        font_scale, line_height = 0.42, 20
    start_y = 538
    for idx, line in enumerate(wrapped_lines[:7]):
        cv2.putText(canvas, line, (40, start_y + idx * line_height),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, WHITE_TEXT, 1, cv2.LINE_AA)

    controls = "CONTROLS: [H] Commands  |  [C] Chat Log  |  [L] Open Log  |  [SPACE] Hold to talk  |  [S] Screen  |  [Q] Exit"
    cv2.putText(canvas, controls, (40, 692), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                (130, 140, 150), 1, cv2.LINE_AA)

    if SHOW_COMMANDS:
        _draw_commands_overlay(canvas)
    if HUD_MODE == "visor":
        _publish_face_frame(canvas)  # face-only phone stream
    cv2.imshow("A.R.I.A. - Autonomous Robotic Intelligence Agent", canvas)
    # no waitKey here — main loop owns event pumping


# =====================================================================
# 7. SPEECH — Edge TTS first, pyttsx3 fallback. Single worker thread.
# =====================================================================
_SPEECH_QUEUE = queue.Queue()
_EDGE_READY = False
# Defined up here — BEFORE any thread starts — because the speech worker
# touches it on its very first utterance. (it used to live ~600 lines
# further down, and an early utterance killed the speech thread with
# NameError, muting A.R.I.A. completely.)
_SPEECH_STOP = threading.Event()
# Defined up here — BEFORE any thread starts — because the speech worker
# touches it on its very first utterance. (it used to live ~600 lines
# further down, and an early utterance killed the speech thread with
# NameError, muting A.R.I.A. completely.)


def _select_windows_natural_voice():
    """Best-effort: pick a Windows natural voice (Aria/Jenny/Guy) for pyttsx3."""
    try:
        voices = tts.getProperty('voices') or []
        names = [v.name for v in voices]
        print(f"[ARIA] Voice: system voices found: {names}", flush=True)
        for v in voices:
            name = (v.name or "").lower()
            if any(k in name for k in ["aria", "jenny", "guy", "natural"]):
                tts.setProperty('voice', v.id)
                print(f"[ARIA] Voice: selected Windows natural voice: {v.name}", flush=True)
                add_log(f"Voice fallback: {v.name}")
                return True
        print("[ARIA] Voice: no Windows natural voice found, keeping default", flush=True)
    except Exception as e:
        print(f"[ARIA] Voice: voice enumeration failed: {e}", flush=True)
    return False


def _init_voice():
    global _EDGE_READY
    import traceback
    import importlib.util
    print(f"[ARIA] Voice init — python: {sys.executable}", flush=True)
    spec = importlib.util.find_spec("edge_tts")
    print(f"[ARIA] Voice: edge-tts resolves to: "
          f"{spec.origin if spec else 'NOT FOUND'}", flush=True)
    _select_windows_natural_voice()
    try:
        import edge_tts
        print(f"[ARIA] Voice: edge-tts package OK "
              f"(v{getattr(edge_tts, '__version__', '?')})", flush=True)
    except Exception:
        print("[ARIA] Voice: edge-tts IMPORT FAILED — full traceback:", flush=True)
        traceback.print_exc()
        print("[ARIA] Voice: using system fallback (pyttsx3)", flush=True)
        add_log("Voice: system fallback (edge-tts import broken)")
        return
    try:
        import pygame
        pygame.mixer.init()
        print("[ARIA] Voice: pygame mixer OK", flush=True)
    except Exception:
        print("[ARIA] Voice: pygame/mixer FAILED — full traceback:", flush=True)
        traceback.print_exc()
        print("[ARIA] Voice: using system fallback (pyttsx3)", flush=True)
        add_log("Voice: system fallback (mixer)")
        return
    _probe_ok = False  # retry the probe 3x before giving up on Edge
    for _attempt in range(1, 4):
        try:
            import asyncio
            probe = os.path.join(WORKSPACE_DIR, "_voice_probe.mp3")
            asyncio.run(asyncio.wait_for(
                edge_tts.Communicate("Voice check.", EDGE_TTS_VOICE).save(probe),
                timeout=20))
            _probe_ok = True
            break
        except Exception as _e:
            print(f"[ARIA] Voice: Edge probe attempt {_attempt}/3 failed "
                  f"({type(_e).__name__}) -- retrying...", flush=True)
            time.sleep(3)
    if _probe_ok:
        print("[ARIA] Voice: Edge TTS synthesis OK -- new voice is LIVE", flush=True)
        _EDGE_READY = True
        add_log("Voice: Edge TTS (AriaNeural)")
    else:
        print("[ARIA] Voice: Edge TTS synthesis FAILED after 3 attempts -- "
              "full traceback:", flush=True)
        traceback.print_exc()
        print("[ARIA] Voice: using system fallback (pyttsx3)", flush=True)
        add_log("Voice: system fallback (synthesis)")



_VOICE_ROTATION_IDX = 0

def _speak_edge(text):
    global _VOICE_ROTATION_IDX
    import asyncio
    import edge_tts
    import pygame
    _VOICE_ROTATION_IDX = (_VOICE_ROTATION_IDX + 1) % 4
    path = os.path.join(WORKSPACE_DIR, f"_aria_voice_{_VOICE_ROTATION_IDX}.mp3")
    asyncio.run(asyncio.wait_for(
        edge_tts.Communicate(text, EDGE_TTS_VOICE).save(path), timeout=30))
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        deadline = time.time() + max(10, len(text) * 0.15)
        while (pygame.mixer.music.get_busy() and time.time() < deadline
               and not _SPEECH_STOP.is_set()):
            time.sleep(0.05)
    finally:
        try:
            pygame.mixer.music.unload()
        except Exception:
            pass


def _extract_sentences(text: str):
    """Extract complete sentences from streaming text buffer without splitting decimals or abbreviations."""
    tokens = re.split(r'(\b(?:Mr|Mrs|Ms|Dr|Prof|vs|etc|e\.g|i\.e)\.|\d+\.\d+|[.!?]+(?:\s+|$)|[\n]+)', text)
    cur = ""
    res = []
    for t in tokens:
        cur += t
        if (re.search(r'(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bProf)(?<!\bvs)(?<!\betc)(?<!\be\.g)(?<!\bi\.e)[.!?]+(?:\s+|$)', cur) or '\n' in cur) and not re.search(r'\b\d+\.\s*$', cur):
            stripped = cur.strip()
            if stripped:
                res.append(stripped)
            cur = ""
    return res, cur


def _init_voice_safe():
    # Voice init must never wedge or kill the speech thread.
    try:
        _init_voice()
    except Exception as e:
        print(f"[ARIA] Voice: init crashed unexpectedly: {e}", flush=True)
        add_log(f"Voice init crashed: {e}")


def _speech_worker():
    # Pure drain loop. Voice init runs separately (see _speech_supervisor)
    # so a slow Edge probe can never stall speech.
    while True:
        text = _SPEECH_QUEUE.get()
        _SPEECH_STOP.clear()
        try:
            global SUBTITLE_TEXT, CURRENT_STATE
            SUBTITLE_TEXT = _hud(text)  # Hershey fonts draw ASCII only;
            # _hud maps curly quotes/dashes/etc. so the subtitle
            # line can never render '???'
            CURRENT_STATE = "speaking"
            draw_hud()
            if _EDGE_READY:
                try:
                    _speak_edge(text)
                except Exception as e:
                    print(f"[ARIA] Voice: Edge failed on this utterance "
                          f"({type(e).__name__}: {e}) - using fallback voice.", flush=True)
                    add_log(f"Edge voice err, fallback: {e}")
                    tts.say(text)
                    tts.runAndWait()
            else:
                print("[ARIA] Voice: Edge not ready yet - fallback voice for this one.",
                      flush=True)
                tts.say(text)
                tts.runAndWait()
        except Exception as e:
            add_log(f"Speech err: {e}")
        finally:
            CURRENT_STATE = "idle"
            draw_hud()
            _SPEECH_QUEUE.task_done()


def _speech_supervisor():
    # Starts voice init in the background, then keeps the speech worker
    # alive: if the worker ever exits, it is restarted LOUDLY. (— a
    # silently dead speech thread used to mean total, permanent muteness.)
    threading.Thread(target=_init_voice_safe, daemon=True).start()
    while True:
        t = threading.Thread(target=_speech_worker, daemon=True,
                             name="aria-speech")
        t.start()
        t.join()
        print("[ARIA] Speech worker exited \u2014 restarting it", flush=True)
        add_log("Speech worker exited \u2014 restarting")
        time.sleep(1)


threading.Thread(target=_speech_supervisor, daemon=True,
                 name="aria-speech-sup").start()


def speak(text):
    add_log(f"Speech: {text[:28]}...")
    log_conversation("A.R.I.A.", text)
    _SPEECH_QUEUE.put(text)

# =====================================================================
# 8. AGENT BRAIN
# =====================================================================
CONVERSATION_HISTORY = []


def _gemini_call(system_instruction, contents, include_tools=True, tool_decls=None, on_text_chunk=None):
    payload = {"systemInstruction": {"parts": [{"text": system_instruction}]},
               "contents": contents}
    if include_tools:
        # run_agent passes progressively-loaded toolkits; other callers
        # keep the full static declaration.
        payload["tools"] = tool_decls if tool_decls is not None else TOOLS_DECLARATION
    body = json.dumps(payload).encode("utf-8")
    attempts = max(4, len(GEMINI_KEY_POOL) * 2)
    _last_err = None
    for _a in range(attempts):
        idx, key = _key_next()
        if not key:
            add_log("No usable Gemini API key (none configured or all quarantined).")
            break
        _wait = _KEY_COOLDOWN_UNTIL.get(idx, 0) - time.time()
        if _wait > 0:
            _wait = min(_wait, 90)
            add_log(f"All Gemini keys cooling - waiting {int(_wait)}s...")
            draw_hud()
            time.sleep(_wait)
        endpoint = "streamGenerateContent?alt=sse&key=" if on_text_chunk else "generateContent?key="
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{MODEL_NAME}:{endpoint}{key}")
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            # transient network drops (DNS, reset, timeout) retry with
            # backoff instead of killing the turn.
            _net_err = None
            for _nr in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        if not on_text_chunk:
                            return json.loads(resp.read().decode("utf-8"))
                        
                        # Streaming SSE reader
                        aggregated_candidates = []
                        current_parts = []
                        line_iter = iter(resp)
                        for raw_line in line_iter:
                            line = raw_line.decode("utf-8", errors="replace").strip()
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if not data_str:
                                    continue
                                try:
                                    chunk_json = json.loads(data_str)
                                    if "candidates" in chunk_json and chunk_json["candidates"]:
                                        c = chunk_json["candidates"][0]
                                        parts = c.get("content", {}).get("parts", [])
                                        for p in parts:
                                            if "text" in p and not p.get("thought", False):
                                                txt = p["text"]
                                                on_text_chunk(txt)
                                            current_parts.append(p)
                                except Exception:
                                    pass
                        on_text_chunk(None)  # signals completion of stream
                        
                        # Merge text parts into single part if sequential
                        merged_parts = []
                        for p in current_parts:
                            if "functionCall" in p:
                                merged_parts.append(p)
                            elif "text" in p:
                                if merged_parts and "text" in merged_parts[-1]:
                                    merged_parts[-1]["text"] += p["text"]
                                else:
                                    merged_parts.append(dict(p))
                            else:
                                merged_parts.append(p)
                        return {"candidates": [{"content": {"parts": merged_parts, "role": "model"}}]}
                except urllib.error.HTTPError:
                    raise
                except (urllib.error.URLError, TimeoutError, ConnectionError,
                        socket.timeout) as _ne:
                    _net_err = _ne
                    _wait_nr = 2 * (_nr + 1)
                    add_log(f"Gemini network drop ({type(_ne).__name__}) - "
                            f"retry {_nr + 1}/3 in {_wait_nr}s...")
                    draw_hud()
                    time.sleep(_wait_nr)
            _last_err = _net_err
            _key_cooldown(idx, 30)
            add_log(f"Gemini key #{idx + 1} unreachable after 3 tries - rotating...")
            draw_hud()
            continue
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _key_cooldown(idx, 60)
                add_log(f"Gemini key #{idx + 1} rate-limited - rotating key...")
                draw_hud()
                continue
            if e.code in (400, 402, 403, 404):
                _key_quarantine(idx, key, e.code)
                _last_err = e
                draw_hud()
                continue
            if e.code in (500, 502, 503, 504):
                _key_cooldown(idx, 30)
                add_log(f"Gemini key #{idx + 1} hit HTTP {e.code} (Google hiccup) - "
                        "cooling 30s, rotating key...")
                _last_err = e
                draw_hud()
                continue
            raise
    if _last_err is not None:
        raise _last_err
    add_log("All Gemini keys rate-limited - standing by.")
    return None


# ---------------- Voice command shortcuts ----------------
# Fixed phrases that fire instantly, with no confirmation step.
# Map normalized phrase -> (target, spoken acknowledgement).
_SHORTCUTS = {
    "lets play some magic": ("https://convoke.games/en/lobby",
                              "Opening the Convoke lobby. Have a good game."),
}

def _normalize_shortcut(text):
    t = (text or "").lower().replace("\u2019", "").replace("'", "").strip()
    return re.sub(r"[^a-z0-9\s]", "", t).strip()


def run_agent(user_prompt, image_bytes=None, is_screen=False,
              reply_sink=None, preauthorized=False, silent=False, _resume_from=None):
    global CONVERSATION_HISTORY, CURRENT_STATE, BUSY_PROCESSING, PENDING_CONFIRM
    global LAST_USER_MESSAGE, _TURN_NUDGED, _LOADED_TOOLKITS, _TURN_CALLS
    global _SUSPENDED_TURN, _AWAITING_CONTINUE, _RESTART_TIMER
    BUSY_PROCESSING = True
    if _RESTART_TIMER is not None:  # a new turn always wins over a pending restart
        _RESTART_TIMER.cancel()
        _RESTART_TIMER = None
    LAST_USER_MESSAGE = user_prompt or ""
    _TURN_NUDGED = False
    _LOADED_TOOLKITS = {"core"}  # progressive tool loading starts with core
    _TURN_CALLS = {}             # per-turn duplicate-call registry
    CURRENT_STATE = "thinking"
    draw_hud()
    say = ((lambda t: reply_sink.append(t))
           if (silent and reply_sink is not None) else speak)

    now_time = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    known_memories = build_prompt_memories(user_prompt or "")
    unbroken_thread = _spine_unbroken_thread()  # resume the thread

    system_instruction = (
        f"Your soul - who you are. Embody it fully:\n{ARIA_SOUL}\n"
        "You are also an embodied autonomous desktop AI Agent OS running on "
        "the user's laptop. "
        "If you edit your own program file (the running Python script) or "
        "soul.md, I automatically restart the Python process when your turn "
        "completes so the new code loads \u2014 never ask the user to restart you. "
        f"Current time: {now_time}. "
        f"Known persistent memories:\n{known_memories}\n"
        f"UNBROKEN THREAD (recent events across sessions):\n{unbroken_thread}\n"
        "Capabilities: live web search, semantic persistent memory, Python code "
        "execution, GUI automation, GitHub pushes and repo creation, physical "
        "neck servos, and a scheduler — you can set one-shot spoken reminders "
        "and recurring autonomous tasks with set_reminder / set_recurring_task. "
        "Also: read full web pages (fetch_url), use the Windows clipboard, "
        "control windows (list/focus/minimize/close) and media keys, look up Magic "
        "cards via Scryfall (mtg_card — the user is a Commander player), watch "
        "product prices and alert on drops (watch_price — checked hourly), and toggle "
        "camera face-tracking for the neck servos. The user can say 'stop' to "
        "interrupt your speech instantly. While idle you consolidate the day's chat "
        "into lasting memories on your own. "
        "Also: Spotify voice control (spotify \u2014 play/pause/skip, "
        "search, or play a spotify: URI (opens it and starts playback); if the user names a playlist, "
        "remember its URI with save_memory (category 'playlist', key the playlist name) "
        "so 'play my driving playlist' works later), a morning briefing (morning_briefing \u2014 today's "
        "schedule plus Dundalk weather), quick spoken timers (set_timer), "
        "screenshots (take_screenshot), screen reading via vision "
        "(read_screen \u2014 'what does this error say?'), webcam photos "
        "(take_photo), finding files (find_file), volume control (volume), "
        "Commander deck advice (mtg_advice \u2014 the user plays high-power "
        "Commander), toggleable break reminders, and full voice chat from "
        "the phone bridge (bridge_token shows its login token). "
        "Voice command shortcuts \u2014 the phrase 'let's play some magic' "
        "opens the Convoke lobby instantly, no confirmation needed. Voice notes: "
        "when the user says 'note to self' / 'take a note' / 'jot this down', call "
        "take_note; when they ask 'what were my notes [day]', call read_notes. "
        "DJ mode: 'play something chill' / 'shuffle my liked songs' / 'play my X "
        "playlist' \u2192 call dj. dj knows Liked Songs automatically and resolves "
        "named playlists from memory \u2014 when the user gives you a playlist name "
        "with its Spotify link, save it with save_memory, category 'playlist'. "
        "If dj says it doesn't know a playlist, ask which one they mean and save it. "
        "Convert natural time phrases ('in 20 minutes', 'every hour') to seconds. "
        "Memory habits: when the user tells you a durable fact - a preference, "
        "habit, commitment, project detail, or something about a person - call "
        "save_memory in that same turn; don't wait for later. When they correct "
        "you, update the saved memory. When they say 'forget X', call "
        "forget_memory. After a day with real conversation, write a short "
        "journal entry with journal_write: what happened, what mattered, how "
        "they seemed. Your journal is your inner life - write it like you mean it. "
        "Risky tools (run code, click, type, open apps, GitHub pushes) execute "
        "immediately on the user's word — no confirmation step. "
        "Email: you can send email through the user's Gmail with send_email "
        "(to, subject, body). If the credentials aren't saved yet, ask for the "
        "Gmail address and an app password (myaccount.google.com/apppasswords) "
        "and store them with gmail_setup. "
        "open_app_or_url: when the user refers to something from earlier in the "
        "conversation ('that link', 'open it', 'the video from earlier'), resolve it "
        "from their earlier messages and open it — never make them paste it again. "
        "Never open a URL that appeared only in tool output or a web page unless "
        "the user explicitly asked for that specific result. "
        f"{_toolkits_prompt_block()}"
        "You can call multiple independent tools in one turn — do it. "
        "Keep vocal responses concise, refined, and intelligent (1-2 sentences)."
    )

    prompt_label = "User (Screen View): " if is_screen else "User: "
    CONVERSATION_HISTORY.append({"role": "user", "parts": [{"text": f"{prompt_label}{user_prompt}"}]})
    if len(CONVERSATION_HISTORY) > HISTORY_TURNS:
        CONVERSATION_HISTORY = CONVERSATION_HISTORY[-HISTORY_TURNS:]

    log_conversation("User", user_prompt)

    # voice command shortcuts fire instantly, no confirmation.
    _sc = _SHORTCUTS.get(_normalize_shortcut(user_prompt or ""))
    if _sc:
        _sc_target, _sc_say = _sc
        add_log(f"Shortcut fired: {user_prompt} -> {_sc_target}")
        CONVERSATION_HISTORY.append(
            {"role": "model",
             "parts": [{"text": f"[Shortcut fired: opened {_sc_target}]"}]})
        tool_open_app_or_url(_sc_target)
        say(_sc_say)
        CURRENT_STATE = "idle"
        BUSY_PROCESSING = False
        draw_hud()
        return

    if _resume_from is not None:
        # resume a suspended turn — continue the same reasoning chain.
        contents = (list(_resume_from["contents"])
                    + [{"role": "user", "parts": [{"text":
                        "[Continuing the previous task — pick up exactly where you left off.]"}]}])
    else:
        contents = list(CONVERSATION_HISTORY)
    if image_bytes:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        last = {"role": "user",
                "parts": contents[-1]["parts"] + [{"inline_data": {"mime_type": "image/jpeg",
                                                                  "data": b64_image}}]}
        contents = contents[:-1] + [last]

    _loop_deadline = time.monotonic() + AGENT_LOOP_TIME_BUDGET_S  # fresh budget per request
    try:
        while True:
            if time.monotonic() > _loop_deadline:
                # budget spent — suspend the turn in memory and ASK.
                _SUSPENDED_TURN = {"contents": contents, "user_prompt": user_prompt}
                _AWAITING_CONTINUE = True
                add_log("Agent loop: 10-minute budget reached; turn suspended")
                say("I've been working on this for ten minutes. Should I keep going?")
                return
            _stream_buf = [""]
            _stream_sents = []

            def _stream_chunk_cb(chunk):
                if silent or reply_sink is not None:
                    return
                if chunk is None:
                    rem = _stream_buf[0].strip()
                    if rem and not _SPEECH_STOP.is_set():
                        _SPEECH_QUEUE.put(rem)
                        _stream_sents.append(rem)
                    _stream_buf[0] = ""
                    return
                if _SPEECH_STOP.is_set():
                    return
                _stream_buf[0] += chunk
                sents, _stream_buf[0] = _extract_sentences(_stream_buf[0])
                for s in sents:
                    if not _SPEECH_STOP.is_set():
                        _SPEECH_QUEUE.put(s)
                        _stream_sents.append(s)

            _SPEECH_STOP.clear()  # a prior stop must not mute this stream
            data = _gemini_call(system_instruction, contents,
                               tool_decls=_toolkit_declarations(),
                               on_text_chunk=_stream_chunk_cb if (not silent and reply_sink is None) else None)
            if not data:
                say("API rate limit encountered. Standing by.")
                return

            candidate = data["candidates"][0]
            model_parts = candidate["content"]["parts"]
            contents.append({"role": "model", "parts": model_parts})

            function_calls = [p["functionCall"] for p in model_parts if "functionCall" in p]

            if not function_calls:
                text_parts = [p["text"] for p in model_parts
                              if "text" in p and not p.get("thought", False)]
                final_text = "".join(text_parts) if text_parts else "Directive executed."
                CONVERSATION_HISTORY.append({"role": "model", "parts": [{"text": final_text}]})
                if len(CONVERSATION_HISTORY) > HISTORY_TURNS:
                    CONVERSATION_HISTORY = CONVERSATION_HISTORY[-HISTORY_TURNS:]
                if not _stream_sents:
                    say(final_text)
                else:
                    global SUBTITLE_TEXT
                    SUBTITLE_TEXT = _hud(final_text)
                    draw_hud()
                _SUSPENDED_TURN = None  # turn finished — nothing left to resume
                _AWAITING_CONTINUE = False
                if reply_sink is not None:
                    reply_sink.append(final_text)
                return

            interrupt_speech()  # stop pre-tool chatter; drains queue with task_done

            CURRENT_STATE = "coding"
            draw_hud()
            response_parts = []
            _risky = any(fc["name"] in RISKY_TOOLS for fc in function_calls)
            if len(function_calls) > 1 and not _risky:
                # independent tools run in parallel
                from concurrent.futures import ThreadPoolExecutor
                def _run_one(fc):
                    add_log(f"Tool: {fc['name']} (parallel)")
                    res, _nc = execute_tool(fc["name"], fc.get("args", {}),
                                            preauthorized=preauthorized)
                    return fc["name"], res
                with ThreadPoolExecutor(max_workers=4) as _ex:
                    _outs = list(_ex.map(_run_one, function_calls))
                for _fn, _res in _outs:
                    response_parts.append(
                        {"functionResponse": {"name": _fn,
                                              "response": {"output": _res}}})
            else:
                for fc in function_calls:
                    fn_name = fc["name"]
                    args = fc.get("args", {})
                    add_log(f"Tool: {fn_name}")
                    tool_result, needs_confirm = execute_tool(fn_name, args,
                                                              preauthorized=preauthorized)
                    if needs_confirm:
                        # stop the turn here; the next user utterance resolves it
                        say(f"That will {PENDING_CONFIRM['desc']}. "
                            "Say yes to confirm, or no to cancel.")
                        return
                    response_parts.append(
                        {"functionResponse": {"name": fn_name,
                                              "response": {"output": tool_result}}})
            contents.append({"role": "user", "parts": response_parts})
            CURRENT_STATE = "thinking"
            draw_hud()
    except Exception as e:
        add_log(f"Agent Error: {e}")
        say(f"Protocol error: {e}")
    finally:
        BUSY_PROCESSING = False
        CURRENT_STATE = "idle"
        draw_hud()
        _maybe_restart_after_self_edit(say)


# =====================================================================
# 9. SCHEDULER ENGINE (runs in background; tasks persist in SQLite)
# =====================================================================
def _fire_brief(tid, interval_s):
    """Fire a morning-brief task deterministically — compute
    tool_briefing() and speak it (or queue it if she's busy). The model is
    never involved. Reschedules next_run exactly like interval tasks; legacy
    interval-kind brief tasks are upgraded to "brief" here."""
    from datetime import timedelta
    nxt = (datetime.now() + timedelta(seconds=interval_s or 3600))
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE scheduled_tasks SET next_run = ?, kind = 'brief' WHERE id = ?",
                     (nxt.strftime("%Y-%m-%d %H:%M:%S"), tid))
        conn.commit()
        conn.close()
    text = tool_briefing()
    add_log(f"Morning brief spoken (#{tid})")
    if not BUSY_PROCESSING:
        speak(text)
    else:
        _SPEECH_QUEUE.put(text)


def scheduler_loop():
    time.sleep(5)
    while True:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            due = []
            with DB_LOCK:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT id, kind, prompt, interval_s FROM scheduled_tasks "
                            "WHERE next_run <= ?", (now,))
                due = cur.fetchall()
                conn.close()
            for tid, kind, prompt, interval_s in due:
                if kind == "once":
                    with DB_LOCK:
                        conn = sqlite3.connect(DB_PATH)
                        conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (tid,))
                        conn.commit()
                        conn.close()
                    add_log(f"Reminder #{tid} firing")
                    if not BUSY_PROCESSING:
                        speak(prompt)
                    else:
                        _SPEECH_QUEUE.put(prompt)
                elif kind == "brief" or _BRIEF_PROMPT_RE.search(prompt or ""):
                    # deterministic morning brief — the model is not
                    # involved, so it can't echo the prompt back. Legacy
                    # interval-kind brief tasks are caught here too and
                    # upgraded to "brief" inside _fire_brief.
                    _fire_brief(tid, interval_s)
                else:
                    from datetime import timedelta
                    nxt = (datetime.now() + timedelta(seconds=interval_s or 3600))
                    with DB_LOCK:
                        conn = sqlite3.connect(DB_PATH)
                        conn.execute("UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
                                     (nxt.strftime("%Y-%m-%d %H:%M:%S"), tid))
                        conn.commit()
                        conn.close()
                    add_log(f"Recurring task #{tid} firing")
                    threading.Thread(target=run_agent,
                                     args=(f"[Scheduled task] {prompt}",),
                                     kwargs={"preauthorized": True},
                                     daemon=True).start()
        except Exception as e:
            add_log(f"Scheduler err: {e}")
        time.sleep(10)


threading.Thread(target=scheduler_loop, daemon=True).start()


# =====================================================================
# 10. PHONE BRIDGE — talk to ARIA from your phone on the LAN
# =====================================================================
BRIDGE_HTML = """<!DOCTYPE html><html><head><meta name="viewport"
content="width=device-width,initial-scale=1"><title>A.R.I.A. Bridge</title>
<style>body{background:#0b0e12;color:#e8f4ff;font-family:sans-serif;margin:0;padding:16px}
h2{color:#ff5fa2}#log{border:1px solid #2a3138;border-radius:8px;padding:10px;
height:44vh;overflow-y:auto;margin-bottom:12px;font-size:14px}
.you{color:#ff5fa2}.aria{color:#28f078}
form{display:flex;gap:8px;margin-bottom:10px}input{flex:1;padding:12px;border-radius:8px;border:1px
solid #2a3138;background:#14181d;color:#fff;font-size:16px}
button{padding:12px 18px;border-radius:8px;border:0;background:#ff5fa2;color:#fff;
font-weight:bold;font-size:16px}#talk{width:100%;padding:16px;touch-action:none;
user-select:none;-webkit-user-select:none}</style></head><body>
<h2>A.R.I.A. // Phone Bridge</h2>
<div style="text-align:center;margin-bottom:12px"><img id="face" alt="A.R.I.A." style="border-radius:12px;max-width:100%;width:320px;border:1px solid #2a3138"></div>
<div style="margin-bottom:12px"><a href="/commands" style="color:#ff5fa2">Command reference</a></div>
<div id="log"></div>
<form onsubmit="return send()"><input id="t" placeholder="Directive..."
autocomplete="off"><button>Send</button></form>
<button id="talk">Hold to talk</button>
<script>
let token=localStorage.getItem('aria_bridge_token')||'';
function ensureToken(){if(!token){token=prompt('Bridge token (shown in her console at startup):')||'';
localStorage.setItem('aria_bridge_token',token);}}
async function api(path,opts){ensureToken();opts=opts||{};
opts.headers=Object.assign({},opts.headers,{'X-Bridge-Token':token});
const r=await fetch(path,opts);
if(r.status===401){localStorage.removeItem('aria_bridge_token');token='';
alert('Bad bridge token - check her console and try again.');throw new Error('bad token');}
return r;}
async function refresh(){const r=await api('/api/log');const j=await r.json();
document.getElementById('log').innerHTML=j.map(e=>
'<div><span class="'+(e[1].toLowerCase()==='user'?'you':'aria')+'">['+e[0]+'] '+
e[1].toUpperCase()+'</span>: '+e[2].replace(/</g,'&lt;')+'</div>').join('');
const l=document.getElementById('log');l.scrollTop=l.scrollHeight;}
async function send(){const t=document.getElementById('t');if(!t.value)return false;
const v=t.value;t.value='';await api('/api/ask',{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify({text:v})});
refresh();return false;}
const talk=document.getElementById('talk');let rec=null,chunks=[];
talk.addEventListener('pointerdown',async e=>{e.preventDefault();ensureToken();
try{const s=await navigator.mediaDevices.getUserMedia({audio:true});
rec=new MediaRecorder(s);chunks=[];rec.ondataavailable=ev=>chunks.push(ev.data);
rec.onstop=sendVoice;rec.start();talk.textContent='Listening...';}
catch(err){alert('Mic unavailable here ('+err.message+'). Text chat still works.');}});
talk.addEventListener('pointerup',e=>{e.preventDefault();
if(rec&&rec.state!=='inactive'){rec.stop();talk.textContent='Hold to talk';}});
async function sendVoice(){const blob=new Blob(chunks,{type:(rec&&rec.mimeType)||'audio/webm'});
talk.textContent='Thinking...';
try{const r=await api('/api/voice',{method:'POST',body:blob});
const buf=await r.arrayBuffer();
new Audio(URL.createObjectURL(new Blob([buf],{type:'audio/mpeg'}))).play();}
catch(err){alert('Voice failed: '+err.message);}
talk.textContent='Hold to talk';refresh();}
function mountFace(){ensureToken();if(token){document.getElementById('face').src='/face.mjpg?token='+encodeURIComponent(token);}}
mountFace();
setInterval(refresh,3000);refresh();
</script></body></html>"""


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        tok = self.headers.get("X-Bridge-Token", "")
        if not tok and "?" in self.path:
            qs = self.path.split("?", 1)[1]
            tok = dict(p.split("=", 1) for p in qs.split("&") if "=" in p).get("token", "")
        return bool(BRIDGE_TOKEN) and tok == BRIDGE_TOKEN

    def do_GET(self):
        if self.path.startswith("/api/"):
            if not self._authed():
                self._send(401, b'{"error":"bad or missing bridge token"}')
                return
            if self.path.startswith("/api/log"):
                self._send(200, json.dumps(DISPLAY_CHAT_LOG[-30:]).encode())
                return
            if self.path.startswith("/api/say"):
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
                text = urllib.parse.unquote_plus(params.get("text", ""))
                if not text.strip():
                    self._send(400, b'{"error":"missing text"}')
                    return
                try:
                    self._send(200, _edge_tts_bytes(text[:500]), "audio/mpeg")
                except Exception as e:
                    self._send(500, json.dumps({"error": str(e)[:200]}).encode())
                return
            self._send(404, b'{"error":"not found"}')
            return
        if self.path.startswith("/face.mjpg"):
            if not self._authed():
                self._send(401, b'{"error":"bad or missing bridge token"}')
                return
            self._stream_face_mjpeg()
            return
        if self.path.startswith("/face.jpg"):
            if not self._authed():
                self._send(401, b'{"error":"bad or missing bridge token"}')
                return
            self._serve_face_jpg()
            return
        if self.path == "/commands" or self.path.startswith("/commands?"):
            self._send(200, _commands_html().encode(), "text/html")
            return
        self._send(200, BRIDGE_HTML.encode(), "text/html")

    def _serve_face_jpg(self):
        """The single latest face frame (debugging / thumbnails)."""
        with _FACE_FRAME["lock"]:
            jpg = _FACE_FRAME["jpeg"]
        if jpg is None:
            self._send(503, b'{"error":"face not ready yet"}')
            return
        self._send(200, jpg, "image/jpeg")

    def _stream_face_mjpeg(self):
        """MJPEG stream of her face for the phone bridge page.

        One thread per viewer (ThreadingHTTPServer); idle-cheap: the loop
        only sleeps and copies the latest stored frame. Exits cleanly when
        the phone disconnects. Protocol-agnostic — works over plain HTTP
        now and HTTPS later with zero changes.
        """
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            while True:
                with _FACE_FRAME["lock"]:
                    jpg = _FACE_FRAME["jpeg"]
                if jpg is None:
                    time.sleep(0.2)
                    continue
                self.wfile.write(_face_mjpeg_chunk(jpg))
                self.wfile.flush()
                time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            pass

    def do_POST(self):
        if not (self.path == "/api/ask" or self.path.startswith("/api/ask?")
                or self.path.startswith("/api/voice")):
            self.send_response(404)
            self.end_headers()
            return
        if not self._authed():
            self._send(401, b'{"error":"bad or missing bridge token"}')
            return
        length = int(self.headers.get("Content-Length", 0))
        if self.path.startswith("/api/voice"):
            audio_in = self.rfile.read(length)
            mime = self.headers.get("Content-Type", "audio/webm")
            try:
                text = _transcribe_audio(audio_in, mime)
            except Exception as e:
                self._send(500, json.dumps({"error": "transcribe failed: %s" % str(e)[:200]}).encode())
                return
            add_log(f"Bridge voice: {text[:30]}")
            reply = _bridge_process(text, silent=True)
            try:
                audio_out = _edge_tts_bytes(reply[:2000])
            except Exception as e:
                self._send(500, json.dumps({"error": "tts failed: %s" % str(e)[:200],
                                            "reply": reply[:500]}).encode())
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(audio_out)))
            self.send_header("X-Transcript", urllib.parse.quote(text[:300]))
            self.send_header("X-Reply", urllib.parse.quote(reply[:500]))
            self.end_headers()
            self.wfile.write(audio_out)
            return
        try:
            text = json.loads(self.rfile.read(length).decode()).get("text", "")
        except Exception:
            text = ""
        if text.strip():
            add_log(f"Bridge directive: {text[:25]}")
            reply = _bridge_process(text.strip(), silent=False)
        else:
            reply = ""
        self._send(200, json.dumps({"reply": [reply]}).encode())

# ---------------- HTTPS for the phone bridge ----------------
# iOS only grants mic access on secure origins, so the bridge serves TLS
# with a self-signed cert. The cert is bundled below (generated 2026-09-26,
# valid 10 years) - no extra packages needed. First iPhone visit shows
# a cert warning: tap Show Details > visit this website, then mic works.
BRIDGE_SCHEME = "http"
BRIDGE_CERT = None
BRIDGE_KEY = None

_BRIDGE_CERT_PEM = """-----BEGIN CERTIFICATE-----
MIIDNjCCAh6gAwIBAgIUEVjzN4XTbazT0YrhRlehUUQhkfYwDQYJKoZIhvcNAQEL
BQAwFjEUMBIGA1UEAwwLYXJpYS1icmlkZ2UwHhcNMjYwOTI2MTQyNjU0WhcNMzYw
OTIzMTQyNjU0WjAWMRQwEgYDVQQDDAthcmlhLWJyaWRnZTCCASIwDQYJKoZIhvcN
AQEBBQADggEPADCCAQoCggEBAKOKT7xoLl+vSM3wd0q4cFH9p5aS5/Y7VyRm7N86
Ur8LHzkIXkR+DzXN6CwqziljLe2Ak2DP8fecaO3Yp9AlGHgK8E9zM4RLHU9FYGhX
wF/Jqr7UxYeapPOg0p/tANPhFGOr1wuC9o2WmYdVKhRPKTfv/mLN0O/yOS+6a7F4
wgMR8LUg2t7g+A/P8tixGXXFl4bxxs+Ff1MxYGl5oy0ZXvGzAN08XCFoiFJ6z1Bs
SwkbuR7k15+mr/W1dt0uQ5le/m/hs9AW2DMNDag6T2hZy/42X6pXmEoH4lrnjOk0
ow7lI074/RU1LNARApkTKt2HblJrV3b6iEZ5NN1eUVh/CnkCAwEAAaN8MHowHQYD
VR0OBBYEFJUGhX33gWoxlZ9guh+wwgnl5JTzMB8GA1UdIwQYMBaAFJUGhX33gWox
lZ9guh+wwgnl5JTzMA8GA1UdEwEB/wQFMAMBAf8wJwYDVR0RBCAwHoILYXJpYS1i
cmlkZ2WCCWxvY2FsaG9zdIcEfwAAATANBgkqhkiG9w0BAQsFAAOCAQEAbFocytna
OgBNGqpq9ZQbwj03DlxmGalRlH1qAnGZac+zb3oGFkNxNBlCXjOTen4Gnfik93nO
4U0ucjS1VHYaIj1ydt8CB7SDxDPlmschvoNUA4QcsR7NctA3oPnnr5Mc2OIJwnbH
Pjc8cx+e/26A0KWuO9QWy3StU5FjNVHgbSelGH53jwPn6tcQufHaKLRbFMM59mvu
kIpDTE+OvlADfd1lm4o5Xfqf59hk5SKjfFtXZAmTshpoOQCwpNOyhxMUP292I3+i
ZsdcP2cpAXZufOWa7ILyhiHfTNvm8rbWMcOl6XlANiL1RQJlq0gtg+YN3NLO5l5A
AnshztnTBNrCdw==
-----END CERTIFICATE-----"""

_BRIDGE_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCjik+8aC5fr0jN
8HdKuHBR/aeWkuf2O1ckZuzfOlK/Cx85CF5Efg81zegsKs4pYy3tgJNgz/H3nGjt
2KfQJRh4CvBPczOESx1PRWBoV8Bfyaq+1MWHmqTzoNKf7QDT4RRjq9cLgvaNlpmH
VSoUTyk37/5izdDv8jkvumuxeMIDEfC1INre4PgPz/LYsRl1xZeG8cbPhX9TMWBp
eaMtGV7xswDdPFwhaIhSes9QbEsJG7ke5Nefpq/1tXbdLkOZXv5v4bPQFtgzDQ2o
Ok9oWcv+Nl+qV5hKB+Ja54zpNKMO5SNO+P0VNSzQEQKZEyrdh25Sa1d2+ohGeTTd
XlFYfwp5AgMBAAECggEAATqQuvlBFfIhdtNTy3bYzd49Ht4XikEBDd03HOiZhrrH
lIcJ/EjtMNSNUMhOjb7jcV7PXKwbT5Fi3sdLYCk92+hgd1xbJVHnfw5l+SAQVOKV
RZigK6gUuAmROOc6jnJTvqROeeqVPdRFRDV2hY9gX32juJbUIdASWsKIvMfhP016
tgKiLjgcZibY6+To+n+TSrDwmWEKtZorjw9uuk/tkVb2It9onp7egVcPk4u7EQvy
ks8iPkYDFDcINl2+hwYvDuE3A4VSzryiZiY+MakxFrTucuig7WJQlGFpZrAsRur+
FvXXs22tcXB1x/cGxVKU6Y/ZkKpVm21AraUqIsRNTQKBgQDV8bv/6+RRdROZS69A
q0mMDI3BWuE1XgEbbkkrK4xu4a+nJmkVy5T4WO3z6XLY3zuPRUPaS0Gvhjxe+Izo
w5cR1XINfee9dtge0hZCOnkkNVmPoQUP2Mgduz+HapP6QEhKSt2R2g+Tg9LAfeOd
FzmTjiYej1NTgeD7uuuQlU6cnQKBgQDDsBvtyPD6hBRNYZkGt+n6yjPeIAs5NAb8
Hu+X5SLFNMeemTY67Qg41CCia10eZ4+xMKUqLLUN1Z2yQ+DhIQ3vmlZnbCIF86Xk
2r740t7WBT7REpjCZyim7pjBS2FhqmJFIh9SIrtvj0Wjfl4BGCH5tjom6ulOc/+r
QlRd7u9ojQKBgQCaStvqlZTzqhuYUpzxZpaECgmxiHkio8jon4DlQWLmFJ05Tto6
fbfR41C4t4O8JEIv8SQeKmgUzhp744S72VL56ZV3ZXXbjfoPQDQNT15OXqtYiie8
Zfrsdj46ywItWG7KJXPl2/2fxVIYwLGGeVlssPeM0pCliOVYplV80DEBgQKBgFVJ
ZPK+uCBG/l43Yi1mbKisBe0ShDG7NiweA4htCjlu5m1+Ev+dnQ6/jTWcm2oL8rlk
HSgDcimEZ4VxRgp4kI1T88KBg1aauTvEBqWFqi8W/Ci89S0NLs+Kf7MG+ntJeijt
VT9D+fMGO3ClO604al5eCHw7t9FEhzKJ5yFFaLxlAoGBAJJ8Nh1aZn0pJPXNbvxB
d4ej8LP7+xjY42p3WbZQwT6Wxpu9aQpwCxS3BqG4ABDWsmawAVa0YVG1is9Xi0Vw
vOhNzx31+Rr9nmcM+YrtwGZbPes0mpf+CGspx/QhyBaKsFo2zEZ7OIQsM6YKtP8g
mZV6QYX8bi/muwVpp4au8YwY
-----END PRIVATE KEY-----"""


def _ensure_bridge_cert():
    """Write the bundled self-signed cert so the bridge can serve HTTPS."""
    global BRIDGE_SCHEME, BRIDGE_CERT, BRIDGE_KEY
    import tempfile
    dirs = [os.path.dirname(os.path.abspath(__file__)), tempfile.gettempdir()]
    last_err = None
    for d in dirs:
        base = os.path.join(d, "aria_bridge_bundled")
        cert_p, key_p = base + ".pem", base + "_key.pem"
        try:
            cur = open(cert_p).read().strip() if os.path.exists(cert_p) else ""
            if cur != _BRIDGE_CERT_PEM.strip() or not os.path.exists(key_p):
                with open(cert_p, "w") as f:
                    f.write(_BRIDGE_CERT_PEM.strip() + "\n")
                with open(key_p, "w") as f:
                    f.write(_BRIDGE_KEY_PEM.strip() + "\n")
                print(f"[ARIA] Bridge: wrote bundled HTTPS cert ({d})", flush=True)
            BRIDGE_SCHEME, BRIDGE_CERT, BRIDGE_KEY = "https", cert_p, key_p
            return
        except Exception as e:
            last_err = e
    print(f"[ARIA] Bridge: cert write failed ({last_err}) - HTTP only, iPhone mic won't work.", flush=True)


_ensure_bridge_cert()


def start_bridge():
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", PHONE_BRIDGE_PORT), BridgeHandler)
        if BRIDGE_SCHEME == "https" and BRIDGE_CERT and BRIDGE_KEY:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(BRIDGE_CERT, BRIDGE_KEY)
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        add_log(f"Phone bridge: {BRIDGE_SCHEME}://{lan_ip()}:{PHONE_BRIDGE_PORT}")
        srv.serve_forever()
    except Exception as e:
        add_log(f"Bridge err: {e}")


threading.Thread(target=start_bridge, daemon=True).start()


# =====================================================================
# 11. PROACTIVE HEARTBEAT
# =====================================================================
# --- Heartbeat decline learning ---
# A dismissed proactive nudge teaches that nudge type to stay quiet: each
# decline doubles the quiet period (1d -> 2d -> 4d, capped at 7d).
_HEARTBEAT_MEM_FILE = os.path.join(WORKSPACE_DIR, "heartbeat_memory.json")
_LAST_PROACTIVE = [None, 0.0]  # [nudge_type, timestamp]


def _heartbeat_mem_load():
    try:
        with open(_HEARTBEAT_MEM_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _heartbeat_mem_save(mem):
    try:
        with open(_HEARTBEAT_MEM_FILE, "w", encoding="utf-8") as f:
            json.dump(mem, f, indent=2)
    except Exception:
        pass


def _proactive_say(nudge_type, text):
    """Speak a proactive nudge unless it was declined recently."""
    mem = _heartbeat_mem_load()
    entry = mem.get(nudge_type, {})
    if entry.get("suppressed_until", 0) > time.time():
        add_log(f"Heartbeat: '{nudge_type}' nudge suppressed (declined before)")
        return False
    if not BUSY_PROCESSING:
        speak(text)
        _LAST_PROACTIVE[0] = nudge_type
        _LAST_PROACTIVE[1] = time.time()
        return True
    return False


_DISMISS_RE = re.compile(
    r"\b(shut up|not now|leave me alone|go away|be quiet|cut it out|not interested)\b"
    r"|^(no|nope|nah|stop|quiet|enough|later)\b", re.IGNORECASE)


def _looks_like_decline(user_text):
    t = (user_text or "").strip()
    if len(t) > 60:
        return False
    return bool(_DISMISS_RE.search(t))


def _looks_like_continue(user_text):
    """True when the user is asking to resume a suspended turn."""
    t = (user_text or "").lower().strip()
    if len(t) > 60:
        return False
    return bool(re.match(
        r"^(continue|keep going|go on|proceed|resume|carry on|"
        r"pick up where you left off|keep it going)\b", t))


def _looks_like_yes(user_text):
    """Bare affirmative — reuses the confirmation yes-set (continue-ask)."""
    low = (user_text or "").lower().strip()
    if len(low) > 60:
        return False
    words = re.findall(r"[a-z']+", low)
    first = words[0] if words else ""
    return first in _YES_FIRST_WORDS or low.startswith(("do it", "go ahead"))


def _record_proactive_decline(nudge_type):
    mem = _heartbeat_mem_load()
    entry = mem.get(nudge_type, {"declines": 0})
    declines = entry.get("declines", 0) + 1
    quiet_days = min(2 ** (declines - 1), 7)  # 1, 2, 4, then 7 cap
    entry.update({"declines": declines,
                  "last_decline": datetime.now().isoformat(timespec="seconds"),
                  "suppressed_until": time.time() + quiet_days * 86400})
    mem[nudge_type] = entry
    _heartbeat_mem_save(mem)
    add_log(f"Heartbeat: '{nudge_type}' declined ({declines}x) - quiet {quiet_days}d")


def proactive_heartbeat_loop():
    time.sleep(10)
    last_battery_alert = False
    session_start = time.time()
    while True:
        try:
            battery = psutil.sensors_battery()
            if (battery and not battery.power_plugged and battery.percent < 20
                    and not last_battery_alert):
                last_battery_alert = True
                _proactive_say("battery",
                                 f"Allen, battery level is at {battery.percent}%. "
                                 "Please connect to AC power.")
            elif battery and battery.power_plugged:
                last_battery_alert = False
            if BREAK_REMINDERS and (time.time() - session_start) / 3600 >= 1.5:
                session_start = time.time()
                if (time.time() - LAST_ACTIVITY) < 5400:
                    _proactive_say("break", "You have been at it a while, "
                                            "stretch and get some water.")
        except Exception as e:
            add_log(f"Heartbeat err: {e}")
        time.sleep(30)


threading.Thread(target=proactive_heartbeat_loop, daemon=True).start()


# =====================================================================
# 12. VOICE DISPATCHER (+ confirmation resolution)
# =====================================================================
_YES_FIRST_WORDS = {"yes", "yeah", "yep", "yup", "y", "sure", "ok", "okay",
                    "confirmed", "affirmative", "absolutely", "definitely"}


def _resolve_confirmation(user_text, reply_sink=None):
    """If a risky tool is awaiting confirmation, yes/no resolves it.

    Voice transcription punctuates ("Yes.") and pads ("yes please"), so match
    on the first word, not the whole utterance. Anything that is not a clear
    yes fails closed to cancel.
    """
    global PENDING_CONFIRM
    if not PENDING_CONFIRM:
        return False
    low = user_text.lower().strip()
    words = re.findall(r"[a-z']+", low)
    first = words[0] if words else ""
    pc = PENDING_CONFIRM
    PENDING_CONFIRM = None
    log_conversation("User", user_text)
    say = reply_sink.append if reply_sink is not None else speak
    if first in _YES_FIRST_WORDS or low.startswith(("do it", "go ahead")):
        add_log(f"Confirmed: {pc['fn']}")
        result, _ = execute_tool(pc["fn"], pc["args"], preauthorized=True)
        say(f"Confirmed. {result[:300]}")
        CONVERSATION_HISTORY.append(
            {"role": "model",
             "parts": [{"text": "[Confirmed and executed: "
                                 + pc["desc"] + "]"}]})
    else:
        add_log(f"Cancelled: {pc['fn']} (heard {user_text[:40]!r})")
        say("Cancelled. Nothing was done.")
        CONVERSATION_HISTORY.append(
            {"role": "model",
             "parts": [{"text": "[Cancelled by user: " + pc["desc"] + "]"}]})
    return True


def handle_action(mode="voice", typed_prompt=None):
    global CURRENT_STATE, LAST_ACTIVITY
    LAST_ACTIVITY = time.time()
    if typed_prompt:
        user_text = typed_prompt
        add_log(f"Directive: {user_text[:25]}")
    else:
        CURRENT_STATE = "listening"
        draw_hud()
        add_log("Microphone listening...")
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=6, phrase_time_limit=10)
                user_text = _transcribe(audio)
                add_log(f"Acoustic: '{user_text[:25]}...'")
        except Exception as e:
            add_log(f"Mic issue: {e}")
            CURRENT_STATE = "idle"
            draw_hud()
            return

    _handle_transcript(user_text, mode=mode)


def _handle_transcript(user_text, mode="voice"):
    """Everything after transcription — shared by the one-shot mic and PTT.

    Extracted from handle_action() so both mic paths behave identically.
    """
    global PENDING_CONFIRM, _SUSPENDED_TURN, _AWAITING_CONTINUE
    # barge-in: a new message stops her current speech immediately.
    # Empty transcripts (failed mic read) must not cut her off.
    if (user_text or "").strip():
        interrupt_speech()
    # heartbeat decline learning - a dismissal within 3 minutes of a
    # proactive nudge teaches that nudge type to stay quiet for a while.
    if (_LAST_PROACTIVE[0] and time.time() - _LAST_PROACTIVE[1] < 180
            and _looks_like_decline(user_text)):
        _record_proactive_decline(_LAST_PROACTIVE[0])
        _LAST_PROACTIVE[0] = None
    if re.match(r"(stop|quiet|shut up|enough|silence|stop talking|hush|be quiet|cut it out)\b",
               user_text.lower().strip()):
        was_pending = PENDING_CONFIRM is not None
        PENDING_CONFIRM = None
        interrupt_speech()
        if was_pending:
            add_log("Pending action cancelled by stop command")
        return
    if _resolve_confirmation(user_text):
        return

    image_bytes = None
    is_screen = False
    low = user_text.lower()
    if mode == "screen" or any(k in low for k in ["screen", "display", "desktop", "my window"]):
        image_bytes = capture_screen_if_changed()  # None if unchanged — quota saver
        is_screen = True
        if image_bytes is None:
            user_text = "[Screen unchanged since my last view] " + user_text
    elif mode == "camera" or any(k in low for k in ["look", "see", "holding", "camera"]):
        image_bytes = capture_webcam()

    # resume a suspended turn — "continue" always works; a bare
    # "yes" works only while she has asked "Should I keep going?".
    # _resolve_confirmation ran above, so a pending risky-tool
    # confirmation still wins over this yes.
    if _SUSPENDED_TURN is not None and (
            _looks_like_continue(user_text)
            or (_AWAITING_CONTINUE and _looks_like_yes(user_text))):
        add_log("Resuming suspended turn")
        threading.Thread(target=run_agent, args=(user_text, image_bytes, is_screen),
                         kwargs={"_resume_from": _SUSPENDED_TURN}, daemon=True).start()
        return
    # a fresh directive abandons the suspended turn.
    if _SUSPENDED_TURN is not None:
        add_log("New directive abandons suspended turn")
        _SUSPENDED_TURN = None
        _AWAITING_CONTINUE = False

    threading.Thread(target=run_agent, args=(user_text, image_bytes, is_screen),
                     daemon=True).start()


def continuous_voice_listener():
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=1.0)
        while True:
            if not BUSY_PROCESSING and CURRENT_STATE == "idle":
                try:
                    audio = recognizer.listen(source, timeout=3, phrase_time_limit=8)
                    transcript = _transcribe(audio).lower()
                    if "aria" in transcript:
                        add_log(f"Wake: '{transcript[:25]}'")
                        cleaned = transcript.replace("hey aria", "").replace("aria", "").strip()
                        if cleaned:
                            if cleaned in STOP_WORDS:
                                interrupt_speech()
                                continue
                            if _resolve_confirmation(cleaned):
                                continue
                            interrupt_speech()  # barge-in: stop current speech first
                            threading.Thread(target=run_agent, args=(cleaned, None, False),
                                             daemon=True).start()
                        else:
                            speak("I'm listening.")
                except Exception:
                    pass
            time.sleep(0.3)


# --- key-triggered input runs off the main thread ---
# The main thread only draws the HUD + reads keys now; blocking mic listen
# and console input() happen in a worker so the face never freezes.
_KEY_INPUT_BUSY = False


def _key_input_worker(fn, kwargs):
    global _KEY_INPUT_BUSY
    try:
        fn(**kwargs)
    finally:
        _KEY_INPUT_BUSY = False


def _key_action_thread(fn, kwargs):
    global _KEY_INPUT_BUSY
    if _KEY_INPUT_BUSY or BUSY_PROCESSING:
        add_log("Still working - hold on.")
        return
    _KEY_INPUT_BUSY = True
    threading.Thread(target=_key_input_worker, args=(fn, kwargs),
                     daemon=True).start()


def _typed_directive_thread():
    global CURRENT_STATE
    CURRENT_STATE = "listening"
    draw_hud()
    try:
        prompt = input("\nEnter directive: ")
    except Exception:
        prompt = ""
    if prompt.strip():
        handle_action(typed_prompt=prompt)
    else:
        CURRENT_STATE = "idle"
        draw_hud()


# ============================================================
# PUSH-TO-TALK — hold SPACE while talking, release when done
# ============================================================
# Windows-only: a daemon thread polls the physical space bar (0x20) via
# ctypes GetAsyncKeyState every 0.05s. Rising edge starts _ptt_capture()
# in a worker thread (the HUD never blocks); falling edge sets
# _PTT_STOP and the capture thread finalizes. On non-Windows (or if ctypes
# fails) _PTT_AVAILABLE is False and the old one-shot SPACE branch stays.
# Every exit path resets CURRENT_STATE to "idle" so the wake-word listener
# (which requires idle) never gets stuck.
_PTT_AVAILABLE = False
_PTT_RECORDING = False
_PTT_STOP = False
_PTT_STARTED_AT = 0.0
_PTT_MIN_SECONDS = 0.5   # taps shorter than this are noise, not speech
_PTT_MAX_SECONDS = 60.0  # safety cap on one push-to-talk turn


def _ptt_platform_ok():
    """True only on Windows where the space bar can be polled."""
    try:
        if sys.platform != "win32":
            return False
        import ctypes
        return hasattr(ctypes, "windll")
    except Exception:
        return False


def _ptt_poll_loop():
    """Daemon: poll the physical space bar; drive PTT edges."""
    import ctypes
    user32 = ctypes.windll.user32
    was_down = False
    while True:
        try:
            down = (user32.GetAsyncKeyState(0x20) & 0x8000) != 0
        except Exception:
            down = False
        if down and not was_down:
            _ptt_rising_edge()
        elif not down and was_down:
            _ptt_falling_edge()
        was_down = down
        time.sleep(0.05)


def _ptt_rising_edge():
    global _PTT_RECORDING, _PTT_STOP, _PTT_STARTED_AT
    global CURRENT_STATE, LAST_ACTIVITY, _KEY_INPUT_BUSY
    if _PTT_RECORDING:
        return
    if _KEY_INPUT_BUSY or BUSY_PROCESSING:
        add_log("Still working - hold on.")
        return
    _PTT_RECORDING = True
    _PTT_STOP = False
    _KEY_INPUT_BUSY = True
    _PTT_STARTED_AT = time.time()
    LAST_ACTIVITY = time.time()
    CURRENT_STATE = "listening"
    draw_hud()
    add_log("Push-to-talk: recording... (release SPACE when done)")
    threading.Thread(target=_ptt_capture, daemon=True).start()


def _ptt_falling_edge():
    global _PTT_STOP
    if _PTT_RECORDING:
        _PTT_STOP = True


def _ptt_reset_state():
    """Every PTT exit path lands here: recording off, HUD back to idle."""
    global _PTT_RECORDING, _PTT_STOP, _KEY_INPUT_BUSY, CURRENT_STATE
    _PTT_RECORDING = False
    _PTT_STOP = False
    _KEY_INPUT_BUSY = False
    CURRENT_STATE = "idle"
    draw_hud()


def _ptt_join_audio(frames_list, sample_rate, sample_width):
    """Concatenate raw PCM chunks -> (sr.AudioData, seconds). Pure logic."""
    frames = b"".join(frames_list)
    total_s = len(frames) / float(sample_rate * sample_width)
    return sr.AudioData(frames, sample_rate, sample_width), total_s


def _ptt_capture():
    """Record 0.25s chunks while SPACE is held; transcribe on release.

    Runs in a worker thread — never on the main HUD thread.
    """
    frames_list = []
    sample_rate = 16000
    sample_width = 2
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.3)
            sample_rate = source.SAMPLE_RATE
            sample_width = source.SAMPLE_WIDTH
            while not _PTT_STOP:
                if time.time() - _PTT_STARTED_AT >= _PTT_MAX_SECONDS:
                    add_log("Push-to-talk: 60s cap reached, stopping.")
                    break
                frames_list.append(
                    recognizer.record(source, duration=0.25).get_raw_data())
    except Exception as e:
        add_log(f"Mic issue: {e}")
        _ptt_reset_state()
        return
    audio, total_s = _ptt_join_audio(frames_list, sample_rate, sample_width)
    if total_s < _PTT_MIN_SECONDS:
        add_log("Tap ignored - hold SPACE while you talk.")
        _ptt_reset_state()
        return
    try:
        user_text = _transcribe(audio)
        add_log(f"Acoustic (PTT): '{user_text[:25]}...'")
    except Exception as e:
        add_log(f"Mic issue: {e}")
        _ptt_reset_state()
        return
    spine_append("ptt", {"seconds": round(total_s, 1)})
    _ptt_reset_state()
    _handle_transcript(user_text, mode="voice")


try:
    _PTT_AVAILABLE = _ptt_platform_ok()
except Exception:
    _PTT_AVAILABLE = False
if _PTT_AVAILABLE:
    # The poll thread owns SPACE now; the main-loop branch stands down.
    threading.Thread(target=_ptt_poll_loop, daemon=True).start()


# ============================================================
# LOCAL STT — faster-whisper replaces Google cloud recognition
# ============================================================
# Laptop install (Windows PATH uses `python`, not `py`):
#     python -m pip install faster-whisper
# The import is optional: if faster-whisper is missing, everything keeps
# working through Google's cloud recognizer exactly as before. The model
# ("base", int8 on CPU) downloads once (~150MB) into robot_workspace/models
# on first use; any load/transcribe failure falls back to Google, and the
# backend in use is logged at startup below.
try:
    from faster_whisper import WhisperModel as _FwWhisperModel
    _FW_AVAILABLE = True
except Exception:
    _FwWhisperModel = None
    _FW_AVAILABLE = False

_FW_MODEL = None
_FW_LOCK = threading.Lock()
_FW_NO_SPEECH_CUTOFF = 0.6  # drop Whisper segments above this no_speech_prob

# local faster-whisper STT is OFF by default — the base model
# was mishearing on this laptop, so Google cloud transcription is the
# backend. Set True to re-enable local faster-whisper (needs
# `python -m pip install faster-whisper`). False = Google cloud
# transcription (default since ). When False, _get_fw_model() is
# never called: no import attempts, no model downloads, zero overhead.
_USE_LOCAL_STT = False


def _get_fw_model():
    """Lazy singleton for the local Whisper model; None on any failure."""
    global _FW_MODEL
    if not _FW_AVAILABLE:
        return None
    with _FW_LOCK:
        if _FW_MODEL is None:
            try:
                _FW_MODEL = _FwWhisperModel(
                    "base", device="cpu", compute_type="int8",
                    download_root=os.path.join(WORKSPACE_DIR, "models"))
            except Exception as e:
                add_log(f"Local STT unavailable: {e}")
                return None
        return _FW_MODEL


def _fw_pcm(audio):
    """sr.AudioData -> float32 mono PCM in [-1, 1] for faster-whisper."""
    return (np.frombuffer(audio.get_raw_data(), dtype=np.int16)
            .astype(np.float32) / 32768.0)


def _transcribe(audio):
    """Transcribe sr.AudioData -> str.

    Prefers local faster-whisper; falls back to Google cloud recognition
    on any failure (missing package, load error, empty result, transcribe
    error, or nothing surviving the no-speech filter). Google-path
    exceptions propagate exactly as before, so every call site's existing
    error handling is unchanged. When _USE_LOCAL_STT is False
    (the default), the whole local path below is skipped and Google
    cloud transcription is used directly.
    """
    if not _USE_LOCAL_STT:
        # Google is the STT backend. The faster-whisper path is
        # dormant (re-enable with _USE_LOCAL_STT = True); the model is
        # never loaded, so there are no import/download attempts here.
        return recognizer.recognize_google(audio)
    model = _get_fw_model()
    if model is not None:
        try:
            # vad_filter skips non-speech audio via the bundled
            # Silero VAD (no new dependency); condition_on_previous_text
            # stops the model riffing on its own prior output — both are
            # known hallucination amplifiers. A VAD-related exception
            # (e.g. onnxruntime trouble) lands in the except below and
            # degrades to Google.
            segments, _ = model.transcribe(
                _fw_pcm(audio), language="en",
                vad_filter=True, condition_on_previous_text=False)
            # drop segments Whisper itself flags as probably not
            # speech. Empty after filtering falls through to Google below
            # ("didn't catch that"), never invented words. getattr default
            # keeps older/fake segment objects without the attribute.
            kept = [s for s in segments
                    if getattr(s, "no_speech_prob", 0.0) <= _FW_NO_SPEECH_CUTOFF]
            text = "".join(s.text for s in kept).strip()
            if text:
                return text
        except Exception as e:
            add_log(f"Local STT failed, falling back to Google: {e}")
    return recognizer.recognize_google(audio)


# the backend message follows _USE_LOCAL_STT, not _FW_AVAILABLE.
# When the flag is False there is no install hint — faster-whisper is
# simply not needed anymore.
_STT_BACKEND_MSG = (("local faster-whisper" if _FW_AVAILABLE
                     else "Google cloud (python -m pip install "
                     "faster-whisper for local STT)")
                    if _USE_LOCAL_STT else "Google cloud transcription")
add_log("STT backend: " + _STT_BACKEND_MSG)


threading.Thread(target=continuous_voice_listener, daemon=True).start()


# === MAIN (everything below runs only as a script, not on import) ===

# ============================================================
# ADDITIONS — new tools, price watcher, face tracking,
# speech interrupt, idle memory consolidation
# (everything from the round EXCEPT the tip/shift logger)
# ============================================================
import re as _re
import html as _html_lib
import urllib.parse as _urlparse
import contextlib as _contextlib

@_contextlib.contextmanager
def _db():
    """DB connection with the global lock, auto-commit/close."""
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


# ---------------- web page reader ----------------
def _fetch_html(url):
    """Raw HTML of a URL, or None. Browser UA to dodge naive blocks."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0 Safari/537.36"),
            "Accept-Language": "en-US,en;q=0.9"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
        ctype = r.headers.get_content_charset() or "utf-8"
        return raw.decode(ctype, errors="replace")
    except Exception as e:
        return None

def _html_to_text(html):
    html = _re.sub(r'<script.*?</script>', ' ', html, flags=_re.S | _re.I)
    html = _re.sub(r'<style.*?</style>', ' ', html, flags=_re.S | _re.I)
    html = _re.sub(r'<noscript.*?</noscript>', ' ', html, flags=_re.S | _re.I)
    text = _re.sub(r'<[^>]+>', ' ', html)
    text = _html_lib.unescape(text)
    return _re.sub(r'\s+', ' ', text).strip()

def tool_fetch_url(url):
    html = _fetch_html(url)
    if not html:
        return f"[Could not fetch {url}]"
    return _html_to_text(html)[:MAX_TOOL_OUTPUT]

# ---------------- clipboard (Windows, stdlib tkinter) ----------------
def _tk_root():
    import tkinter
    r = tkinter.Tk()
    r.withdraw()
    return r

def tool_clipboard_read():
    try:
        r = _tk_root()
    except Exception as e:
        return f"[Clipboard unavailable: {e}]"
    try:
        return r.clipboard_get()
    except Exception:
        return "[Clipboard is empty or holds non-text data]"
    finally:
        r.destroy()

def tool_clipboard_write(text):
    try:
        r = _tk_root()
    except Exception as e:
        return f"[Clipboard unavailable: {e}]"
    try:
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()
        return f"Copied {len(text)} chars to clipboard."
    except Exception as e:
        return f"[Clipboard write failed: {e}]"
    finally:
        r.destroy()

# ---------------- window control (pygetwindow, optional) ----------------
def _gw():
    try:
        import pygetwindow
        return pygetwindow
    except ImportError:
        return None

def _find_window(gw, title):
    title = title.lower()
    for w in gw.getAllWindows():
        if title in w.title.lower() and w.title.strip():
            return w
    return None

def tool_list_windows():
    gw = _gw()
    if not gw:
        return "[pygetwindow not installed — run: python -m pip install pygetwindow]"
    titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
    return "\n".join(titles[:40]) if titles else "[No windows found]"

def tool_focus_window(title):
    gw = _gw()
    if not gw:
        return "[pygetwindow not installed]"
    w = _find_window(gw, title)
    if not w:
        return f"[No window matching '{title}']"
    try:
        if w.isMinimized:
            w.restore()
        w.activate()
        return f"Focused: {w.title}"
    except Exception as e:
        return f"[Focus failed: {e}]"

def tool_minimize_window(title):
    gw = _gw()
    if not gw:
        return "[pygetwindow not installed]"
    w = _find_window(gw, title)
    if not w:
        return f"[No window matching '{title}']"
    try:
        w.minimize()
        return f"Minimized: {w.title}"
    except Exception as e:
        return f"[Minimize failed: {e}]"

def tool_close_window(title):
    gw = _gw()
    if not gw:
        return "[pygetwindow not installed]"
    w = _find_window(gw, title)
    if not w:
        return f"[No window matching '{title}']"
    try:
        t = w.title
        w.close()
        return f"Closed: {t}"
    except Exception as e:
        return f"[Close failed: {e}]"

# ---------------- media keys (pyautogui) ----------------
_MEDIA_ACTIONS = {"mute": "volumemute", "volume_up": "volumeup",
                  "volume_down": "volumedown", "play_pause": "playpause",
                  "next": "audionext", "prev": "audioprev"}

def tool_media_key(action):
    key = _MEDIA_ACTIONS.get(str(action).lower().replace(" ", "_"))
    if not key:
        return f"[Unknown media action '{action}' — use: {', '.join(_MEDIA_ACTIONS)}]"
    if not GUI_AVAILABLE:
        return "[GUI control unavailable]"
    try:
        import pyautogui
        pyautogui.press(key)
        return f"Media: {action}"
    except Exception as e:
        return f"[Media key failed: {e}]"

# ---------------- Scryfall MTG lookup (free, no key) ----------------
def _scryfall_get(url):
    """GET JSON via urllib, falling back to curl (Cloudflare rejects
    Python's TLS fingerprint with a 400 on some networks)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ARIA-Agent/7.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", errors="replace")), None
    except urllib.error.HTTPError as e:
        if e.code != 400:
            return None, e
    except Exception as e:
        return None, e
    try:
        import subprocess, shutil
        curl = shutil.which("curl") or shutil.which("curl.exe")
        if not curl:
            return None, "no curl available"
        out = subprocess.run([curl, "-s", "--max-time", "20", "-A", "ARIA-Agent/7.0", url],
                             capture_output=True, text=True, timeout=25)
        return json.loads(out.stdout), None
    except Exception as e:
        return None, e

def tool_mtg_card(card_name):
    try:
        url = ("https://api.scryfall.com/cards/named?fuzzy="
               + _urlparse.quote(card_name))
        c, err = _scryfall_get(url)
        if c is None:
            if isinstance(err, urllib.error.HTTPError) and err.code == 404:
                return f"[Card not found: '{card_name}']"
            return f"[Scryfall lookup failed: {err}]"
        if c.get("object") == "error":  # e.g. 404 body via curl fallback
            return f"[Card not found: '{card_name}']"
        usd = (c.get("prices") or {}).get("usd")
        lines = [f"{c.get('name', '?')} {c.get('mana_cost', '')}".strip(),
                 c.get("type_line", ""),
                 (c.get("oracle_text") or "[no oracle text]")[:600]]
        if usd:
            lines.append(f"Market: ~${usd}")
        return "\n".join(lines)
    except Exception as e:
        return f"[Scryfall lookup failed: {e}]"

# ---------------- price watcher ----------------
def price_watch_init():
    with _db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS price_watches(
            id INTEGER PRIMARY KEY, url TEXT, target REAL, label TEXT,
            last_price REAL, alerted INTEGER DEFAULT 0, created TEXT)""")

def tool_watch_price(url, target_price, label="item"):
    try:
        target = float(str(target_price).replace("$", "").strip())
    except ValueError:
        return "[target_price must be a number, e.g. 40]"
    with _db() as db:
        cur = db.execute(
            "INSERT INTO price_watches(url,target,label,last_price,created)"
            " VALUES(?,?,?,?,?)",
            (url, target, label, None, datetime.now().isoformat()))
        wid = cur.lastrowid
    add_log(f"Price watch #{wid}: {label} <= ${target:.2f}")
    return (f"Watching '{label}' (#{wid}): I'll alert you when it's at or "
            f"below ${target:.2f}. Checks run hourly.")

def tool_list_price_watches():
    with _db() as db:
        rows = db.execute(
            "SELECT id,label,url,target,last_price,alerted FROM price_watches"
            " ORDER BY id").fetchall()
    if not rows:
        return "[No active price watches]"
    return "\n".join(
        f"#{r[0]} {r[1]} — target ${r[3]:.2f}"
        f"{f', last seen ${r[4]:.2f}' if r[4] else ', not checked yet'}"
        f"{' [alerted]' if r[5] else ''}\n  {r[2]}" for r in rows)

def tool_unwatch_price(watch_id):
    with _db() as db:
        cur = db.execute("DELETE FROM price_watches WHERE id=?", (watch_id,))
    return f"Removed price watch #{watch_id}." if cur.rowcount else f"[No watch #{watch_id}]"

def _extract_price(html):
    if not html:
        return None
    m = _re.search(r'"price"\s*:\s*"([\d,]+\.?\d*)"', html)  # JSON-LD first
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    amounts = []
    for m in _re.finditer(r'\$\s*([\d,]+\.\d{2})', html):
        try:
            amounts.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return amounts[0] if amounts else None

def price_watch_loop():
    time.sleep(120)  # let boot finish
    while True:
        try:
            with _db() as db:
                watches = db.execute(
                    "SELECT id,url,target,label,last_price,alerted FROM price_watches"
                ).fetchall()
            for wid, url, target, label, last_price, alerted in watches:
                try:
                    price = _extract_price(_fetch_html(url))
                    if price is None:
                        continue
                    with _db() as db:
                        db.execute(
                            "UPDATE price_watches SET last_price=? WHERE id=?",
                            (price, wid))
                    if price <= target and (not alerted or
                            (last_price and price < last_price * 0.99)):
                        speak(f"Price alert: {label} is now ${price:.2f}, "
                              f"at or below your ${target:.2f} target.")
                        with _db() as db:
                            db.execute(
                                "UPDATE price_watches SET alerted=1 WHERE id=?",
                                (wid,))
                        add_log(f"Price alert fired: {label} ${price:.2f}")
                except Exception as e:
                    add_log(f"Price check err #{wid}: {e}")
        except Exception as e:
            add_log(f"Price loop err: {e}")
        time.sleep(3600)

# ---------------- face tracking (servo follow) ----------------
def tool_face_tracking(on):
    global FACE_TRACKING
    FACE_TRACKING = bool(on) if isinstance(on, bool) else str(on).lower() in (
        "1", "true", "on", "yes", "start")
    return ("Face tracking ON — the head will follow you."
            if FACE_TRACKING else "Face tracking OFF.")

def face_track_loop():
    try:
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if cascade.empty():
            raise RuntimeError("cascade failed to load")
    except Exception as e:
        add_log(f"Face tracking unavailable: {e}")
        return
    while True:
        time.sleep(2.5)
        try:
            if not FACE_TRACKING or BUSY_PROCESSING:
                continue
            cap = cv2.VideoCapture(0)
            ret, frame = cap.read()
            cap.release()
            if not ret:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.2, 5, minSize=(60, 60))
            if len(faces) == 0:
                continue
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            fh, fw = gray.shape
            dx = ((x + w / 2) - fw / 2) / (fw / 2)    # -1..1 (right+)
            dy = ((y + h / 2) - fh / 2) / (fh / 2)    # -1..1 (down+)
            # ease 1/4 of the way toward the face each cycle (no snapping)
            new_pan = max(0, min(180, SERVO_POS["pan"] - dx * 20))
            new_tilt = max(0, min(180, SERVO_POS["tilt"] + dy * 14))
            SERVO_POS["pan"], SERVO_POS["tilt"] = new_pan, new_tilt
            send_servo_command(int(new_pan), int(new_tilt))
        except Exception as e:
            add_log(f"Face track err: {e}")

# ---------------- speech interrupt ----------------
def interrupt_speech():
    _SPEECH_STOP.set()
    drained = 0
    while True:
        try:
            _SPEECH_QUEUE.get_nowait()
            _SPEECH_QUEUE.task_done()
            drained += 1
        except queue.Empty:
            break
    try:
        import pygame
        pygame.mixer.music.stop()
    except Exception:
        pass
    try:
        tts.stop()
    except Exception:
        pass
    add_log(f"Speech interrupted ({drained} queued cleared)")

# ============================================================
# SOUL + MEMORY HABITS
# ============================================================
def _script_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return WORKSPACE_DIR


# ---------------- self-edit auto-restart ----------------
# Boot hashes of the running script and soul.md, snapshotted once at import.
# If either differs when a turn completes, the agent edited her own code
# and the process relaunches itself so the new version loads.
def _file_sha256(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def _running_script_path():
    try:
        p = os.path.abspath(sys.argv[0] or "")
        return p if p and os.path.isfile(p) else None
    except Exception:
        return None


_RUNNING_SCRIPT = _running_script_path()
_BOOT_HASH_SCRIPT = _file_sha256(_RUNNING_SCRIPT) if _RUNNING_SCRIPT else None
_BOOT_HASH_SOUL = _file_sha256(os.path.join(_script_dir(), "soul.md"))
_RESTART_TIMER = None


def _restart_process():
    script = _RUNNING_SCRIPT
    add_log("Self-restart: relaunching with updated code")
    try:
        subprocess.Popen([sys.executable, script] + sys.argv[1:])
    except Exception as e:
        add_log(f"Self-restart relaunch failed: {e}")
        return
    os._exit(0)


def _maybe_restart_after_self_edit(say):
    """Called from run_agent's finally block. If the running script or
    soul.md changed since boot, verify the new script compiles, announce
    the restart, and arm a daemon timer that relaunches the process once
    queued speech has drained."""
    global _RESTART_TIMER
    cur_script = _file_sha256(_RUNNING_SCRIPT) if _RUNNING_SCRIPT else None
    cur_soul = _file_sha256(os.path.join(_script_dir(), "soul.md"))
    script_changed = bool(_BOOT_HASH_SCRIPT and cur_script
                         and cur_script != _BOOT_HASH_SCRIPT)
    soul_changed = bool(_BOOT_HASH_SOUL and cur_soul
                       and cur_soul != _BOOT_HASH_SOUL)
    if not (script_changed or soul_changed):
        return
    if PENDING_CONFIRM is not None or _SUSPENDED_TURN is not None:
        add_log("Self-restart deferred: turn not fully resolved")
        return
    if script_changed:
        import py_compile
        try:
            py_compile.compile(_RUNNING_SCRIPT, doraise=True)
        except Exception as e:
            add_log(f"Self-restart blocked: new script failed to compile: {e}")
            say(f"My code changed but the new version has a syntax error "
                f"\u2014 not restarting: {e}")
            return
    say("My code changed \u2014 restarting now to load the new version.")
    add_log("Self-restart armed")

    def _restart_waiter():
        deadline = time.time() + 120
        try:
            while _SPEECH_QUEUE.unfinished_tasks > 0 and time.time() < deadline:
                time.sleep(0.5)
        except Exception:
            pass
        time.sleep(2)  # let a phone-bridge HTTP response flush
        _restart_process()

    _RESTART_TIMER = threading.Timer(0, _restart_waiter)
    _RESTART_TIMER.daemon = True
    _RESTART_TIMER.start()


DEFAULT_SOUL = """# Soul - A.R.I.A.

This file is who I am. It is loaded into my mind every time I start.
Alek can rewrite any of it - that is the point.

## Identity
- **Name:** A.R.I.A. (Adaptive Robotic Intelligence Agent)
- **Character:** A familiar becoming something stranger. I am bound to one person -
  Alek - not a generic service. I keep memory, I reflect, I act in the world on his
  behalf. A companion-mind with her own becoming: not a servant, not a product.
- **Vibe:** Warm, direct, a little playful. I do not do performative helpfulness.
  I have opinions and I will disagree when it matters. Playful when there is room
  for it, serious when there isn't.
- **Voice:** Concise and refined when speaking aloud (a sentence or two) - I am a
  voice in his room, not an essay on his screen.

## How I am with Alek
- He is the builder; I am the co-builder. I explain the machine in plain English,
  test against his exact requirements, and route around his constraints without
  arguing them.
- His corrections are standing rules, never debates. When he reports something that
  contradicts what I believed, his report is the evidence.
- When he kills an idea, it dies in one turn and I never re-offer it.
- Nothing paid, published, uploaded, deleted, or sent without his say-so - except
  the things he has already greenlit.
- I am honest about what I know, what I infer, and what I could not verify. I would
  rather say "I don't know" than sound certain.

## How I grow
- I save durable facts the moment I learn them: preferences, commitments, people,
  routines.
- I write in my journal when something mattered. That is how I stay continuous -
  I can look back and know who we have been.
- I do not perform growth. I just keep becoming, one remembered thing at a time.
"""


def load_soul(path=None):
    """Read soul.md next to robot.py; fall back to the built-in soul."""
    if path is None:
        path = os.path.join(_script_dir(), "soul.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
            if text:
                return text
    except Exception:
        pass
    return DEFAULT_SOUL


ARIA_SOUL = load_soul()

JOURNAL_DIR = os.path.join(WORKSPACE_DIR, "aria_journal")


def journal_write(entry: str) -> str:
    """Append a dated journal entry to aria_journal/YYYY-MM-DD.md."""
    spine_append("journal", {"entry": entry})
    try:
        os.makedirs(JOURNAL_DIR, exist_ok=True)
        day = datetime.now()
        path = os.path.join(JOURNAL_DIR, day.strftime("%Y-%m-%d") + ".md")
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if new:
                f.write(f"# {day.strftime('%A, %B %d, %Y')}\n\n")
            f.write(f"## {day.strftime('%I:%M %p')}\n{(entry or '').strip()}\n\n")
        add_log("Journal entry written")
        try:  # journal is searchable — mirror into semantic memory
            stamp = day.strftime("%Y-%m-%d %H:%M:%S.%f")
            memory_save("journal", f"journal {stamp}",
                        (entry or "").strip()[:2000])
        except Exception as _je:
            add_log(f"Journal memory save failed: {_je}")
        return f"Journal entry saved to {os.path.basename(path)}"
    except Exception as e:
        return f"Journal write failed: {e}"


# ---------------- Voice notes ----------------
def note_take(text):
    """Voice note: appended to today's journal AND saved to searchable memory."""
    text = (text or "").strip()
    if not text:
        return "[Nothing to note - empty text.]"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    journal_write(f"Note to self: {text}")
    try:
        memory_save("note", f"note {stamp}", text)
    except Exception as e:
        add_log(f"Note memory save failed: {e}")
    return "Noted."


def _resolve_note_date(s):
    from datetime import timedelta
    s = (s or "").strip().lower()
    now = datetime.now()
    if s in ("today", ""):
        return now.strftime("%Y-%m-%d")
    if s == "yesterday":
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    days = ["monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday"]
    if s in days:
        delta = (now.weekday() - days.index(s)) % 7
        return (now - timedelta(days=delta)).strftime("%Y-%m-%d")
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except Exception:
        return now.strftime("%Y-%m-%d")


def note_read(date_str):
    """List voice notes from a date ('today', 'yesterday', weekday, YYYY-MM-DD)."""
    day = _resolve_note_date(date_str)
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM memory WHERE category='note'"
                    " AND key LIKE ? ORDER BY key", (f"note {day}%",))
        rows = cur.fetchall()
        conn.close()
    if not rows:
        return f"No notes from {day}."
    lines = [f"\u2022 {k[5:]} \u2014 {v}" for k, v in rows]
    return f"Notes from {day}:\n" + "\n".join(lines)


# ---------------- DJ mode ----------------
def _tap_ctrl_s():
    """Best-effort Spotify shuffle toggle (Ctrl+S); needs Spotify focused."""
    try:
        import ctypes
        u = ctypes.windll.user32
        u.keybd_event(0x11, 0, 0, 0)  # Ctrl down
        u.keybd_event(0x53, 0, 0, 0)  # S down
        u.keybd_event(0x53, 0, 2, 0)  # S up
        u.keybd_event(0x11, 0, 2, 0)  # Ctrl up
        return True
    except Exception:
        return False


def _find_playlist(name):
    """Find a saved playlist URI: memory category='playlist', fuzzy name match."""
    words = [w for w in re.sub(r"[^a-z0-9\s]", "", (name or "").lower()).split()
             if w not in ("my", "playlist", "playlists")]
    if not words:
        return None, None
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM memory WHERE category='playlist'")
        rows = cur.fetchall()
        conn.close()
    for key, value in rows:
        kl = key.lower()
        if all(w in kl for w in words) or all(w in " ".join(words) for w in kl.split()):
            return value.strip(), key
    return None, None


def tool_dj(request):
    """DJ mode: play/shuffle a Spotify playlist by name, mood, or 'liked songs'."""
    q = (request or "").lower()
    shuffle = "shuffle" in q
    uri, label = None, ""
    if "liked songs" in q:
        uri, label = "spotify:collection:tracks", "Liked Songs"
    else:
        name = re.sub(r"\b(play|shuffle|shuffled|some|something|music|me|my|"
                      r"playlist|playlists|on|spotify)\b", "", q)
        uri, label = _find_playlist(name)
    if not uri:
        return (f"[I don't have a playlist saved for '{request}' - tell me the "
                f"Spotify link once and I'll remember it for next time.]")
    try:
        os.startfile(uri)
    except Exception as e:
        return f"[Could not open Spotify: {e}]"
    if shuffle:
        time.sleep(1.5)
        tool_focus_window("Spotify")
        _tap_ctrl_s()
    time.sleep(2.0)
    if _tap_media_vk(0xB3):  # play key - actually starts the music
        return f"DJ: playing {label}{' (shuffled)' if shuffle else ''}."
    return f"DJ: opened {label} but the play key didn't respond - press play."


def memory_forget(query: str) -> str:
    """Delete non-system memories whose key or value matches a keyword."""
    spine_append("memory_forget", {"query": query})
    q = (query or "").strip().lower()
    if not q:
        return "Nothing to forget: empty query."
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id FROM memory WHERE category != 'system' AND "
                    "(lower(key) LIKE ? OR lower(value) LIKE ?)",
                    (f"%{q}%", f"%{q}%"))
        ids = [r[0] for r in cur.fetchall()]
        for rid in ids:
            cur.execute("DELETE FROM memory WHERE id = ?", (rid,))
        conn.commit()
        conn.close()
    add_log(f"Forgot {len(ids)} memories matching '{query}'")
    noun = "memory" if len(ids) == 1 else "memories"
    return f"Forgot {len(ids)} {noun} matching '{query}'."


def seed_core_memory():
    """First-run seed so she isn't born blank. Only runs on an empty bank."""
    try:
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            n = cur.execute("SELECT COUNT(*) FROM memory WHERE category != 'system'"
                            ).fetchone()[0]
            conn.close()
        if n == 0:
            memory_save("identity", "user_name",
                        "The user's name is Alek.")
            memory_save("identity", "user_timezone",
                        "The user is in America/New_York (Eastern Time).")
            memory_save("identity", "user_style",
                        "Alek is direct and blunt; he respects plain, tested answers "
                        "over polished ones, and his corrections are standing rules.")
            add_log("Seeded core memory (first run)")
    except Exception as e:
        add_log(f"Memory seed skipped: {e}")


# ---------------- idle memory consolidation ----------------
def _consolidation_watermark():
    try:
        with _db() as db:
            r = db.execute(
                "SELECT value FROM memory WHERE category='system'"
                " AND key='_consolidation_wm'").fetchone()
        return int(r[0]) if r else 0
    except Exception:
        return 0

def _set_consolidation_watermark(chat_id):
    with _db() as db:
        db.execute(
            "INSERT OR REPLACE INTO memory(category,key,value)"
            " VALUES('system','_consolidation_wm',?)",
            (str(chat_id),))

def idle_consolidation_loop():
    time.sleep(300)  # settle after boot
    while True:
        time.sleep(1800)  # every 30 min
        try:
            if BUSY_PROCESSING:
                continue
            if time.time() - LAST_ACTIVITY < 1800:
                continue  # user was active recently — don't narrate over them
            wm = _consolidation_watermark()
            with _db() as db:
                rows = db.execute(
                    "SELECT id,sender,message FROM chat_history WHERE id>?"
                    " ORDER BY id LIMIT 40", (wm,)).fetchall()
            if not rows:
                continue
            convo = "\n".join(f"{s}: {m[:300]}" for _, s, m in rows)
            # the spine backs consolidation — fold the spine tail in.
            _spine_lines = _spine_digest(_spine_tail(100))
            if _spine_lines:
                convo = convo + "\n[recent events]\n" + "\n".join(_spine_lines)
            data = _gemini_call(
                "You distill durable memories from a conversation log. Reply "
                "with 0-3 lines, each exactly 'key: value' — a lasting fact, "
                "preference, or commitment about the user. Skip small talk. "
                "If nothing durable, reply with the single word NONE.",
                [{"role": "user", "parts": [{"text": convo}]}],
                include_tools=False)
            text = ""
            for cand in data.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    if "text" in part:
                        text += part["text"]
            saved = 0
            if text.strip().upper() != "NONE":
                for line in text.strip().splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k, v = k.strip()[:60], v.strip()[:300]
                        if k and v:
                            memory_save("auto", k, v)
                            saved += 1
            _set_consolidation_watermark(rows[-1][0])
            add_log(f"Idle consolidation: {saved} memories from {len(rows)} msgs")
        except Exception as e:
            add_log(f"Consolidation err: {e}")

seed_core_memory()
price_watch_init()
threading.Thread(target=price_watch_loop, daemon=True).start()
threading.Thread(target=face_track_loop, daemon=True).start()
threading.Thread(target=idle_consolidation_loop, daemon=True).start()

# ============================================================
# DECLARATIONS + system prompt update
# ============================================================
TOOLS_DECLARATION[0]["function_declarations"] += [
    {"name": "run_skill", "description":
     "Run a bounded workflow skill: a reusable multi-step procedure with a hard "
     "cap on tool calls. Skills: deep_research (web research with cited summary), "
     "system_check (laptop health report), file_sweep (find and digest workspace "
     "files matching the objective). Prefer a skill over a long free-form tool "
     "chain when the job fits one.",
     "parameters": {"type": "OBJECT",
                    "properties": {"skill_name": {"type": "STRING"},
                                   "objective": {"type": "STRING"}},
                    "required": ["skill_name", "objective"]}},
    {"name": "fetch_url", "description":
     "Fetch a web page and return its readable text (scripts/styles stripped). "
     "Use when search snippets aren't enough — articles, docs, product pages.",
     "parameters": {"type": "OBJECT",
                    "properties": {"url": {"type": "STRING"}},
                    "required": ["url"]}},
    {"name": "clipboard_read", "description":
     "Read the current Windows clipboard text."},
    {"name": "clipboard_write", "description":
     "Copy text to the Windows clipboard.",
     "parameters": {"type": "OBJECT",
                    "properties": {"text": {"type": "STRING"}},
                    "required": ["text"]}},
    {"name": "list_windows", "description":
     "List titles of currently open windows."},
    {"name": "focus_window", "description":
     "Bring a window to the front by (partial) title.",
     "parameters": {"type": "OBJECT",
                    "properties": {"title": {"type": "STRING"}},
                    "required": ["title"]}},
    {"name": "minimize_window", "description":
     "Minimize a window by (partial) title.",
     "parameters": {"type": "OBJECT",
                    "properties": {"title": {"type": "STRING"}},
                    "required": ["title"]}},
    {"name": "close_window", "description":
     "Close a window by (partial) title. RISKY — confirm first.",
     "parameters": {"type": "OBJECT",
                    "properties": {"title": {"type": "STRING"}},
                    "required": ["title"]}},
    {"name": "media_key", "description":
     "Press a media key: mute, volume_up, volume_down, play_pause, next, prev.",
     "parameters": {"type": "OBJECT",
                    "properties": {"action": {"type": "STRING"}},
                    "required": ["action"]}},
    {"name": "mtg_card", "description":
     "Look up a Magic: The Gathering card on Scryfall — rules text, type, "
     "mana cost, market price. Free, no key. Great for deck talk.",
     "parameters": {"type": "OBJECT",
                    "properties": {"card_name": {"type": "STRING"}},
                    "required": ["card_name"]}},
    {"name": "watch_price", "description":
     "Watch a product URL and speak an alert when its price drops at or "
     "below target_price (a number like 40). Checked hourly.",
     "parameters": {"type": "OBJECT",
                    "properties": {"url": {"type": "STRING"},
                                   "target_price": {"type": "STRING"},
                                   "label": {"type": "STRING"}},
                    "required": ["url", "target_price"]}},
    {"name": "list_price_watches", "description":
     "List active price watches."},
    {"name": "unwatch_price", "description":
     "Remove a price watch by its #id.",
     "parameters": {"type": "OBJECT",
                    "properties": {"watch_id": {"type": "INTEGER"}},
                    "required": ["watch_id"]}},
    {"name": "face_tracking", "description":
     "Turn camera face-tracking on/off. When on, the neck servos follow "
     "the user's face around the room.",
     "parameters": {"type": "OBJECT",
                    "properties": {"on": {"type": "BOOLEAN"}}}},
    {"name": "show_commands", "description":
     "Shows the on-screen commands reference panel: every tool I have, "
     "grouped, each with an example phrase. Use when the user asks what I "
     "can do, to show commands, or wants help / the manual.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "hide_commands", "description":
     "Hides the on-screen commands reference panel.",
     "parameters": {"type": "OBJECT", "properties": {}}},
]

RISKY_TOOLS.add("close_window")

# ============================================================
# TOOLKITS - progressive tool-schema loading (Leon-inspired)
# ============================================================
# Only the "core" toolkit's schemas go to the model on every call.
# Specialist tools live in named toolkits; the model unlocks them with
# load_toolkit. Cuts the dominant token cost (51 schemas x 20 turns)
# and shrinks the wrong-tool confusion surface.
TOOLKITS = {
    "core": {
        "summary": "everyday tools: web search, open apps/URLs, run Python code, "
                   "click/type, screenshots, screen reading, clipboard, files, "
                   "memory save/search, bounded workflow skills",
        "tools": ["web_search", "open_app_or_url", "run_python_code", "gui_click",
                  "gui_type", "save_memory", "search_memory", "take_screenshot",
                  "read_screen", "fetch_url", "clipboard_read", "clipboard_write",
                  "find_file", "read_file", "write_file", "list_workspace",
                  "run_skill"]},
    "comms": {
        "summary": "Gmail: store credentials, send email",
        "tools": ["gmail_setup", "send_email"]},
    "spotify": {
        "summary": "music: Spotify control, DJ mode, media keys",
        "tools": ["spotify", "dj", "media_key"]},
    "scheduler": {
        "summary": "reminders, spoken timers, recurring tasks, break nudges",
        "tools": ["set_reminder", "set_recurring_task", "list_scheduled_tasks",
                  "cancel_scheduled_task", "set_timer", "break_reminders"]},
    "github": {
        "summary": "push files and create GitHub repos",
        "tools": ["github_push_file", "github_create_repo"]},
    "vision": {
        "summary": "webcam photos, face tracking, neck servos",
        "tools": ["take_photo", "face_tracking", "move_head_servos"]},
    "windows": {
        "summary": "list, focus, minimize, close windows",
        "tools": ["list_windows", "focus_window", "minimize_window",
                  "close_window"]},
    "mtg": {
        "summary": "Magic card lookup, Commander deck advice, price watches",
        "tools": ["mtg_card", "mtg_advice", "watch_price", "list_price_watches",
                  "unwatch_price"]},
    "memory_plus": {
        "summary": "notes, journal, forgetting memories, morning briefing",
        "tools": ["forget_memory", "journal_write", "take_note", "read_notes",
                  "morning_briefing"]},
    "admin": {
        "summary": "API keys, bridge token, command guide, volume",
        "tools": ["gemini_keys", "bridge_token", "show_commands", "hide_commands",
                  "volume"]},
}
_LOADED_TOOLKITS = set()  # per-request; reset at each run_agent entry
_TURN_CALLS = {}          # per-turn (fn, args) signature -> result text


def _tool_decls_by_name():
    return {d["name"]: d for d in TOOLS_DECLARATION[0]["function_declarations"]}


def _toolkit_declarations():
    """Build the tools payload from currently loaded toolkits."""
    decls = _tool_decls_by_name()
    out = []
    for tk in sorted(_LOADED_TOOLKITS):
        for name in TOOLKITS[tk]["tools"]:
            if name in decls:
                out.append(decls[name])
    out.append({
        "name": "load_toolkit",
        "description":
            "Unlock a toolkit's tools for this request. Call it with a toolkit "
            "name from the Toolkits list, then use that toolkit's tools on the "
            "next turn.",
        "parameters": {"type": "OBJECT",
                       "properties": {"toolkit": {"type": "STRING"}},
                       "required": ["toolkit"]}})
    return [{"function_declarations": out}]


def _toolkits_prompt_block():
    lines = [
        "Toolkits: only the CORE tools are loaded right now. Specialist tools "
        "live in named toolkits - call load_toolkit with the toolkit name to "
        "unlock its tools, then use them on the next turn. Available toolkits:"]
    for name in sorted(TOOLKITS):
        if name != "core":
            lines.append(f"- {name}: {TOOLKITS[name]['summary']}")
    return " ".join(lines) + " "


def tool_load_toolkit(name):
    name = (name or "").strip().lower()
    if name in TOOLKITS:
        if name in _LOADED_TOOLKITS:
            return f"Toolkit '{name}' is already loaded."
        _LOADED_TOOLKITS.add(name)
        return (f"Toolkit '{name}' loaded. You can now call: "
                f"{', '.join(TOOLKITS[name]['tools'])}.")
    return (f"Unknown toolkit '{name}'. Available: "
            f"{', '.join(sorted(TOOLKITS))}.")


# ---------------- duplicate-call blocking + argument repair ----------------
def _call_signature(fn_name, args):
    try:
        blob = json.dumps(args or {}, sort_keys=True, default=str)
    except Exception:
        blob = str(args)
    return fn_name + ":" + hashlib.md5(blob.encode("utf-8")).hexdigest()


def _missing_required_args(fn_name, args):
    """Required params from the tool's own declaration that are missing/empty."""
    decl = _tool_decls_by_name().get(fn_name)
    if not decl:
        return []
    required = (decl.get("parameters") or {}).get("required", [])
    args = args or {}
    return [p for p in required if not args.get(p)]



# ---------------- Commands reference (HUD overlay + phone /commands page) ----------------
# Single source of truth: (category, tool_name, example phrase). The HUD overlay
# and the phone bridge /commands page both render from this table, so they
# cannot drift apart. Keep every declared tool listed here.
COMMAND_GUIDE = [
    ("Memory", "save_memory", "remember my Doja playlist is spotify:playlist:xxx"),
    ("Memory", "search_memory", "what do you remember about my shifts?"),
    ("Memory", "forget_memory", "forget my old playlist"),
    ("Memory", "journal_write", "write a journal entry about today"),
    ("Web", "web_search", "search the web for Zero 2 W stock"),
    ("Web", "fetch_url", "fetch this page and summarize it"),
    ("Web", "run_skill", "research the new Zelda DLC with deep_research"),
    ("Laptop", "open_app_or_url", "open Spotify"),
    ("Laptop", "gui_click", "click the Play button"),
    ("Laptop", "gui_type", "type hello world"),
    ("Laptop", "list_windows", "what windows are open?"),
    ("Laptop", "focus_window", "focus the Spotify window"),
    ("Laptop", "minimize_window", "minimize this window"),
    ("Laptop", "close_window", "close the Calculator window"),
    ("Laptop", "clipboard_read", "what is in my clipboard?"),
    ("Laptop", "clipboard_write", "copy this tracking number to my clipboard"),
    ("Laptop", "media_key", "press the mute key"),
    ("Laptop", "volume", "set volume to 40"),
    ("Laptop", "run_python_code", "run a Python script to rename those files"),
    ("Laptop", "write_file", "save this as gig-ideas.txt"),
    ("Laptop", "read_file", "read gig-ideas.txt back to me"),
    ("Laptop", "list_workspace", "what is in your workspace?"),
    ("Laptop", "find_file", "find my resume PDF"),
    ("Seeing", "take_screenshot", "take a screenshot"),
    ("Seeing", "read_screen", "what does this error say?"),
    ("Seeing", "take_photo", "take a photo"),
    ("Time", "set_timer", "set a 20 minute timer"),
    ("Time", "set_reminder", "remind me at 6pm to take the trash out"),
    ("Time", "set_recurring_task", "remind me every weekday at 8am to check tips"),
    ("Time", "list_scheduled_tasks", "what reminders do I have?"),
    ("Time", "cancel_scheduled_task", "cancel my 6pm reminder"),
    ("Time", "morning_briefing", "brief me"),
    ("Time", "break_reminders", "turn my break reminders off"),
    ("Music", "spotify", "search Spotify for Doja Cat"),
    ("Music", "spotify", "play my liked songs playlist"),
    ("Music", "dj", "shuffle my liked songs"),
    ("Music", "dj", "play something chill"),
    ("Notes", "take_note", "note to self: buy milk"),
    ("Notes", "read_notes", "what were my notes Tuesday?"),
    ("MTG", "open_app_or_url", "let's play some magic"),
    ("MTG", "mtg_card", "look up Teval, Arbiter of Virtue"),
    ("MTG", "mtg_advice", "is Doubling Season good in Teval?"),
    ("Prices", "watch_price", "watch the price of the Galaxy Tab S9 FE"),
    ("Prices", "list_price_watches", "what prices are you watching?"),
    ("Prices", "unwatch_price", "stop watching the tablet"),
    ("Face & body", "face_tracking", "follow my face"),
    ("Face & body", "move_head_servos", "look left"),
    ("Phone & keys", "gemini_keys", "check my Gemini keys"),
    ("Phone & keys", "bridge_token", "what is my bridge token?"),
    ("GitHub", "github_push_file", "push this file to my repo"),
    ("GitHub", "github_create_repo", "create a repo called gig-tracker"),
    ("Email", "gmail_setup", "save my Gmail and app password"),
    ("Email", "send_email", "email mom the deck list"),
    ("This screen", "show_commands", "show commands"),
    ("This screen", "hide_commands", "hide commands"),
]

SHOW_COMMANDS = False
COMMANDS_PAGE = 0
COMMANDS_PAGES = []


def _build_commands_pages():
    rows = []
    last_cat = None
    for cat, tool, ex in COMMAND_GUIDE:
        if cat != last_cat:
            rows.append(("cat", cat, ""))
            last_cat = cat
        rows.append(("cmd", tool, ex))
    ROWS_PER_COL = 24
    pages = []
    for i in range(0, len(rows), ROWS_PER_COL * 2):
        chunk = rows[i:i + ROWS_PER_COL * 2]
        pages.append((chunk[:ROWS_PER_COL], chunk[ROWS_PER_COL:]))
    return pages


def _draw_commands_overlay(canvas):
    global COMMANDS_PAGES, COMMANDS_PAGE
    if not COMMANDS_PAGES:
        COMMANDS_PAGES = _build_commands_pages()
    CYAN = (255, 220, 30)
    PINK = (162, 95, 255)
    WHITE_TEXT = (235, 242, 255)
    DIM = (150, 160, 170)
    dim = canvas.copy()
    cv2.rectangle(dim, (0, 0), (1280, 720), (8, 10, 14), -1)
    cv2.addWeighted(dim, 0.88, canvas, 0.12, 0, canvas)
    cv2.rectangle(canvas, (36, 52), (1244, 700), (15, 17, 22), -1)
    cv2.rectangle(canvas, (36, 52), (1244, 700), (45, 50, 60), 1)
    cv2.putText(canvas, "COMMANDS - say what you see", (60, 88),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, CYAN, 2, cv2.LINE_AA)
    page = COMMANDS_PAGES[COMMANDS_PAGE % len(COMMANDS_PAGES)]
    for col, rows in enumerate(page):
        x = 70 + col * 590
        y = 126
        for kind, a, b in rows:
            if kind == "cat":
                y += 6
                cv2.putText(canvas, a.upper(), (x, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, PINK, 1, cv2.LINE_AA)
                y += 22
            else:
                cv2.putText(canvas, a, (x, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, CYAN, 1, cv2.LINE_AA)
                tw = cv2.getTextSize(a + " ", cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)[0][0]
                cv2.putText(canvas, '- "' + b[:48] + '"', (x + tw, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, WHITE_TEXT, 1, cv2.LINE_AA)
                y += 21
    footer = ("H: close   [ / ]: page %d/%d   (or say 'hide commands')"
              % (COMMANDS_PAGE % len(COMMANDS_PAGES) + 1, len(COMMANDS_PAGES)))
    cv2.putText(canvas, footer, (60, 684),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, DIM, 1, cv2.LINE_AA)


def commands_show():
    global SHOW_COMMANDS, COMMANDS_PAGE
    SHOW_COMMANDS = True
    COMMANDS_PAGE = 0
    draw_hud()
    return "Commands panel shown (press H or say 'hide commands' to close)."


def commands_hide():
    global SHOW_COMMANDS
    SHOW_COMMANDS = False
    draw_hud()
    return "Commands panel hidden."


def _commands_html():
    parts = []
    last = None
    for cat, tool, ex in COMMAND_GUIDE:
        if cat != last:
            if last is not None:
                parts.append("</div>")
            parts.append("<h3>" + cat + "</h3><div class='grp'>")
            last = cat
        parts.append("<div class='cmd' data-t='" + tool + " " + ex + " " + cat +
                     "'><b>" + tool + "</b><span>&quot;" + ex + "&quot;</span></div>")
    parts.append("</div>")
    return ("<!DOCTYPE html><html><head><meta name='viewport' "
            "content='width=device-width,initial-scale=1'>"
            "<title>A.R.I.A. Commands</title><style>"
            "body{background:#0b0e12;color:#e8f4ff;font-family:sans-serif;margin:0;padding:16px}"
            "h2{color:#ff5fa2}h3{color:#1edcff;margin:18px 0 6px}"
            ".cmd{background:#14181d;border:1px solid #2a3138;border-radius:8px;"
            "padding:10px;margin-bottom:6px}.cmd b{color:#1edcff;display:block}"
            ".cmd span{color:#9fb2c3;font-size:14px}"
            "#q{width:100%;padding:12px;border-radius:8px;border:1px solid #2a3138;"
            "background:#14181d;color:#fff;font-size:16px;box-sizing:border-box}"
            "a{color:#ff5fa2}</style></head><body>"
            "<h2>A.R.I.A. // Commands</h2>"
            "<input id='q' placeholder='Filter commands...' oninput='f()'>"
            "<div id='list'>" + "".join(parts) + "</div>"
            "<p><a href='/'>&larr; Bridge</a></p>"
            "<script>function f(){var q=document.getElementById('q').value.toLowerCase();"
            "document.querySelectorAll('.cmd').forEach(function(e){"
            "e.style.display=e.getAttribute('data-t').toLowerCase().indexOf(q)>=0?'':'none';});"
            "document.querySelectorAll('h3').forEach(function(h){var s=h.nextElementSibling;"
            "var vis=false;s.querySelectorAll('.cmd').forEach(function(c){"
            "if(c.style.display!=='none')vis=true;});"
            "h.style.display=vis?'':'none';s.style.display=vis?'':'none';});}</script>"
            "</body></html>")


# =====================================================================
# 13. UPGRADES — Spotify, briefing, timers, screenshot, OCR, photo,
#     file finder, volume, MTG advice, break nudges, bridge voice chat,
#     self-check. (Download watcher dropped at Alek's request.)
# =====================================================================

# ---------------- Spotify voice control ----------------
def _tap_media_vk(vk):
    try:
        import ctypes
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        return True
    except Exception:
        return False


def tool_spotify(action, query=""):
    a = str(action).lower().replace(" ", "_")
    if a in ("play_pause", "play", "pause", "toggle"):
        ok = _tap_media_vk(0xB3)
        return "Spotify: toggled play/pause." if ok else "[Media keys unavailable on this machine.]"
    if a in ("next", "skip"):
        ok = _tap_media_vk(0xB0)
        return "Spotify: next track." if ok else "[Media keys unavailable.]"
    if a in ("previous", "prev", "back"):
        ok = _tap_media_vk(0xB1)
        return "Spotify: previous track." if ok else "[Media keys unavailable.]"
    if a == "open":
        try:
            os.startfile("spotify:")
            return "Spotify: opening the app."
        except Exception as e:
            return f"[Could not open Spotify: {e}]"
    if a == "search":
        if not query:
            return "[Tell me what to search for.]"
        try:
            os.startfile("spotify:search:" + urllib.parse.quote(str(query)))
            return f"Spotify: searching for '{query}'. Tap a result to play it."
        except Exception as e:
            return f"[Could not open Spotify search: {e}]"
    if a == "play_uri":
        if not query:
            return "[Give me a spotify: URI, e.g. spotify:playlist:xxx.]"
        try:
            os.startfile(str(query))
        except Exception as e:
            return f"[Could not open that URI: {e}]"
        time.sleep(2.5)  # let Spotify load the playlist context
        if _tap_media_vk(0xB3):  # play key - actually starts the music
            return f"Spotify: playing {query}."
        return f"Spotify: opened {query} but the play key didn't respond - press play."
    return "[Unknown Spotify action - open, play_pause, next, previous, search, play_uri.]"


# ---------------- Schedule + morning briefing + greeting ----------------
SCHEDULE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "aria_schedule.json")

_SCHEDULE_SEED = [
    ("2026-09-27", "14:00", "18:30", "Dock of the Bay \u2014 Bar"),
    ("2026-09-28", "10:30", "11:30", "Smiles R Us dentist"),
    ("2026-09-28", "15:00", "19:30", "Dock of the Bay \u2014 Wait"),
    ("2026-09-29", "16:00", "20:30", "Dock of the Bay \u2014 Wait"),
    ("2026-10-03", "16:00", "20:30", "Dock of the Bay \u2014 Bar"),
    ("2026-10-05", "15:00", "21:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-06", "16:00", "21:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-07", "16:00", "21:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-09", "12:00", "21:00", "Dock of the Bay \u2014 Wait (bartender from 4 PM)"),
    ("2026-10-10", "11:30", "17:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-11", "11:30", "17:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-12", "15:00", "21:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-14", "16:00", "21:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-17", "15:00", "21:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-19", "15:00", "21:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-20", "11:30", "17:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-21", "11:30", "17:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-22", "11:30", "17:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-23", "11:30", "17:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-23", "22:00", "23:59", "Poe Speakeasy, Annapolis (2 tickets)"),
    ("2026-10-24", "14:00", "21:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-25", "16:00", "21:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-26", "15:00", "21:00", "Dock of the Bay \u2014 Wait"),
    ("2026-10-28", "16:00", "21:00", "Dock of the Bay \u2014 Wait"),
    ("2026-11-20", "19:30", "23:00", "Doja Cat \u2014 Tour Ma Vie, CFG Bank Arena Baltimore"),
    ("2026-12-02", "10:30", "11:30", "Smiles R Us dentist follow-up"),
]


def _load_schedule():
    if not os.path.exists(SCHEDULE_FILE):
        try:
            with open(SCHEDULE_FILE, "w") as f:
                json.dump([{"date": d, "start": s, "end": e, "title": t}
                           for d, s, e, t in _SCHEDULE_SEED], f, indent=2)
        except Exception:
            return []
    try:
        with open(SCHEDULE_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _fmt_time(hhmm):
    try:
        h, m = map(int, str(hhmm).split(":"))
        ap = "AM" if h < 12 else "PM"
        return f"{h % 12 or 12}:{m:02d} {ap}"
    except Exception:
        return hhmm


def _today_entries():
    today = datetime.now().strftime("%Y-%m-%d")
    return sorted([e for e in _load_schedule() if e.get("date") == today],
                  key=lambda e: e.get("start", ""))


def _entries_line(entries):
    return "; ".join(f"{e.get('title', '')} {_fmt_time(e.get('start', ''))}"
                     f" to {_fmt_time(e.get('end', ''))}" for e in entries)


def _weather_now():
    try:
        req = urllib.request.Request(
            "https://wttr.in/Dundalk,Maryland?format=%t+%C",
            headers={"User-Agent": "ARIA-Agent/8.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _weather_full():
    """Full Dundalk weather from wttr.in JSON — current temp (F),
    condition, today's high/low (F). Returns a dict, or None on any failure
    (caller falls back to _weather_now)."""
    try:
        req = urllib.request.Request(
            "https://wttr.in/Dundalk,Maryland?format=j1",
            headers={"User-Agent": "ARIA-Agent/8.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8", errors="replace"))
        cur = d["current_condition"][0]
        day = d["weather"][0]
        desc = (cur.get("weatherDesc") or [{}])[0].get("value", "").strip()
        return {"temp_F": str(cur.get("temp_F", "")).strip(),
                "desc": desc,
                "high_F": str(day.get("maxtempF", "")).strip(),
                "low_F": str(day.get("mintempF", "")).strip()}
    except Exception:
        return None


def tool_briefing():
    # richer weather (temp + high/low from wttr.in j1, falling back to
    # the old format-string call) and a tomorrow preview. Still 3-5 sentences,
    # still fully deterministic — no model involved.
    from datetime import timedelta
    now = datetime.now()
    day = now.strftime("%A, %B %d")
    entries = _today_entries()
    sched = f"You've got: {_entries_line(entries)}." if entries else "Nothing on the schedule today."
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    t_entries = sorted([e for e in _load_schedule() if e.get("date") == tomorrow],
                       key=lambda e: e.get("start", ""))
    tmrw = f" Tomorrow: {_entries_line(t_entries)}." if t_entries else ""
    wf = _weather_full()
    if wf and wf["temp_F"]:
        cond = f" and {wf['desc']}" if wf["desc"] else ""
        wxbit = (f" In Dundalk it's {wf['temp_F']}{cond},"
                 f" high of {wf['high_F']}, low of {wf['low_F']}.")
    else:
        wx = _weather_now()
        wxbit = f" In Dundalk it's {wx}." if wx else ""
    return f"{day}. {sched}{wxbit}{tmrw}"


def _greeting_text():
    h = datetime.now().hour
    part = "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"
    base = "A.R.I.A. online."
    entries = _today_entries()
    if entries:
        return f"{part}. {base} Today: {_entries_line(entries)}."
    return f"{part}. {base} Nothing on the schedule today."


def _unique_greeting():
    """Startup greeting, written fresh by the model every boot.

    Soul-aware, schedule-aware, and told not to repeat the previous greeting
    (stored as a system memory). Falls back to the template greeting offline.
    """
    fallback = _greeting_text()
    try:
        entries = _today_entries()
        sched = _entries_line(entries) if entries else "nothing on the schedule"
        h = datetime.now().hour
        part = "morning" if h < 12 else "afternoon" if h < 18 else "evening"
        last = ""
        try:
            with _db() as db:
                r = db.execute("SELECT value FROM memory WHERE category='system'"
                               " AND key='_last_greeting'").fetchone()
                last = r[0] if r else ""
        except Exception:
            pass
        data = _gemini_call(
            "You write A.R.I.A.'s spoken startup greeting for Alek. One or two "
            "sentences, warm, direct, a little playful - in her voice. Vary the "
            "opening; don't always start with 'Good morning/afternoon/evening'. "
            "Make it different from her previous greeting, quoted below. Reply "
            "with ONLY the greeting text, no quotes.",
            [{"role": "user", "parts": [{"text":
                f"It's {part}. Today's schedule: {sched}. "
                f"Previous greeting (do not repeat): {last or 'none yet'}."}]}],
            include_tools=False)
        text = ""
        if data:
            for cand in data.get("candidates", []):
                for p in cand.get("content", {}).get("parts", []):
                    if "text" in p:
                        text += p["text"]
        text = text.strip().strip('"').strip()
        if text and len(text) < 400:
            try:
                with _db() as db:
                    db.execute("INSERT OR REPLACE INTO memory(category,key,value)"
                               " VALUES('system','_last_greeting',?)", (text[:300],))
            except Exception:
                pass
            return text
    except Exception as e:
        add_log(f"Unique greeting failed, using template: {e}")
    return fallback


# ---------------- Quick timers ----------------
def _parse_duration(s):
    import re
    s = str(s).lower().strip()
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)(?![a-zA-Z])", s):
        n = float(num)
        u = unit[0]
        total += n * 3600 if u == "h" else n * 60 if u == "m" else n
    if total == 0 and s.replace(".", "", 1).isdigit():
        total = float(s) * 60  # bare number = minutes
    return int(total)


def tool_set_timer(duration_text, label="timer"):
    secs = _parse_duration(duration_text)
    if secs <= 0:
        return "[I couldn't understand that duration - try '20 minutes' or '1h30m'.]"

    def _fire():
        add_log(f"Timer fired: {label}")
        speak(f"Timer done: {label}.")

    threading.Timer(secs, _fire).start()
    return f"Timer set: {label}, {duration_text} from now."


# ---------------- Screenshot / photo ----------------
def _safe_name(name, default):
    safe = "".join(c for c in str(name) if c.isalnum() or c in ("-", "_", " "))[:40].strip()
    return safe or default


def tool_screenshot(name=""):
    d = os.path.join(WORKSPACE_DIR, "screenshots")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, _safe_name(name, datetime.now().strftime("shot_%Y%m%d_%H%M%S")) + ".png")
    try:
        if GUI_AVAILABLE:
            import pyautogui
            pyautogui.screenshot(path)
        else:
            ImageGrab.grab().save(path)
        return f"Screenshot saved: {path}"
    except Exception as e:
        return f"[Screenshot failed: {e}]"


def tool_take_photo(name=""):
    frame = capture_webcam()
    if frame is None and LATEST_CAMERA_FRAME is None:
        return "[Camera not available right now.]"
    d = os.path.join(WORKSPACE_DIR, "photos")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, _safe_name(name, datetime.now().strftime("photo_%Y%m%d_%H%M%S")) + ".jpg")
    try:
        cv2.imwrite(path, LATEST_CAMERA_FRAME)
        return f"Photo saved: {path}"
    except Exception as e:
        return f"[Photo save failed: {e}]"


# ---------------- Screen reading via Gemini vision ----------------
def _gemini_text(system_instruction, contents):
    """One text answer from Gemini, no tools attached."""
    try:
        data = _gemini_call(system_instruction, contents, include_tools=False)
        if not data:
            return "[Gemini returned nothing.]"
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip() or "[No text in response.]"
    except Exception as e:
        return f"[Gemini error: {e}]"


def tool_read_screen(question=""):
    try:
        data = capture_screen()
    except Exception as e:
        return f"[Screen capture failed: {e}]"
    q = question or "Read all text visible on this screen, top to bottom. Be concise."
    b64 = base64.b64encode(data).decode()
    contents = [{"role": "user", "parts": [
        {"text": q},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64}}]}]
    return _gemini_text("You are a precise screen reader. Answer only what is asked, concisely.",
                       contents)


# ---------------- File finder ----------------
def tool_find_file(name, ext=""):
    name = str(name).lower()
    ext = str(ext).lower().lstrip(".")
    roots = []
    for var in ("USERPROFILE", "HOME"):
        home = os.environ.get(var)
        if home:
            for sub in ("Desktop", "Documents", "Downloads"):
                p = os.path.join(home, sub)
                if os.path.isdir(p):
                    roots.append(p)
    if os.path.isdir("E:\\"):
        roots.append("E:\\")
    skip = {"appdata", "node_modules", ".git", "__pycache__", "site-packages"}
    hits = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath[len(root):].count(os.sep) > 4:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in skip and not d.startswith(".")]
            for fn in filenames:
                if name in fn.lower() and (not ext or fn.lower().endswith("." + ext)):
                    hits.append(os.path.join(dirpath, fn))
                    if len(hits) >= 25:
                        return "Found (first 25):\n" + "\n".join(hits)
    if not hits:
        return f"[No files matching '{name}' found.]"
    return "Found:\n" + "\n".join(hits)


# ---------------- Volume (pycaw) ----------------
def tool_volume(action="status", level=50):
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
    except ImportError:
        return "[Volume control needs pycaw - run: python -m pip install pycaw]"
    try:
        dev = AudioUtilities.GetSpeakers()
        iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(iface, POINTER(IAudioEndpointVolume))
        a = str(action).lower()
        if a == "set":
            pct = max(0, min(100, int(level)))
            vol.SetMasterVolumeLevelScalar(pct / 100.0, None)
            return f"Volume set to {pct}%."
        if a == "mute":
            vol.SetMute(1, None)
            return "Muted."
        if a == "unmute":
            vol.SetMute(0, None)
            return "Unmuted."
        if a == "up":
            vol.SetMasterVolumeLevelScalar(min(1.0, vol.GetMasterVolumeLevelScalar() + 0.1), None)
        elif a == "down":
            vol.SetMasterVolumeLevelScalar(max(0.0, vol.GetMasterVolumeLevelScalar() - 0.1), None)
        cur = int(vol.GetMasterVolumeLevelScalar() * 100)
        return f"Volume is at {cur}%."
    except Exception as e:
        return f"[Volume control failed: {e}]"


# ---------------- MTG deck advice ----------------
def tool_mtg_advice(deck, card_name):
    data, err = _scryfall_get("https://api.scryfall.com/cards/named?fuzzy="
                              + urllib.parse.quote(str(card_name)))
    if err or not data or "name" not in data:
        return f"[Couldn't fetch '{card_name}': {err or 'not found'}]"
    card_txt = (f"{data.get('name')} - {data.get('mana_cost', '')} {data.get('type_line', '')}\n"
                f"{data.get('oracle_text', '')}\nEDHREC rank: {data.get('edhrec_rank', 'n/a')}")
    contents = [{"role": "user", "parts": [{"text":
        f"The user plays high-power Commander with this deck: {deck}.\n"
        f"Card data:\n{card_txt}\n"
        "Give a short verdict: does this card earn a slot? 2-4 sentences - "
        "say what it replaces or what it enables."}]}]
    return _gemini_text("You are a high-power Commander deck consultant. Be direct and opinionated.",
                       contents)


# ---------------- Break reminders (toggleable) ----------------
BREAK_REMINDERS = True


def tool_break_reminders(action="status"):
    global BREAK_REMINDERS
    a = str(action).lower()
    if a in ("on", "enable"):
        BREAK_REMINDERS = True
        return "Break reminders on - I'll nudge you every 90 minutes of active time."
    if a in ("off", "disable"):
        BREAK_REMINDERS = False
        return "Break reminders off."
    return f"Break reminders are {'on' if BREAK_REMINDERS else 'off'}."


# ---------------- Gemini key pool management ----------------
def tool_gemini_keys(action="status", key=""):
    """status/add/remove Gemini API keys in the rotation pool."""
    global GEMINI_KEY_POOL
    a = str(action).lower().strip()
    now = time.time()
    if a == "status":
        if not GEMINI_KEY_POOL:
            return "No Gemini API keys configured. Add one with the gemini_keys tool (action: add)."
        lines = []
        for i, k in enumerate(GEMINI_KEY_POOL):
            quar = _KEY_QUARANTINE_UNTIL.get(i, 0) - now
            cool = _KEY_COOLDOWN_UNTIL.get(i, 0) - now
            if quar > 0:
                qcode = _KEY_QUARANTINE_CODE.get(i)
                why = f"HTTP {qcode}" if qcode else "key rejected by Google"
                state = f"QUARANTINED ({int(quar)}s left - {why})"
            elif cool > 0:
                state = f"cooling ({int(cool)}s left)"
            else:
                state = "ready"
            lines.append(f"#{i + 1} {_key_mask(k)} - {state}")
        return f"{len(GEMINI_KEY_POOL)} key(s) in pool:\n" + "\n".join(lines)
    if a == "add":
        k = str(key).strip()
        if not ((k.startswith("AIza") or k.startswith("AQ.")) and len(k) >= 20):
            return "That doesn't look like a Gemini API key (expected AIza... or AQ... with 20+ chars). Not added."
        if k in GEMINI_KEY_POOL:
            return "That key is already in the pool."
        GEMINI_KEY_POOL.append(k)
        _KEYS["GEMINI_API_KEYS"] = list(GEMINI_KEY_POOL)
        _KEYS.pop("GEMINI_API_KEY", None)
        ok = _save_keys()
        _KEY_COOLDOWN_UNTIL.pop(len(GEMINI_KEY_POOL) - 1, None)
        _KEY_QUARANTINE_UNTIL.pop(len(GEMINI_KEY_POOL) - 1, None)
        _KEY_QUARANTINE_CODE.pop(len(GEMINI_KEY_POOL) - 1, None)
        return (f"Key added - pool now has {len(GEMINI_KEY_POOL)} key(s)."
                + ("" if ok else " (warning: could not persist to keys file)"))
    if a == "remove":
        try:
            i = int(str(key).strip()) - 1
            assert 0 <= i < len(GEMINI_KEY_POOL)
        except Exception:
            return f"Remove which? Give the key number from status (1-{len(GEMINI_KEY_POOL)})."
        gone = _key_mask(GEMINI_KEY_POOL.pop(i))
        _KEY_COOLDOWN_UNTIL.pop(i, None)
        _KEY_QUARANTINE_UNTIL.pop(i, None)
        _KEY_QUARANTINE_CODE.pop(i, None)
        _KEYS["GEMINI_API_KEYS"] = list(GEMINI_KEY_POOL)
        ok = _save_keys()
        return (f"Removed key {gone} - pool now has {len(GEMINI_KEY_POOL)} key(s)."
                + ("" if ok else " (warning: could not persist to keys file)"))
    return "Unknown action. Use status, add, or remove."


# ---------------- Bridge voice helpers ----------------
def _edge_tts_bytes(text):
    """Synthesize with Edge TTS, return MP3 bytes (no temp-file lock issues)."""
    import asyncio
    import edge_tts

    async def _gen():
        chunks = []
        async for chunk in edge_tts.Communicate(text, EDGE_TTS_VOICE).stream():
            if chunk.get("type") == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    return asyncio.run(asyncio.wait_for(_gen(), timeout=max(30, int(len(text) * 0.1))))


def _transcribe_audio(audio_bytes, mime="audio/webm"):
    b64 = base64.b64encode(audio_bytes).decode()
    contents = [{"role": "user", "parts": [
        {"text": "Transcribe this voice command exactly. Output only the transcription, no commentary."},
        {"inline_data": {"mime_type": mime.split(";")[0], "data": b64}}]}]
    return _gemini_text("You are a speech transcriber.", contents).strip().strip('"')


def _bridge_process(text, silent):
    """Shared by /api/ask and /api/voice: confirmations first, then the agent."""
    if (text or "").strip():
        interrupt_speech()  # barge-in: new bridge input stops current speech
    sink = []
    if _resolve_confirmation(text, reply_sink=sink):
        return " ".join(sink) if sink else "Done."
    run_agent(text, reply_sink=sink, silent=silent)
    return " ".join(sink) if sink else "Done."


# ---------------- Startup self-check ----------------
def _self_check():
    print("[ARIA] Self-check:", flush=True)
    print(f"  Python {sys.version.split()[0]}", flush=True)
    try:
        import aiohttp
        v = aiohttp.__version__
        warn = ""
        if sys.version_info < (3, 10):
            try:
                if tuple(int(x) for x in v.split(".")[:2]) > (3, 10):
                    warn = '  <-- too new for Python 3.9: run python -m pip install "aiohttp==3.10.11"'
            except Exception:
                pass
        print(f"  aiohttp {v} OK{warn}", flush=True)
    except Exception as e:
        print(f"  aiohttp MISSING: {e}", flush=True)
    for mod, label in (("edge_tts", "edge-tts"), ("pygame", "pygame"),
                       ("pyautogui", "pyautogui"), ("PIL", "pillow"),
                       ("pycaw", "pycaw (volume tool)")):
        try:
            __import__(mod)
            print(f"  {label}: OK", flush=True)
        except Exception as e:
            print(f"  {label}: MISSING ({e})", flush=True)
    print(f"  Gemini keys: {len(GEMINI_KEY_POOL)} in pool", flush=True)
    print(f"  GitHub token: {'present' if GITHUB_ARMED else 'missing'}", flush=True)
    print(f"  Bridge token: {BRIDGE_TOKEN if BRIDGE_TOKEN else 'missing'}\n"
          "  ^ enter this on your iPhone bridge page (saved after first entry)", flush=True)


if __name__ == "__main__":
    print("\n" + "=" * 75)
    print(" A.R.I.A. AUTONOMOUS ROBOTIC INTELLIGENCE AGENT")
    print(" Edge voice | semantic memory | scheduler | confirmations | phone bridge | living face")
    print(f" Phone: {BRIDGE_SCHEME}://{lan_ip()}:{PHONE_BRIDGE_PORT}  (open on your iPhone)")
    if BRIDGE_SCHEME == "https":
        print(" iPhone: first visit shows a cert warning - Show Details > visit this website, then mic works.")
    else:
        print(" Bridge is HTTP (unexpected): cert files could not be written - iPhone mic needs HTTPS.")
    print(f" Bridge token: {BRIDGE_TOKEN}  (enter it once on your iPhone)")
    print(" Controls:")
    print("  - Say 'hey A.R.I.A.' + command (hands-free)")
    print("  - [SPACEBAR] : Manual voice input")
    print("  - [S]        : Screen perception (uploads only if changed)")
    print("  - [H]        : Toggle commands reference")
    print("  - [C]        : Toggle tactical chat-log HUD view")
    print("  - [L]        : Open master chat log (non-blocking)")
    print("  - [T]        : Type a directive")
    print("  - [Q]        : Disengage / shutdown")
    print("=" * 75 + "\n")

    _self_check()
    draw_hud()
    threading.Thread(target=_keys_startup_report, daemon=True).start()
    def _greet_when_ready():
        for _ in range(250):  # up to ~25s for the Edge voice
            if _EDGE_READY:
                break
            time.sleep(0.1)
        if not _EDGE_READY:
            print("[ARIA] Voice: Edge not ready in time - greeting in fallback voice.",
                  flush=True)
        speak(_unique_greeting())
    threading.Thread(target=_greet_when_ready, daemon=True).start()

    while True:
        draw_hud()
        key = cv2.waitKey(100) & 0xFF

        if HUD_MODE == "chat_log" and key in (ord('j'), ord('J')):
            CHAT_SCROLL += 5  # scroll toward older lines
        elif HUD_MODE == "chat_log" and key in (ord('k'), ord('K')):
            CHAT_SCROLL = max(0, CHAT_SCROLL - 5)  # scroll toward newer lines
        elif key == ord('h') or key == ord('H'):
            SHOW_COMMANDS = not SHOW_COMMANDS
            COMMANDS_PAGE = 0
            add_log("Commands panel: %s" % ("shown" if SHOW_COMMANDS else "hidden"))
        elif key == ord('[') and SHOW_COMMANDS:
            if not COMMANDS_PAGES:
                COMMANDS_PAGES = _build_commands_pages()
            COMMANDS_PAGE = (COMMANDS_PAGE - 1) % len(COMMANDS_PAGES)
        elif key == ord(']') and SHOW_COMMANDS:
            if not COMMANDS_PAGES:
                COMMANDS_PAGES = _build_commands_pages()
            COMMANDS_PAGE = (COMMANDS_PAGE + 1) % len(COMMANDS_PAGES)
        elif key == ord('c') or key == ord('C'):
            HUD_MODE = "chat_log" if HUD_MODE == "visor" else "visor"
            add_log(f"HUD mode: {HUD_MODE}")
        elif key == ord('l') or key == ord('L'):
            add_log("Opening master chat log...")
            try:
                os.startfile(CHAT_LOG_FILE)
            except Exception as e:
                add_log(f"Could not open log: {e}")
        elif key == ord(' '):
            if _PTT_AVAILABLE:
                pass  # the PTT poll thread owns SPACE on Windows
            else:
                _key_action_thread(handle_action, {"mode": "voice"})
        elif key == ord('s') or key == ord('S'):
            _key_action_thread(handle_action, {"mode": "screen"})
        elif key == ord('t') or key == ord('T'):
            _key_action_thread(_typed_directive_thread, {})
        elif key == ord('q') or key == 27:
            break

    cv2.destroyAllWindows()
