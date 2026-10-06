"""
A.R.I.A. Autonomous Agent Operating System — Main Orchestrator.
Integrates all subsystems: Config, Memory Spine, Speech Pipeline, Hardware Servos,
Vision Optics, Background Scheduler, Spotify Media, Phone Bridge, and Agent Brain.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import threading
import time
from datetime import datetime
from typing import Optional, List, Tuple, Callable

import cv2
import numpy as np
import speech_recognition as sr

# Core package imports
from aria.config import (
    ROOT_DIR, WORKSPACE_DIR, SOUL_PATH, MODEL_NAME,
    PHONE_BRIDGE_PORT, BRIDGE_TOKEN, GITHUB_USERNAME, GITHUB_TOKEN,
    ARIA_SOUL, get_setting, set_setting, add_log, register_log_listener
)
import aria.memory as memory
import aria.speech as speech
import aria.hardware as hardware
import aria.vision as vision
import aria.scheduler as scheduler
import aria.spotify as spotify
import aria.hud as hud
import aria.ops as _ops
import aria.ops_screen as _ops_screen
import aria.pixel_avatar as pixel_avatar
import aria.bridge as bridge
import aria.agent as agent
from aria.tools.dispatch import (
    execute_tool, set_log_hook, set_hud_hook, set_history_hook, set_spine_hook,
)

# Global runtime flags
RUNNING: bool = True


def request_shutdown():
    """OPS Controls tab: stop the main loop cleanly."""
    global RUNNING
    RUNNING = False
    add_log("Shutdown requested.")
EXCITED_UNTIL: float = 0.0   # wake-up burst expiry timestamp
EXCITED_REVERT: str = "idle"  # state to return to after the burst
WHISPER_MODE: bool = bool(get_setting("whisper_mode", False))
VOICE_LISTENER_ONLINE: bool = False

_PTT_AVAILABLE: bool = False
_PTT_RECORDING: bool = False
_PTT_STOP: bool = False
_PTT_STARTED_AT: float = 0.0
_PTT_MIN_SECONDS: float = 0.5
_PTT_MAX_SECONDS: float = 60.0

recognizer = sr.Recognizer()


def _init_wiring():
    """Wire cross-subsystem events, hooks, and logging pipes."""
    # 1. Logging
    register_log_listener(hud.add_hud_log)
    set_log_hook(add_log)
    set_hud_hook(lambda st: hud.set_hud_state(st))
    speech.set_speech_state_hook(lambda st: hud.set_hud_state(st))
    set_spine_hook(memory.spine_append)
    set_history_hook(lambda entry: agent.CONVERSATION_HISTORY.append(entry))

    # 2. Chat history listener
    memory.register_chat_listener(hud.add_display_chat)

    # 3. Vision callers — use the native Ollama vision path
    # (_default_vision_call: native /api/chat images array first, then the
    # provider chain). agent.gemini_text was the legacy hook but Gemini keys
    # are 402-dead, so wiring it here silently bypassed the working path.
    vision.set_vision_text_caller(vision._default_vision_call)

    # 4. Bridge processor & chat log
    bridge.set_bridge_processor(lambda text, silent: handle_action("voice", typed_prompt=text, silent=silent))
    bridge.set_chat_log_provider(memory.get_display_chat_log)

    # 5. HUD mood & subsystems
    hud.set_mood_callback(lambda: agent.mood_word())
    hud.set_subsystems_callback(get_subsystem_statuses)




def get_subsystem_statuses() -> List[Tuple[str, str, bool]]:
    """Live (name, status, is_ok) for the HUD subsystem display."""
    v_stat = vision.get_vision_status()
    h_stat = hardware.get_hardware_status()

    # 0. Voice Pathway
    voice_entry = ("Voice Pathway", "ARMED" if VOICE_LISTENER_ONLINE else "OFFLINE", VOICE_LISTENER_ONLINE)

    # 1. Vision
    v_ok = v_stat["vision_last"][0]
    v_str = "READY" if v_ok else ("ERROR" if v_ok is False else "IDLE")
    v_str = f"{v_str} [{vision.body_camera_label()}]"
    vision_entry = ("Vision Optics", v_str, bool(v_ok))

    # 2. Screen
    s_ok = v_stat["screen_last"][0]
    s_str = "READY" if s_ok else ("ERROR" if s_ok is False else "IDLE")
    screen_entry = ("Screen Perception", s_str, bool(s_ok))

    # 3. Memory
    mem_ok = memory.memory_db_probe()
    mem_entry = ("Semantic Memory", "READY" if mem_ok else "ERROR", mem_ok)

    # 4. Hardware Servos
    hw_conn = h_stat["connected"]
    hw_str = f"P{h_stat['pan']} T{h_stat['tilt']}" if hw_conn else "VIRTUAL"
    hw_entry = ("Head Servos", hw_str, hw_conn)

    # 5. Scheduler
    sched_tasks = len(scheduler.sched_list())
    sched_entry = ("Scheduler", f"ARMED ({sched_tasks})" if sched_tasks else "IDLE", sched_tasks > 0)

    # 6. GitHub
    gh_ok = bool(GITHUB_USERNAME and GITHUB_TOKEN and GITHUB_TOKEN != "INSERT")
    gh_entry = ("GitHub Tools", "ARMED" if gh_ok else "OFFLINE", gh_ok)

    # 7. Autonomous Daemon & Workers
    try:
        goals_count = len(agent.goal_list(status_filter="pending"))
        workers_entry = ("Autonomy & Workers", f"ARMED ({goals_count})" if goals_count else "ACTIVE", True)
    except Exception:
        workers_entry = ("Autonomy & Workers", "ACTIVE", True)

    return [voice_entry, vision_entry, screen_entry, mem_entry, hw_entry, sched_entry, gh_entry, workers_entry]


def set_whisper_mode(on: bool) -> bool:
    global WHISPER_MODE
    WHISPER_MODE = bool(on)
    hud.WHISPER_MODE = WHISPER_MODE
    set_setting("whisper_mode", WHISPER_MODE)
    add_log(f"Whisper mode {'enabled' if WHISPER_MODE else 'disabled'}.")
    return WHISPER_MODE


def toggle_whisper_mode() -> bool:
    return set_whisper_mode(not WHISPER_MODE)


def _ops_read_selected_aloud() -> None:
    """Speak the currently selected OPS inbox item (runs in a thread)."""
    try:
        dash = _ops.get_dashboard()
        items = ((dash.get("inbox") or {}).get("data") or {}).get("items", [])
        idx = _ops.SELECTED_MAIL
        if idx >= len(items):
            speech.speak("No email selected.")
            return
        it = items[idx]
        add_log(f"Reading mail {idx + 1} aloud.")
        body = _ops.read_mail_body(it["uid"])
        speech.speak(f"From {it['sender']}. Subject: {it['subject']}. {body}")
    except Exception as e:
        add_log(f"Read-aloud failed: {e}")


def handle_action(mode: str = "voice", typed_prompt: Optional[str] = None, silent: bool = False) -> str:
    """Central action pipeline invoked from voice, PTT, HUD typed commands, or Phone Bridge."""
    agent.proactive.mood_note_interaction()

    say = (lambda t: None) if silent else speech.speak

    user_text = typed_prompt if typed_prompt else ""
    if not user_text:
        return ""

    low = user_text.lower().strip()

    # 1. Stop words
    if any(low == w or low.startswith(w + " ") for w in speech.STOP_WORDS):
        speech.interrupt_speech()
        return "Interrupted."

    # 2. Whisper mode voice commands
    if "whisper mode" in low or "whisper on" in low or "whisper off" in low:
        on = "off" not in low and ("on" in low or not WHISPER_MODE)
        set_whisper_mode(on)
        ack = f"Whisper mode {'on' if on else 'off'}."
        say(ack)
        return ack


    # 5. Multimodal context attachment
    image_bytes = None
    is_screen = False
    if mode == "screen" or any(k in low for k in ["screen", "display", "desktop", "my window"]):
        image_bytes = vision.capture_screen_if_changed()
        is_screen = True
        if image_bytes is None:
            user_text = "[Screen unchanged since my last view] " + user_text
    elif mode == "camera" or any(k in low for k in ["look", "see", "holding", "camera"]):
        image_bytes = vision.capture_webcam()

    # 6. Run Agent Brain
    return agent.run_agent(user_text, image_bytes=image_bytes, is_screen=is_screen, silent=silent)


def _ptt_rising_edge():
    global _PTT_RECORDING, _PTT_STOP, _PTT_STARTED_AT
    if _PTT_RECORDING or agent.BUSY_PROCESSING or hud.TYPING_ACTIVE:
        return
    _PTT_RECORDING = True
    _PTT_STOP = False
    _PTT_STARTED_AT = time.time()
    agent.LAST_ACTIVITY = time.time()
    hud.set_hud_state("listening")
    hud.draw_hud()
    add_log("Push-to-talk started (listening)...")
    threading.Thread(target=_ptt_capture, daemon=True).start()


def _ptt_falling_edge():
    global _PTT_STOP
    if _PTT_RECORDING and not _PTT_STOP:
        _PTT_STOP = True
        add_log("Push-to-talk released.")


def _ptt_reset_state():
    global _PTT_RECORDING, _PTT_STOP
    _PTT_RECORDING = False
    _PTT_STOP = False
    hud.set_hud_state("idle")
    hud.draw_hud()


def _ptt_capture():
    frames = []
    sample_rate = 16000
    sample_width = 2

    try:
        with sr.Microphone(sample_rate=16000) as source:
            sample_rate = source.SAMPLE_RATE
            sample_width = source.SAMPLE_WIDTH
            chunk_size = source.CHUNK
            while not _PTT_STOP:
                if time.time() - _PTT_STARTED_AT >= _PTT_MAX_SECONDS:
                    add_log("PTT: 60s cap reached.")
                    break
                raw_chunk = source.stream.read(chunk_size)
                if not raw_chunk:
                    continue
                frames.append(raw_chunk)
    except Exception as e:
        add_log(f"Mic issue: {e}")
        _ptt_reset_state()
        return

    raw_audio = b"".join(frames)
    total_s = len(raw_audio) / float(sample_rate * sample_width) if (sample_rate and sample_width) else 0.0
    if total_s < _PTT_MIN_SECONDS:
        add_log("Tap ignored — hold SPACE while you talk.")
        _ptt_reset_state()
        return

    audio_data = sr.AudioData(raw_audio, sample_rate, sample_width)
    try:
        user_text = speech.transcribe_local_or_cloud(audio_data)
        add_log(f"Acoustic (PTT): '{user_text[:25]}...'")
    except Exception as e:
        add_log(f"Mic transcribe error: {e}")
        _ptt_reset_state()
        return

    memory.spine_append("ptt", {"seconds": round(total_s, 1)})
    _ptt_reset_state()
    handle_action("voice", typed_prompt=user_text)


def _ptt_poll_loop():
    """Windows-only: poll physical spacebar via GetAsyncKeyState."""
    try:
        u32 = ctypes.windll.user32
    except Exception:
        return
    was_down = False
    while RUNNING:
        try:
            down = (u32.GetAsyncKeyState(0x20) & 0x8000) != 0
        except Exception:
            down = False
        if down and not was_down:
            _ptt_rising_edge()
        elif not down and was_down:
            _ptt_falling_edge()
        was_down = down
        time.sleep(0.05)


def _on_hud_mouse(event, x, y, flags, param):
    """Handle mouse clicks and wheel on HUD interactive buttons."""
    # Mouse wheel support for paging commands, scrolling chat, or adjusting volume
    if event == getattr(cv2, "EVENT_MOUSEWHEEL", 10):
        if hud.SHOW_COMMANDS:
            if flags > 0:
                hud.commands_prev_page()
            else:
                hud.commands_next_page()
            return
        elif hud.HUD_MODE == "ops":
            _ops_screen.handle_wheel(flags > 0)
            return
        elif hud.HUD_MODE == "chat_log":
            if flags > 0:
                hud.CHAT_SCROLL = hud.CHAT_SCROLL + 1
            else:
                hud.CHAT_SCROLL = max(0, hud.CHAT_SCROLL - 1)
            return
        elif hud.handle_wheel(x, y, flags > 0):
            return

    # Mouse drag over interactive sliders (e.g. volume)
    if event == cv2.EVENT_MOUSEMOVE and (flags & cv2.EVENT_FLAG_LBUTTON):
        if hud.handle_drag(x, y):
            return

    if event == cv2.EVENT_LBUTTONUP:
        hud.handle_release(x, y)

    if event == cv2.EVENT_RBUTTONDOWN:
        # Right click anywhere on directive bar pastes from clipboard
        ix, iy, iw, ih = hud._INPUT_BAR
        if ix <= x <= ix + iw and iy <= y <= iy + ih:
            pasted = hud.get_clipboard_text()
            if pasted:
                hud.TYPING_ACTIVE = True
                hud.TYPING_BUFFER += pasted
                add_log(f"Pasted from clipboard ({len(pasted)} chars).")
            else:
                add_log("Clipboard empty or non-text.")
            return

    if event == cv2.EVENT_LBUTTONDOWN:
        # OPS overlay: tabs, task rows, HUB events, control buttons
        if hud.HUD_MODE == "ops":
            if _ops_screen.handle_click(x, y):
                return
        # If commands overlay is visible, intercept clicks on overlay buttons
        if hud.SHOW_COMMANDS:
            # Prev page button
            px, py, pw, ph = hud._COMMANDS_PREV_BTN
            if px <= x <= px + pw and py <= y <= py + ph:
                hud.commands_prev_page()
                return

            # Next page button
            nx, ny, nw, nh = hud._COMMANDS_NEXT_BTN
            if nx <= x <= nx + nw and ny <= y <= ny + nh:
                hud.commands_next_page()
                return

            # Close button
            cx, cy, cw, ch = hud._COMMANDS_CLOSE_BTN
            if cx <= x <= cx + cw and cy <= y <= cy + ch:
                hud.SHOW_COMMANDS = False
                return

            # Top right X button
            xx, xy, xw, xh = hud._COMMANDS_X_BTN
            if xx <= x <= xx + xw and xy <= y <= xy + xh:
                hud.SHOW_COMMANDS = False
                return

            # Clicking anywhere outside overlay bounds closes it
            if not (36 <= x <= 1244 and 52 <= y <= 700):
                hud.SHOW_COMMANDS = False
                return

            return

        # Check if HUD interactive context tiles or tabs handled the click
        if hud.handle_click(x, y):
            return

        # Whisper button
        bx, by, bw, bh = hud._WHISPER_BTN
        if bx <= x <= bx + bw and by <= y <= by + bh:
            on = toggle_whisper_mode()
            speech.speak(f"Whisper mode {'on' if on else 'off'}.")
            return

        # Directive input bar buttons:
        # 1. PASTE button
        px, py, pw, ph = hud._INPUT_PASTE_BTN
        if px <= x <= px + pw and py <= y <= py + ph:
            pasted = hud.get_clipboard_text()
            hud.TYPING_ACTIVE = True
            if pasted:
                hud.TYPING_BUFFER += pasted
                add_log(f"Pasted from clipboard ({len(pasted)} chars).")
            else:
                add_log("Clipboard empty or non-text.")
            return

        # 2. SEND button
        sx, sy, sw, sh = hud._INPUT_SEND_BTN
        if sx <= x <= sx + sw and sy <= y <= sy + sh:
            prompt = hud.TYPING_BUFFER.strip()
            if prompt:
                hud.TYPING_ACTIVE = False
                hud.TYPING_BUFFER = ""
                add_log(f"Typed directive: '{prompt[:35]}...'")
                threading.Thread(target=handle_action, args=("voice", prompt), daemon=True).start()
            else:
                add_log("Directive is empty — paste or type text first.")
            return

        # 3. CLEAR button
        cx, cy, cw, ch = hud._INPUT_CLEAR_BTN
        if cx <= x <= cx + cw and cy <= y <= cy + ch:
            hud.TYPING_BUFFER = ""
            add_log("Directive buffer cleared.")
            return

        # 4. ESC / Toggle Typing button
        ex, ey, ew, eh = hud._INPUT_ESC_BTN
        if ex <= x <= ex + ew and ey <= y <= ey + eh:
            if hud.TYPING_ACTIVE:
                hud.TYPING_ACTIVE = False
                hud.TYPING_BUFFER = ""
                add_log("Typing cancelled.")
            else:
                hud.TYPING_ACTIVE = True
                add_log("Typing mode engaged.")
            return

        # Directive input bar body click
        ix, iy, iw, ih = hud._INPUT_BAR
        if ix <= x <= ix + iw and iy <= y <= iy + ih:
            hud.TYPING_ACTIVE = True
            add_log("Typing mode engaged.")
            return


def _console_input_loop():
    """Background listener for typing directives directly in the terminal console."""
    while RUNNING:
        try:
            line = sys.stdin.readline()
            if not line:
                time.sleep(0.2)
                continue
            text = line.strip()
            if text:
                add_log(f"Terminal directive: '{text[:35]}...'")
                threading.Thread(target=handle_action, args=("voice", text), daemon=True).start()
        except Exception:
            time.sleep(0.5)


def _extract_wake_command(text: str) -> Tuple[bool, str]:
    """Detect if wake word is present and extract following command.
    Matches variations: Aria, Hey Aria, Hi Aria, Hello Aria, Ok Aria, Arya, etc.
    Avoids false triggering on isolated common words like 'area'.
    """
    if not text:
        return False, ""
    low = text.lower().strip()
    pattern = r'\b(?:(?:hey|hi|hello|ok|okay|yo)\s+)?(?:aria|arya|ahria|auria)\b|\b(?:hey|hi|hello|ok|okay|yo)\s+area\b'
    match = re.search(pattern, low)
    if not match:
        return False, ""
    
    # Wake word must occur near the start of the utterance (within first 3 words)
    prefix = low[:match.start()].strip()
    if prefix and len(prefix.split()) > 3:
        return False, ""

    # Repeatedly strip wake words from the beginning
    remainder = low
    while True:
        m = re.search(pattern, remainder)
        if m and m.start() == 0:
            remainder = remainder[m.end():].strip(" ,.-!?:;—–\t\n")
        else:
            break

    # If nothing left besides wake words/punctuation, it's a conversational wake
    sub_cleaned = re.sub(pattern, "", low).strip(" ,.-!?:;—–\t\n")
    if not sub_cleaned:
        return True, ""

    return True, remainder


def continuous_voice_listener():
    """Always-on wake-word mic loop.
    
    Monitors microphone for 'Aria' / 'Hey Aria' and phonetic variants.
    Handles:
      1. Combined wake + directive: e.g. "Aria, open Spotify" -> runs directive immediately.
      2. Conversational wake: e.g. "Hey Aria" -> responds "I'm listening", enters listening state,
         and captures the user's follow-up directive without requiring wake-word repetition.
      3. Barge-in / interruption: cutting off ARIA when user speaks stop words.
      4. Self-hearing suppression: skips processing while ARIA herself is speaking.
      5. Auto-reconnection: cleanly re-arms if microphone hardware resets or drops.
    """
    global EXCITED_UNTIL, EXCITED_REVERT, VOICE_LISTENER_ONLINE
    
    wake_rec = sr.Recognizer()
    wake_rec.pause_threshold = 0.8
    wake_rec.phrase_threshold = 0.3
    wake_rec.non_speaking_duration = 0.5
    wake_rec.dynamic_energy_threshold = True
    wake_rec.energy_threshold = 300
    wake_rec.dynamic_energy_adjustment_damping = 0.15
    wake_rec.dynamic_energy_ratio = 1.5

    while RUNNING:
        if not speech.VOICE_ENABLED:
            VOICE_LISTENER_ONLINE = False
            time.sleep(1.0)
            continue
        try:
            with sr.Microphone() as source:
                VOICE_LISTENER_ONLINE = True
                try:
                    wake_rec.adjust_for_ambient_noise(source, duration=0.8)
                    if wake_rec.energy_threshold < 250:
                        wake_rec.energy_threshold = 250
                except Exception:
                    pass
                add_log("Wake-word listener armed ('Aria' / 'Hey Aria').")
                
                while RUNNING:
                    # While PTT recording, ARIA busy, or ARIA speaking/thinking, yield
                    if _PTT_RECORDING or agent.BUSY_PROCESSING or hud.CURRENT_STATE in ("thinking", "working", "coding"):
                        time.sleep(0.2)
                        continue

                    # Listen for acoustic phrase
                    try:
                        audio = wake_rec.listen(source, timeout=2.5, phrase_time_limit=8.0)
                    except sr.WaitTimeoutError:
                        continue
                    except Exception:
                        time.sleep(0.2)
                        continue

                    # Pre-filter: skip sub-word fragments (<0.3s) and faint clicks (<150 RMS)
                    try:
                        raw_data = audio.get_raw_data()
                        dur = len(raw_data) / (audio.sample_rate * audio.sample_width)
                        if dur < 0.3:
                            continue
                        import audioop
                        if audioop.rms(raw_data, audio.sample_width) < 150:
                            continue
                    except Exception:
                        pass

                    # Transcribe
                    try:
                        transcript = speech.transcribe_local_or_cloud(audio, wake_rec).strip()
                    except Exception:
                        continue

                    if not transcript:
                        continue

                    transcript_lower = transcript.lower()

                    # Barge-in stop check while ARIA is speaking
                    if hud.CURRENT_STATE == "speaking" or speech.is_speaking():
                        if any(stop_w in transcript_lower for stop_w in speech.STOP_WORDS):
                            speech.interrupt_speech()
                            hud.set_hud_state("idle")
                            add_log("Barge-in: speech halted.")
                        # Do not process wake word commands from speech coming out of ARIA's own speakers
                        continue

                    # Check for wake word
                    is_wake, cleaned_cmd = _extract_wake_command(transcript)
                    if not is_wake:
                        continue

                    add_log(f"Wake: '{transcript[:30]}'")

                    # Case 1: Single turn with command ('Aria, open Spotify')
                    if cleaned_cmd:
                        speech.interrupt_speech()
                        hud.set_hud_state("excited")
                        EXCITED_UNTIL = time.time() + 1.5
                        EXCITED_REVERT = "thinking"
                        threading.Thread(target=handle_action, args=("voice", cleaned_cmd), daemon=True).start()

                    # Case 2: Conversational wake ('Aria' / 'Hey Aria' alone)
                    else:
                        speech.interrupt_speech()
                        hud.set_hud_state("excited")
                        speech.speak("I'm listening.")
                        speech.wait_until_done(timeout=3.0)
                        time.sleep(0.15)
                        hud.set_hud_state("listening")
                        try:
                            follow_audio = wake_rec.listen(source, timeout=6.0, phrase_time_limit=12.0)
                            follow_cmd = speech.transcribe_local_or_cloud(follow_audio, wake_rec).strip()
                            if follow_cmd:
                                add_log(f"Voice directive: '{follow_cmd[:35]}'")
                                hud.set_hud_state("thinking")
                                threading.Thread(target=handle_action, args=("voice", follow_cmd), daemon=True).start()
                            else:
                                hud.set_hud_state("idle")
                        except (sr.WaitTimeoutError, Exception):
                            hud.set_hud_state("idle")

        except Exception as e:
            VOICE_LISTENER_ONLINE = False
            add_log(f"Wake listener reset: {e}")
            time.sleep(2.0)


def _greeting_text():
    """Template startup greeting used when the model is unreachable (offline)."""
    h = datetime.now().hour
    part = "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"
    base = "A.R.I.A. online."
    entries = scheduler._today_entries()
    if entries:
        return f"{part}. {base} Today: {scheduler._entries_line(entries)}."
    return f"{part}. {base} Nothing on the schedule today."


def _unique_greeting():
    """Startup greeting, written fresh by the model every boot.

    Soul-aware, schedule-aware, and told not to repeat the previous greeting
    (stored as a system memory). Falls back to the template greeting offline.
    """
    fallback = _greeting_text()
    try:
        entries = scheduler._today_entries()
        sched = scheduler._entries_line(entries) if entries else "nothing on the schedule"
        h = datetime.now().hour
        part = "morning" if h < 12 else "afternoon" if h < 18 else "evening"
        last = memory.memory_get("system", "_last_greeting")
        mood = agent.mood_word()
        text = agent.gemini_text(
            "You write A.R.I.A.'s spoken startup greeting for Alek. One or two "
            "sentences, warm, direct, a little playful - in her voice. Vary the "
            "opening; don't always start with 'Good morning/afternoon/evening'. "
            f"Her current mood is '{mood}' - let it flavor the greeting "
            "lightly (never theatrical; it changes nothing factual). "
            "Make it different from her previous greeting, quoted below. Reply "
            "with ONLY the greeting text, no quotes.",
            [{"role": "user", "parts": [{"text":
                f"It's {part}. Today's schedule: {sched}. "
                f"Previous greeting (do not repeat): {last or 'none yet'}."}]}])
        text = (text or "").strip().strip('"').strip()
        if text and len(text) < 400 and not text.startswith("["):
            try:
                memory.memory_save("system", "_last_greeting", text[:300])
            except Exception as e:
                add_log(f"Greeting generated but not stored: {e}")
            return text
    except Exception as e:
        add_log(f"Unique greeting failed, using template: {e}")
    return fallback


def start_all():
    """Initialize and boot all ARIA agent subsystems."""
    global _PTT_AVAILABLE
    print("[ARIA] Initializing OS subsystems...", flush=True)

    # 0. Databases first — creates the memory tables on a fresh workspace.
    #    Idempotent (CREATE TABLE IF NOT EXISTS), so it is safe every boot.
    memory.init_databases()

    # 1. Wire internal hooks
    _init_wiring()

    # 2. Hardware connection
    hardware.init_hardware()

    # 3. Speech workers
    speech.start_speech_worker()

    # 4. Background threads
    threading.Thread(target=vision.face_track_loop,
                     args=(lambda: agent.BUSY_PROCESSING,),
                     daemon=True).start()

    threading.Thread(target=scheduler.scheduler_loop,
                     args=(lambda: agent.BUSY_PROCESSING, lambda p: handle_action("scheduled", p)),
                     daemon=True).start()

    threading.Thread(target=agent.proactive_heartbeat_loop,
                     args=(speech.speak, lambda: agent.BUSY_PROCESSING, lambda: WHISPER_MODE,
                           lambda: agent.LAST_ACTIVITY, lambda mode, prompt: handle_action(mode=mode, typed_prompt=prompt, silent=True)),
                     daemon=True).start()

    # 4c. Persistent autonomous workers (jobs supervisor, downloads watcher, system resource monitor)
    agent.workers.start_all_workers()

    threading.Thread(target=agent.idle_consolidation_loop,
                     args=(agent.gemini_text, lambda: agent.BUSY_PROCESSING, lambda: agent.LAST_ACTIVITY),
                     daemon=True).start()

    threading.Thread(target=bridge.start_bridge_server, daemon=True).start()

    # 4b. MCP servers: connect configured servers in the background.
    #     Failures are logged, never fatal — ARIA boots fine without them.
    def _mcp_autoconnect():
        try:
            from aria import mcp as mcp_mod
            summary = mcp_mod.autoconnect_enabled_servers()
            add_log(f"MCP autoconnect: {summary}")
        except Exception as e:
            add_log(f"MCP autoconnect failed: {e}")

    threading.Thread(target=_mcp_autoconnect, daemon=True).start()

    # 5. PTT Poller on Windows
    if sys.platform == "win32" and hasattr(ctypes, "windll"):
        _PTT_AVAILABLE = True
        threading.Thread(target=_ptt_poll_loop, daemon=True).start()
        add_log("Push-to-talk armed: hold SPACE to speak.")
    else:
        add_log("Push-to-talk offline (non-Windows platform).")

    # 6. Console typing listener
    if sys.stdin and hasattr(sys.stdin, "readline"):
        threading.Thread(target=_console_input_loop, daemon=True).start()

    # 6b. Wake-word listener — always-on mic, "Aria" + command.
    threading.Thread(target=continuous_voice_listener, daemon=True).start()

    # 7. Spoken greeting — unique every boot. Runs in a background thread so
    # the model call and the voice-ready wait never block startup.
    def _greet_when_ready():
        try:
            for _ in range(250):  # up to ~25s for the Edge voice
                if speech.voice_ready():
                    break
                time.sleep(0.1)
            greeting = _unique_greeting()
            add_log(f"Greeting: {greeting}")
            speech.speak(greeting)
        except Exception as e:
            add_log(f"Greeting thread failed: {e}")
    threading.Thread(target=_greet_when_ready, daemon=True).start()


def main():
    """Main application loop with OpenCV HUD window."""
    global RUNNING

    # ARIA ULTIMATE (Module 10): first-run wizard — checks the environment and
    # API keys before subsystems boot. Fully skippable, never blocks startup.
    try:
        import os as _os
        if not _os.path.exists(_os.path.expanduser("~/ARIA/.first_run_done")):
            from aria import first_run
            first_run.run_first_run_wizard()
    except Exception as _fr_e:
        try:
            add_log(f"First-run wizard skipped: {_fr_e}")
        except Exception:
            pass

    start_all()

    win_name = "A.R.I.A. // OS"
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win_name, _on_hud_mouse)

    try:
        while RUNNING:
            try:
                frame = hud.draw_hud()
            except Exception as e:
                frame = np.zeros((720, 1280, 3), dtype=np.uint8)
                cv2.putText(frame, f"HUD RENDER FAULT: {e}", (50, 360),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1, cv2.LINE_AA)
                # Full traceback goes to the log so a screenshot is never the
                # only evidence: without this the next fault is undiagnosable.
                try:
                    import traceback as _tb
                    add_log("HUD RENDER FAULT:\n" + "".join(
                        _tb.format_exception(type(e), e, e.__traceback__))[:2000])
                except Exception:
                    pass
            cv2.imshow(win_name, frame)

            # wake-up burst expiry: settle back once her excited moment passes
            if hud.CURRENT_STATE == "excited" and time.time() >= EXCITED_UNTIL:
                hud.set_hud_state(EXCITED_REVERT)

            key_raw = cv2.waitKeyEx(30)
            if key_raw != -1:
                key = key_raw & 0xFF
                is_left = (key_raw in (0x250000, 2424832, 65361)) or (key_raw >> 16 == 0x25)
                is_right = (key_raw in (0x270000, 2555904, 65363)) or (key_raw >> 16 == 0x27)
                is_up = (key_raw in (0x260000, 2490368, 65362)) or (key_raw >> 16 == 0x26)
                is_down = (key_raw in (0x280000, 2621440, 65364)) or (key_raw >> 16 == 0x28)

                # Check modifier keys via Windows API
                is_ctrl = False
                is_shift = False
                is_alt = False
                try:
                    is_ctrl = bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000)
                    is_shift = bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)
                    is_alt = bool(ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000)
                except Exception:
                    pass

                # Universal paste shortcut: Ctrl+V or Shift+Insert
                is_paste = (key == 22) or (key in (ord('v'), ord('V')) and is_ctrl) or (key_raw in (45, 0x2D0000) and is_shift)

                if is_paste:
                    pasted = hud.get_clipboard_text()
                    hud.TYPING_ACTIVE = True
                    if pasted:
                        hud.TYPING_BUFFER += pasted
                        agent.LAST_ACTIVITY = time.time()
                        add_log(f"Pasted from clipboard ({len(pasted)} chars).")
                    else:
                        add_log("Clipboard empty or non-text.")
                elif key in (ord('x'), ord('X')) and hud.CURRENT_STATE == "speaking" and not hud.TYPING_ACTIVE:
                    # X: cut her off — stop speech immediately. Only fires while
                    # she's actually talking and not currently typing.
                    drained = speech.interrupt_speech()
                    hud.set_hud_state("idle")
                    add_log("Speech cut off." if not drained
                            else f"Speech cut off ({drained} queued cleared).")
                    agent.LAST_ACTIVITY = time.time()
                elif key in (ord('c'), ord('C')) and not hud.TYPING_ACTIVE:
                    # C: cycle HUD color theme. Guarded so typing 'c' is unaffected.
                    name = pixel_avatar.cycle_theme()
                    add_log(f"Color theme: {name}.")
                    agent.LAST_ACTIVITY = time.time()
                elif hud.SHOW_COMMANDS:
                    if is_left or is_up:
                        hud.commands_prev_page()
                    elif is_right or is_down:
                        hud.commands_next_page()
                    elif key in (ord('h'), ord('H'), 27):  # H or ESC closes commands
                        hud.SHOW_COMMANDS = False
                elif hud.TYPING_ACTIVE:
                    if hud.HUD_MODE == "ops" and _ops_screen.ACTIVE_TAB == "notes":
                        # Notes tab: typing edits the notes buffer (autosaves).
                        if key in (13, 10):  # Enter: newline
                            _ops_screen.notes_newline()
                        elif key == 27:  # ESC: stop editing
                            hud.TYPING_ACTIVE = False
                            _ops_screen.save_notes()
                            add_log("Notes saved.")
                        elif key in (8, 127):  # Backspace
                            _ops_screen.notes_backspace()
                        elif 32 <= key_raw <= 126 and not is_ctrl:  # Printable
                            _ops_screen.notes_type(chr(key_raw))
                            agent.LAST_ACTIVITY = time.time()
                    elif key in (13, 10):  # Enter: submit directive
                        prompt = hud.TYPING_BUFFER.strip()
                        hud.TYPING_ACTIVE = False
                        hud.TYPING_BUFFER = ""
                        if prompt:
                            add_log(f"Typed directive: '{prompt[:35]}...'")
                            threading.Thread(target=handle_action, args=("voice", prompt), daemon=True).start()
                    elif key == 27:  # ESC: cancel typing mode
                        hud.TYPING_ACTIVE = False
                        hud.TYPING_BUFFER = ""
                        add_log("Typing cancelled.")
                    elif key in (8, 127):  # Backspace
                        hud.TYPING_BUFFER = hud.TYPING_BUFFER[:-1]
                    elif key == 21:  # Ctrl+U: clear buffer
                        hud.TYPING_BUFFER = ""
                    elif 32 <= key_raw <= 126 and not is_ctrl:  # Printable character (including space)
                        hud.TYPING_BUFFER += chr(key_raw)
                        agent.LAST_ACTIVITY = time.time()
                else:
                    if hud.HUD_MODE == "ops" and key in (ord('o'), ord('O'), 27):
                        hud.HUD_MODE = "visor"  # O or ESC: back to face
                        _ops_screen.save_state()
                        add_log("OPS closed.")
                        agent.LAST_ACTIVITY = time.time()
                    elif hud.HUD_MODE == "ops" and key in (ord('q'), ord('Q')):
                        hud.HUD_MODE = "visor"  # Q exits OPS; shutdown from visor only
                        _ops_screen.save_state()
                        add_log("OPS closed.")
                        agent.LAST_ACTIVITY = time.time()
                    elif hud.HUD_MODE == "ops" and key in tuple(ord(str(d)) for d in range(1, 8)):
                        _ops_screen.set_tab(int(chr(key)) - 1)  # 1-7: switch OPS tab
                        agent.LAST_ACTIVITY = time.time()
                    elif hud.HUD_MODE == "ops" and is_ctrl and is_alt and key in (ord('l'), ord('L')):
                        _ops_screen.set_tab(0)  # Ctrl+Alt+L: focus Log pane
                        agent.LAST_ACTIVITY = time.time()
                    elif hud.HUD_MODE == "ops" and is_ctrl and is_alt and key in (ord('t'), ord('T')):
                        _ops_screen.set_tab(1)  # Ctrl+Alt+T: focus Tasks pane
                        agent.LAST_ACTIVITY = time.time()
                    elif hud.HUD_MODE == "ops" and is_ctrl and is_alt and key in (ord('s'), ord('S')):
                        _ops_screen.set_tab(2)  # Ctrl+Alt+S: focus Sensors pane
                        agent.LAST_ACTIVITY = time.time()
                    elif hud.HUD_MODE == "ops" and (key in (ord('j'), ord('J')) or is_down):
                        _ops_screen.scroll_active(-3)
                        agent.LAST_ACTIVITY = time.time()
                    elif hud.HUD_MODE == "ops" and (key in (ord('k'), ord('K')) or is_up):
                        _ops_screen.scroll_active(3)
                        agent.LAST_ACTIVITY = time.time()
                    elif hud.HUD_MODE == "ops" and key in (ord('e'), ord('E')):
                        threading.Thread(target=_ops_read_selected_aloud, daemon=True).start()
                        agent.LAST_ACTIVITY = time.time()
                    elif hud.HUD_MODE == "ops" and key in (ord('r'), ord('R')):
                        _ops.refresh_all()
                        add_log("OPS refreshing...")
                        agent.LAST_ACTIVITY = time.time()
                    elif key in (ord('q'), ord('Q'), 27):  # ESC or Q
                        add_log("Shutdown requested by user.")
                        break
                    elif key in (ord('t'), ord('T'), 13):  # T or Enter: activate typing
                        hud.TYPING_ACTIVE = True
                        add_log("Typing mode: type your directive and press Enter.")
                    elif key in (ord('1'), ord('2'), ord('3'), ord('4'), ord('5')):
                        tile_map = {
                            ord('1'): "dashboard",
                            ord('2'): "subtitles",
                            ord('3'): "audio",
                            ord('4'): "tasks",
                            ord('5'): "spotify"
                        }
                        hud.set_tile_mode(tile_map[key])
                        add_log(f"Context tile: {tile_map[key].upper()}")
                    elif key in (9, ord('\t')):
                        cm = hud.cycle_tile_mode()
                        add_log(f"Context tile: {cm.upper()}")
                    elif key in (ord('v'), ord('V')) and not is_ctrl:
                        hud.HUD_MODE = "chat_log" if hud.HUD_MODE == "visor" else "visor"
                    elif key in (ord('o'), ord('O')):
                        hud.HUD_MODE = "ops"
                        _ops_screen.open_panel()  # land on the HUB tab
                        _ops.refresh_all()
                        add_log("OPS command center.")
                        agent.LAST_ACTIVITY = time.time()
                    elif key in (ord('w'), ord('W')):
                        on = toggle_whisper_mode()
                        speech.speak(f"Whisper mode {'on' if on else 'off'}.")
                    elif key in (ord('h'), ord('H')):
                        hud.SHOW_COMMANDS = not hud.SHOW_COMMANDS
                    elif key in (ord('j'), ord('J')) or is_down:
                        hud.CHAT_SCROLL = max(0, hud.CHAT_SCROLL - 1)
                    elif key in (ord('k'), ord('K')) or is_up:
                        hud.CHAT_SCROLL = hud.CHAT_SCROLL + 1

    except KeyboardInterrupt:
        pass
    finally:
        RUNNING = False
        cv2.destroyAllWindows()
        memory.spine_write_resume_card()
        print("[ARIA] Shutdown complete. Resume card recorded.", flush=True)


if __name__ == "__main__":
    main()

# Bridge mobile audio fix updated
