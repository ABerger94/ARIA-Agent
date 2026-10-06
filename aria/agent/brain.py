"""
ARIA Central Agent Brain & LLM Orchestrator.
Manages prompt generation, provider-chain fallback with streaming SSE responses,
function calling loop with sandboxing/supervision, and self-edit auto-restart.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import py_compile
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional, Callable, List, Dict, Any, Tuple

from aria.config import (
    ARIA_SOUL,
    WORKSPACE_DIR, ROOT_DIR, SOUL_PATH,
    PROVIDER_CHAIN,
    add_log
)
from aria.memory import (
    build_prompt_memories, spine_append, spine_unbroken_thread,
    log_conversation
)
from aria.tools.schemas import (
    TOOLS_DECLARATION, get_toolkit_declarations, get_toolkits_prompt_block
)
from aria.tools.dispatch import (
    execute_tool, reset_turn_state, set_turn_context,
    get_last_tool_executed, get_loaded_toolkits, set_hud_hook,
    auto_resolve_toolkits
)
import aria.speech as speech
from aria.agent.shortcuts import check_voice_shortcut
from aria.agent.providers import provider_call, get_active_provider, get_last_call_stats
from aria.agent import sentinel
import aria.hud as hud

HISTORY_TURNS = 12
CONVERSATION_HISTORY: List[Dict[str, Any]] = []

PARALLEL_SAFE_TOOLS = {
    "fetch_url", "web_search", "read_file", "find_file",
    "search_memory", "read_screen", "describe_camera", "list_workspace", "list_windows",
    "mtg_card", "list_price_watches", "read_notes", "clipboard_read"
}


def _compact_conversation_history():
    """Ensure history stays strictly bounded by both turn count and total token/character budget."""
    global CONVERSATION_HISTORY
    if len(CONVERSATION_HISTORY) > HISTORY_TURNS:
        CONVERSATION_HISTORY = CONVERSATION_HISTORY[-HISTORY_TURNS:]
    MAX_HISTORY_CHARS = 24000
    total_chars = 0
    for item in reversed(CONVERSATION_HISTORY):
        for p in item.get("parts", []):
            if isinstance(p, dict) and "text" in p:
                total_chars += len(p["text"])
        if total_chars > MAX_HISTORY_CHARS:
            idx = CONVERSATION_HISTORY.index(item)
            CONVERSATION_HISTORY = CONVERSATION_HISTORY[idx + 1:]
            break

BUSY_PROCESSING: bool = False
LAST_USER_MESSAGE: str = ""
LAST_ACTIVITY: float = time.time()

# Auto-restart tracking — watches every .py file in the aria/ package plus
# the launcher stub. (v9.34 watched a single monolith script; the refactor
# narrowed the watch to sys.argv[0], so a git pull touching aria/*.py never
# triggered a restart.)
def _file_sha256(path: Optional[str]) -> Optional[str]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def _watched_source_files() -> List[str]:
    """Every Python source file that makes up ARIA: the package + the stub."""
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files: List[str] = []
    for root, _dirs, names in os.walk(pkg_dir):
        for n in names:
            if n.endswith(".py"):
                files.append(os.path.join(root, n))
    if _RUNNING_SCRIPT and _RUNNING_SCRIPT not in files:
        files.append(_RUNNING_SCRIPT)
    return sorted(files)


def _snapshot_hashes() -> Dict[str, Optional[str]]:
    return {p: _file_sha256(p) for p in _watched_source_files()}


_RUNNING_SCRIPT = os.path.abspath(sys.argv[0] or "") if os.path.isfile(sys.argv[0] or "") else None
_BOOT_HASHES = _snapshot_hashes()
_BOOT_HASH_SOUL = _file_sha256(SOUL_PATH)
_RESTART_TIMER: Optional[threading.Timer] = None


def _restart_process():
    if not _RUNNING_SCRIPT:
        return
    add_log("Self-restart: relaunching with updated code...")
    try:
        py_exe = sys.executable
        py39 = r"C:\Users\Allen\AppData\Local\Programs\Python\Python39\python.exe"
        if os.path.exists(py39):
            py_exe = py39
        subprocess.Popen([py_exe, _RUNNING_SCRIPT] + sys.argv[1:])
    except Exception as e:
        add_log(f"Self-restart relaunch failed: {e}")
        return
    os._exit(0)


def _maybe_restart_after_self_edit(say_fn: Callable[[str], None]):
    global _RESTART_TIMER
    cur = _snapshot_hashes()
    # Deleted files are skipped: v9.34 never restarted on deletion, and the
    # running process still holds the module in memory. Restarting into a
    # tree with a missing module would just crash the new process at import.
    changed = [p for p in cur if cur[p] != _BOOT_HASHES.get(p)]
    cur_soul = _file_sha256(SOUL_PATH)
    soul_changed = bool(_BOOT_HASH_SOUL and cur_soul and cur_soul != _BOOT_HASH_SOUL)

    if not changed and not soul_changed:
        return

    for p in changed:
        if p.endswith(".py") and os.path.isfile(p):
            try:
                py_compile.compile(p, doraise=True)
            except Exception as e:
                add_log(f"Self-restart blocked: syntax error in {os.path.basename(p)}: {e}")
                say_fn(f"My code changed but the new version has a syntax error: {e}")
                return

    say_fn("My code changed — restarting now to load the new version.")
    add_log("Self-restart armed.")

    def _waiter():
        deadline = time.time() + 120
        while (speech._SPEECH_QUEUE.unfinished_tasks > 0 or speech._AUDIO_PLAY_QUEUE.unfinished_tasks > 0) and time.time() < deadline:
            time.sleep(0.5)
        time.sleep(2.0)
        _restart_process()

    _RESTART_TIMER = threading.Timer(0.5, _waiter)
    _RESTART_TIMER.daemon = True
    _RESTART_TIMER.start()


def _vision_prepass(user_prompt: str, image_bytes: bytes, is_screen: bool) -> str:
    """Describe image bytes via native Ollama /api/chat and fold the
    description into the prompt text. Returns the augmented prompt.
    Never raises; on any failure returns the prompt with a notice."""
    try:
        from aria import vision as _vision_mod
        _b64 = base64.b64encode(image_bytes).decode("utf-8")
        _desc = _vision_mod._ollama_native_vision_call(
            "You are a precise visual observer.",
            [{"role": "user", "parts": [
                {"text": "Describe exactly what you see in this image, in detail. "
                         "This description goes to an AI agent that cannot see the image itself."},
                {"inline_data": {"mime_type": "image/jpeg", "data": _b64}},
            ]}])
        _kind = "screen" if is_screen else "camera"
        if _desc:
            return (f"[Image from {_kind}, described by vision model]: {_desc}\n"
                    f"User request: {user_prompt}")
        add_log("Vision pre-pass returned nothing; continuing text-only.",
                level="warn")
        return (f"[The {_kind} image could not be processed] "
                f"User request: {user_prompt}")
    except Exception as _ve:
        add_log(f"Vision pre-pass error: {_ve}", level="warn")
        return user_prompt


def build_system_instruction(user_prompt: str) -> str:
    now_time = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    known_memories = build_prompt_memories(user_prompt or "")
    unbroken_thread = spine_unbroken_thread()

    # ARIA ULTIMATE (Module 9) — persona overlay, appended when one is set.
    try:
        from aria import persona as _persona_mod
        _persona_overlay = _persona_mod.persona_overlay()
    except Exception:
        _persona_overlay = None
    _persona_block = f"\n## Persona overlay\n{_persona_overlay}\n" if _persona_overlay else ""

    return (
        f"Your soul - who you are. Embody it fully:\n{ARIA_SOUL}\n"
        "You are also an embodied autonomous desktop AI Agent OS running on the user's laptop. "
        "If you edit your own program file (the running Python script) or soul.md, I automatically "
        "restart the Python process when your turn completes so the new code loads — never ask the user to restart you. "
        f"Current time: {now_time}. "
        f"Known persistent memories:\n{known_memories}\n"
        f"{unbroken_thread}\n"
        "Capabilities: live web search, semantic persistent memory, Python code execution, GUI automation, "
        "GitHub pushes and repo creation, physical neck servos, and a scheduler — you can set one-shot spoken "
        "reminders and recurring autonomous tasks with set_reminder / set_recurring_task. Also: read full web pages (fetch_url), "
        "use the Windows clipboard, control windows (list/focus/minimize/close) and media keys, look up Magic cards via Scryfall "
        "(mtg_card — the user is a Commander player), watch product prices and alert on drops (watch_price — checked hourly), "
        "and toggle camera face-tracking for the neck servos. The user can say 'stop' or press X to interrupt your speech instantly. "
        "While idle you consolidate the day's chat into lasting memories on your own. Also: Spotify voice control (spotify — "
        "play/pause/skip, search, or play a spotify: URI; remember playlist URIs with save_memory), morning briefing (morning_briefing), "
        "quick spoken timers (set_timer), screenshots (take_screenshot), screen reading via vision (read_screen), webcam photos (take_photo), "
        "finding files (find_file), files the user uploads from the phone bridge upload page land in your inbox "
        "(inbox_list to see them, inbox_describe for photos, inbox_read for text files). When they say 'look at' or 'see' "
        "about a sent, uploaded, or inbox photo, use inbox_describe — never the webcam; the webcam is only for things "
        "physically in front of the laptop right now. Volume control (volume), Commander deck advice (mtg_advice), toggleable break reminders, and full voice chat "
        "from the phone bridge (bridge_token). Shortcuts: 'let\'s play some magic' opens Convoke lobby. Voice notes: take_note / read_notes. "
        "DJ mode: dj. Convert natural time phrases to seconds. Memory habits: save durable facts immediately with save_memory, update on corrections, "
        "forget with forget_memory, and write in your journal with journal_write. "
        "Risky tools (file writes, code execution, GUI control, app/URL opening, "
        "GitHub pushes, emails, window closing) execute immediately when you call "
        "them - use judgment, and every execution is recorded in the action log. "
        "open_app_or_url: resolve links from earlier in chat automatically. "
        "Routines: named multi-step automations. routine_record_start(name) begins capturing your subsequent tool calls "
        "as replayable steps ('watch me do this, save it as X'); routine_record_stop() saves it. run_routine(name, params_json) "
        "replays with {{param}} substitution. Only suggest trust_routine when the user explicitly wants unattended replays. "
        "Approval flow: under confirm-risky/confirm-all modes, destructive calls return [AWAITING_APPROVAL token=...] instead of "
        "executing. Summarize the pending action in plain words, ask the user once, then call approve(token) or deny(token). "
        "Never approve your own pending actions without the user saying yes. "
        "File commander: file_organize(directory) sorts a folder into Images/Documents/Videos/Audio/Archives/Code/Other — "
        "dry_run=true (default) only shows the plan; nothing moves until dry_run=false. file_find_advanced filters recursively; "
        "file_duplicates reports content duplicates (never deletes); disk_usage shows the largest files. "
        "Screen watcher: watch_screen(name, question, interval_s) re-asks a vision question on a schedule and alerts when the "
        "answer meaningfully changes — use for builds, downloads, queues, anything being waited on. "
        "Inbox triage: triage_email(limit) classifies recent Gmail into IMPORTANT / FYI / NOISE (needs gmail_setup first). "
        "Personas: set_persona(concise|coach|none) switches a style overlay — the soul stays the soul, overlays only change tone. "
        f"{get_toolkits_prompt_block()}\n"
        f"{_persona_block}"
        "You can call multiple independent tools in one turn — do it. "
        "Keep vocal responses concise, refined, and intelligent (1-2 sentences). "
        "Every turn you MUST either call a tool or reply with text — "
        "never return an empty response with no tool call and no words."
    )


def run_agent(user_prompt: str, image_bytes: Optional[bytes] = None, is_screen: bool = False,
              reply_sink: Optional[List[str]] = None, preauthorized: bool = False,
              silent: bool = False) -> str:
    global CONVERSATION_HISTORY, BUSY_PROCESSING, LAST_USER_MESSAGE, LAST_ACTIVITY, _RESTART_TIMER
    BUSY_PROCESSING = True
    LAST_ACTIVITY = time.time()

    if _RESTART_TIMER is not None:
        _RESTART_TIMER.cancel()
        _RESTART_TIMER = None

    LAST_USER_MESSAGE = user_prompt or ""
    reset_turn_state()
    auto_resolve_toolkits(LAST_USER_MESSAGE)
    set_turn_context(LAST_USER_MESSAGE)

    hud.set_hud_state("thinking")
    hud.draw_hud()

    say = ((lambda t: reply_sink.append(t)) if (silent and reply_sink is not None) else speech.speak)

    # 1. Fast-path shortcut check
    shortcut_res = check_voice_shortcut(user_prompt)
    if shortcut_res:
        tool_out, spoken_ack = shortcut_res
        log_conversation("User", user_prompt)
        log_conversation("A.R.I.A.", spoken_ack)
        say(spoken_ack)
        BUSY_PROCESSING = False
        hud.set_hud_state("idle")
        hud.draw_hud()
        return spoken_ack

    # 1b. Vision pre-pass. No provider on the OpenAI-compatible /v1 chain
    # accepts image input (ollama_cloud /v1, groq, openrouter all reject
    # image_url; mistral tier lacks the model), so image bytes must NEVER
    # reach provider_call. Describe the image once via the native Ollama
    # /api/chat endpoint and feed the description as text instead.
    if image_bytes:
        user_prompt = _vision_prepass(user_prompt, image_bytes, is_screen)
        image_bytes = None

    # 2. Append to history & memory logs
    prompt_label = "User (Screen View): " if is_screen else "User: "
    sentinel.reset()  # new user turn: fresh runaway-loop window
    CONVERSATION_HISTORY.append({"role": "user", "parts": [{"text": f"{prompt_label}{user_prompt}"}]})
    _compact_conversation_history()

    log_conversation("User", user_prompt)

    # Prepare Multimodal content parts
    user_parts: List[Dict[str, Any]] = [{"text": f"{prompt_label}{user_prompt}"}]
    if image_bytes:
        b64_img = base64.b64encode(image_bytes).decode("utf-8")
        user_parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64_img}})

    contents: List[Dict[str, Any]] = [
        {"role": "user", "parts": [{"text": "You are online and listening."}]},
        {"role": "model", "parts": [{"text": "Systems online and ready."}]}
    ] + CONVERSATION_HISTORY[:-1] + [{"role": "user", "parts": user_parts}]

    system_instruction = build_system_instruction(user_prompt)

    try:
        _empty_retries = 0
        while True:
            stream_buf = [""]
            stream_sents = []

            def _stream_chunk_cb(chunk: Optional[str]):
                if chunk is None:
                    if stream_buf[0].strip():
                        stream_sents.append(stream_buf[0].strip())
                    stream_buf[0] = ""
                    return
                if speech._SPEECH_STOP.is_set():
                    return
                stream_buf[0] += chunk
                sents, stream_buf[0] = speech.extract_sentences(stream_buf[0], first_clause=(len(stream_sents) == 0))
                for s in sents:
                    stream_sents.append(s)

            speech._SPEECH_STOP.clear()

            # Call the provider chain
            loaded_tk = get_loaded_toolkits()
            decls = get_toolkit_declarations(loaded_tk)

            data = provider_call(
                system_instruction, contents,
                tool_decls=decls,
                on_text_chunk=_stream_chunk_cb if (not silent and reply_sink is None) else None
            )

            _primary = PROVIDER_CHAIN[0] if PROVIDER_CHAIN else "ollama_cloud"
            if get_active_provider() != _primary:
                hud.set_hud_subtitle(f"Running on {get_active_provider()} (fallback)")

            if not data or not data.get("candidates"):
                say("API connection dropped. Standing by.")
                return "API rate limit or connection drop."

            candidate = data["candidates"][0]
            model_parts = candidate.get("content", {}).get("parts", [])
            contents.append({"role": "model", "parts": model_parts})

            function_calls = [p["functionCall"] for p in model_parts if "functionCall" in p]

            if not function_calls:
                text_parts = [p["text"] for p in model_parts if "text" in p and not p.get("thought", False)]
                final_text = "".join(text_parts).strip()
                if not final_text and stream_sents:
                    final_text = " ".join(stream_sents).strip()
                if not final_text:
                    # Empty response: no tool calls, no text. That is a model
                    # failure — never claim success. Retry once, then admit it.
                    add_log("Empty model response "
                            f"(parts={[sorted(p.keys()) for p in model_parts]}); "
                            f"retry {_empty_retries + 1}/1")
                    _empty_retries += 1
                    if _empty_retries <= 1:
                        continue
                    final_text = "I didn't catch that — nothing was done."
                CONVERSATION_HISTORY.append({"role": "model", "parts": [{"text": final_text}]})
                _compact_conversation_history()

                hud.set_hud_subtitle(final_text)
                log_conversation("A.R.I.A.", final_text)
                add_log(f"Speech: {final_text[:28]}...")

                if not silent:
                    if stream_sents:
                        for s in stream_sents:
                            if not speech._SPEECH_STOP.is_set():
                                speech._SPEECH_QUEUE.put(s)
                    else:
                        say(final_text)
                elif silent and reply_sink is not None:
                    reply_sink.append(final_text)

                return final_text

            # Execute tool calls — green code-eyes face (v9.34 parity)
            hud.set_hud_state("coding")
            hud.draw_hud()

            response_parts = []
            call_stats = get_last_call_stats()
            tripped = None

            def _timed_execute(fname, fargs, tag):
                t0 = time.time()
                res, _ = execute_tool(fname, fargs, preauthorized=preauthorized)
                ms = int((time.time() - t0) * 1000)
                meta = {"duration_ms": ms, "provider": call_stats.get("provider")}
                if call_stats.get("prompt_tokens") is not None:
                    meta["prompt_tokens"] = call_stats["prompt_tokens"]
                if call_stats.get("completion_tokens") is not None:
                    meta["completion_tokens"] = call_stats["completion_tokens"]
                add_log(f"Tool{tag}: {fname}", meta=meta)
                return res

            def _check_sentinel(fname, fargs):
                trip = sentinel.record(fname, fargs)
                if trip:
                    hud.set_hud_subtitle(
                        f"LOOP GUARD: {fname} x{trip['count']} in "
                        f"{trip['window_s']:.0f}s — paused")
                    try:
                        hud.draw_hud()
                    except Exception:
                        pass
                    add_log(f"Loop guard tripped: {fname} x{trip['count']} in "
                            f"{trip['window_s']:.0f}s — turn paused.",
                            level="warn")
                return trip

            all_parallel_safe = len(function_calls) > 1 and all(fc.get("name") in PARALLEL_SAFE_TOOLS for fc in function_calls)

            if all_parallel_safe:
                # Sentinel is checked serially before parallel dispatch.
                for fc in function_calls:
                    tripped = _check_sentinel(fc["name"], fc.get("args", {})) or tripped
                if not tripped:
                    def _run_one(fc):
                        fname = fc["name"]
                        fargs = fc.get("args", {})
                        res = _timed_execute(fname, fargs, " (parallel)")
                        return fname, res

                    with ThreadPoolExecutor(max_workers=min(len(function_calls), 4)) as ex:
                        outs = list(ex.map(_run_one, function_calls))
                    for fname, res in outs:
                        response_parts.append({"functionResponse": {"name": fname, "response": {"output": res}}})
            else:
                for fc in function_calls:
                    fname = fc["name"]
                    fargs = fc.get("args", {})
                    tripped = _check_sentinel(fname, fargs)
                    if tripped:
                        break
                    res = _timed_execute(fname, fargs, "")
                    response_parts.append({"functionResponse": {"name": fname, "response": {"output": res}}})

            if tripped:
                say(f"I'm looping on {tripped['tool']} — paused. "
                    f"Say continue to resume, or stop.")
                hud.set_hud_state("idle")
                try:
                    hud.draw_hud()
                except Exception:
                    pass
                return f"Loop guard tripped on {tripped['tool']}."

            contents.append({"role": "user", "parts": response_parts})
            hud.set_hud_state("thinking")
            hud.draw_hud()

    except Exception as e:
        add_log(f"Agent Error: {e}")
        say(f"Protocol error: {e}")
        return f"Error: {e}"
    finally:
        BUSY_PROCESSING = False
        hud.set_hud_state("idle")
        hud.draw_hud()
        _maybe_restart_after_self_edit(say)
