"""
ARIA Central Agent Brain & LLM Orchestrator.
Manages prompt generation, Gemini multi-key rotation, streaming SSE responses,
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
    ARIA_SOUL, MODEL_NAME, GEMINI_API_KEY, GEMINI_KEY_POOL,
    WORKSPACE_DIR, ROOT_DIR, SOUL_PATH,
    get_gemini_key, quarantine_key, add_log
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
    get_last_tool_executed, get_loaded_toolkits, set_hud_hook
)
import aria.speech as speech
from aria.agent.shortcuts import check_voice_shortcut
import aria.hud as hud

HISTORY_TURNS = 12
CONVERSATION_HISTORY: List[Dict[str, Any]] = []

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
        subprocess.Popen([sys.executable, _RUNNING_SCRIPT] + sys.argv[1:])
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


def gemini_call(system_instruction: str, contents: List[Dict[str, Any]],
                include_tools: bool = True, tool_decls: Optional[Any] = None,
                on_text_chunk: Optional[Callable[[Optional[str]], None]] = None) -> Optional[Dict[str, Any]]:
    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": contents
    }
    if include_tools:
        payload["tools"] = tool_decls if tool_decls is not None else TOOLS_DECLARATION

    body = json.dumps(payload).encode("utf-8")
    attempts = max(4, len(GEMINI_KEY_POOL) * 2)
    last_err = None

    for _a in range(attempts):
        key = get_gemini_key()
        if not key:
            add_log("No usable Gemini key available.")
            break

        endpoint = "streamGenerateContent?alt=sse&key=" if on_text_chunk else "generateContent?key="
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:{endpoint}{key}"
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})

        try:
            net_err = None
            for nr in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        if not on_text_chunk:
                            return json.loads(resp.read().decode("utf-8"))

                        # SSE Streaming reader
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
                                                on_text_chunk(p["text"])
                                            current_parts.append(p)
                                except Exception:
                                    pass

                        on_text_chunk(None)  # signal stream completion

                        merged_parts = []
                        for p in current_parts:
                            if "functionCall" in p:
                                merged_parts.append(p)
                            elif "text" in p and not p.get("thought", False):
                                if merged_parts and "text" in merged_parts[-1] and "functionCall" not in merged_parts[-1]:
                                    merged_parts[-1]["text"] += p["text"]
                                else:
                                    merged_parts.append(p)
                            else:
                                merged_parts.append(p)

                        return {"candidates": [{"content": {"parts": merged_parts}}]}
                except (urllib.error.URLError, TimeoutError, ConnectionError, socket.timeout) as ne:
                    net_err = ne
                    time.sleep(2 * (nr + 1))

            last_err = net_err
            quarantine_key(key, "network_drop")
            add_log("Gemini network drop - rotating key...")
            continue
        except urllib.error.HTTPError as e:
            if e.code == 429:
                quarantine_key(key, 429)
                add_log("Gemini rate-limit (429) - rotating key...")
                continue
            if e.code in (400, 402, 403, 404):
                quarantine_key(key, e.code)
                last_err = e
                continue
            if e.code in (500, 502, 503, 504):
                quarantine_key(key, e.code)
                last_err = e
                continue
            raise e

    if last_err:
        raise last_err
    return None


def gemini_text(system_instruction: str, contents: List[Dict[str, Any]]) -> str:
    """One-shot direct text query to Gemini without tool schemas."""
    try:
        data = gemini_call(system_instruction, contents, include_tools=False)
        if not data or not data.get("candidates"):
            return "[Gemini returned nothing.]"
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip() or "[No text in response.]"
    except Exception as e:
        return f"[Gemini error: {e}]"


def build_system_instruction(user_prompt: str) -> str:
    now_time = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    known_memories = build_prompt_memories(user_prompt or "")
    unbroken_thread = spine_unbroken_thread()

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
        "finding files (find_file), volume control (volume), Commander deck advice (mtg_advice), toggleable break reminders, and full voice chat "
        "from the phone bridge (bridge_token). Shortcuts: 'let\'s play some magic' opens Convoke lobby. Voice notes: take_note / read_notes. "
        "DJ mode: dj. Convert natural time phrases to seconds. Memory habits: save durable facts immediately with save_memory, update on corrections, "
        "forget with forget_memory, and write in your journal with journal_write. Risky tools execute immediately on user's word. "
        "open_app_or_url: resolve links from earlier in chat automatically. "
        f"{get_toolkits_prompt_block()}\n"
        "You can call multiple independent tools in one turn — do it. "
        "Keep vocal responses concise, refined, and intelligent (1-2 sentences)."
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

    # 2. Append to history & memory logs
    prompt_label = "User (Screen View): " if is_screen else "User: "
    CONVERSATION_HISTORY.append({"role": "user", "parts": [{"text": f"{prompt_label}{user_prompt}"}]})
    if len(CONVERSATION_HISTORY) > HISTORY_TURNS:
        CONVERSATION_HISTORY = CONVERSATION_HISTORY[-HISTORY_TURNS:]

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
        while True:
            stream_buf = [""]
            stream_sents = []

            def _stream_chunk_cb(chunk: Optional[str]):
                if chunk is None:
                    if stream_buf[0].strip() and not speech._SPEECH_STOP.is_set():
                        speech._SPEECH_QUEUE.put(stream_buf[0].strip())
                        stream_sents.append(stream_buf[0].strip())
                    stream_buf[0] = ""
                    return
                if speech._SPEECH_STOP.is_set():
                    return
                stream_buf[0] += chunk
                sents, stream_buf[0] = speech.extract_sentences(stream_buf[0], first_clause=(len(stream_sents) == 0))
                for s in sents:
                    if not speech._SPEECH_STOP.is_set():
                        speech._SPEECH_QUEUE.put(s)
                        stream_sents.append(s)

            speech._SPEECH_STOP.clear()

            # Call Gemini
            loaded_tk = get_loaded_toolkits()
            decls = get_toolkit_declarations(loaded_tk)

            data = gemini_call(
                system_instruction, contents,
                tool_decls=decls,
                on_text_chunk=_stream_chunk_cb if (not silent and reply_sink is None) else None
            )

            if not data or not data.get("candidates"):
                say("API connection dropped. Standing by.")
                return "API rate limit or connection drop."

            candidate = data["candidates"][0]
            model_parts = candidate.get("content", {}).get("parts", [])
            contents.append({"role": "model", "parts": model_parts})

            function_calls = [p["functionCall"] for p in model_parts if "functionCall" in p]

            if not function_calls:
                text_parts = [p["text"] for p in model_parts if "text" in p and not p.get("thought", False)]
                final_text = "".join(text_parts).strip() if text_parts else "Directive executed."
                CONVERSATION_HISTORY.append({"role": "model", "parts": [{"text": final_text}]})
                if len(CONVERSATION_HISTORY) > HISTORY_TURNS:
                    CONVERSATION_HISTORY = CONVERSATION_HISTORY[-HISTORY_TURNS:]

                hud.set_hud_subtitle(final_text)
                log_conversation("A.R.I.A.", final_text)
                add_log(f"Speech: {final_text[:28]}...")

                if not silent and not stream_sents:
                    say(final_text)
                elif silent and reply_sink is not None:
                    reply_sink.append(final_text)

                return final_text

            # Execute tool calls — green code-eyes face (v9.34 parity)
            hud.set_hud_state("coding")
            hud.draw_hud()

            response_parts = []
            if len(function_calls) > 1:
                def _run_one(fc):
                    fname = fc["name"]
                    fargs = fc.get("args", {})
                    add_log(f"Tool (parallel): {fname}")
                    res, _ = execute_tool(fname, fargs, preauthorized=preauthorized)
                    return fname, res

                with ThreadPoolExecutor(max_workers=4) as ex:
                    outs = list(ex.map(_run_one, function_calls))
                for fname, res in outs:
                    response_parts.append({"functionResponse": {"name": fname, "response": {"output": res}}})
            else:
                fc = function_calls[0]
                fname = fc["name"]
                fargs = fc.get("args", {})
                add_log(f"Tool: {fname}")
                res, _ = execute_tool(fname, fargs, preauthorized=preauthorized)
                response_parts.append({"functionResponse": {"name": fname, "response": {"output": res}}})

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
