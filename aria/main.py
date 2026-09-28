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
from typing import Optional, List, Dict, Any, Tuple

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
import aria.bridge as bridge
import aria.agent as agent
from aria.tools.dispatch import (
    execute_tool, set_log_hook, set_hud_hook, set_history_hook, set_spine_hook
)

# Global runtime flags
RUNNING: bool = True
PENDING_CONFIRM: Optional[Dict[str, Any]] = None
WHISPER_MODE: bool = bool(get_setting("whisper_mode", False))

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
    set_spine_hook(memory.spine_append)
    set_history_hook(lambda: agent.CONVERSATION_HISTORY)

    # 2. Chat history listener
    memory.register_chat_listener(lambda ts, sender, msg: hud.DISPLAY_CHAT_LOG.append((ts, sender, msg)))

    # 3. Vision callers
    vision.set_vision_text_caller(agent.gemini_text)

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
    
    # 1. Vision
    v_ok = v_stat["vision_last"][0]
    v_str = "READY" if v_ok else ("ERROR" if v_ok is False else "IDLE")
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

    return [vision_entry, screen_entry, mem_entry, hw_entry, sched_entry, gh_entry]


def set_whisper_mode(on: bool) -> bool:
    global WHISPER_MODE
    WHISPER_MODE = bool(on)
    hud.WHISPER_MODE = WHISPER_MODE
    set_setting("whisper_mode", WHISPER_MODE)
    add_log(f"Whisper mode {'enabled' if WHISPER_MODE else 'disabled'}.")
    return WHISPER_MODE


def toggle_whisper_mode() -> bool:
    return set_whisper_mode(not WHISPER_MODE)


_YES_FIRST_WORDS = {"yes", "yeah", "yep", "yup", "y", "sure", "ok", "okay",
                    "confirmed", "affirmative", "absolutely", "definitely"}


def _resolve_confirmation(user_text: str, say_fn: Callable[[str], None]) -> bool:
    global PENDING_CONFIRM
    if not PENDING_CONFIRM:
        return False
    low = user_text.lower().strip()
    words = re.findall(r"[a-z']+", low)
    first = words[0] if words else ""
    pc = PENDING_CONFIRM
    PENDING_CONFIRM = None
    memory.log_conversation("User", user_text)

    if first in _YES_FIRST_WORDS or low.startswith(("do it", "go ahead")):
        add_log(f"Confirmed: {pc['fn']}")
        res, _ = execute_tool(pc["fn"], pc["args"], preauthorized=True)
        say_fn(f"Confirmed. {res[:300]}")
        agent.CONVERSATION_HISTORY.append({
            "role": "model",
            "parts": [{"text": f"[Confirmed and executed: {pc.get('desc', pc['fn'])}]"}]
        })
    else:
        add_log(f"Cancelled: {pc['fn']}")
        say_fn("Cancelled. Nothing was done.")
        agent.CONVERSATION_HISTORY.append({
            "role": "model",
            "parts": [{"text": f"[Cancelled by user: {pc.get('desc', pc['fn'])}]"}]
        })
    return True


def handle_action(mode: str = "voice", typed_prompt: Optional[str] = None, silent: bool = False) -> str:
    """Central action pipeline invoked from voice, PTT, HUD typed commands, or Phone Bridge."""
    global PENDING_CONFIRM
    agent.LAST_ACTIVITY = time.time()
    agent.proactive.mood_note_interaction()

    say = (lambda t: None) if silent else speech.speak

    user_text = typed_prompt if typed_prompt else ""
    if not user_text:
        return ""

    low = user_text.lower().strip()

    # 1. Stop words
    if any(low == w or low.startswith(w + " ") for w in speech.STOP_WORDS):
        was_pending = PENDING_CONFIRM is not None
        PENDING_CONFIRM = None
        speech.interrupt_speech()
        if was_pending:
            add_log("Pending action cancelled by stop command.")
        return "Interrupted."

    # 2. Confirmation resolution
    if _resolve_confirmation(user_text, say):
        return "Confirmation resolved."

    # 3. Whisper mode voice commands
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
    """Handle mouse clicks on HUD interactive buttons."""
    if event == cv2.EVENT_LBUTTONDOWN:
        # Whisper button
        bx, by, bw, bh = hud._WHISPER_BTN
        if bx <= x <= bx + bw and by <= y <= by + bh:
            on = toggle_whisper_mode()
            speech.speak(f"Whisper mode {'on' if on else 'off'}.")
            return

        # Directive input bar
        ix, iy, iw, ih = hud._INPUT_BAR
        if ix <= x <= ix + iw and iy <= y <= iy + ih:
            hud.TYPING_ACTIVE = True
            hud.TYPING_BUFFER = ""
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


def continuous_voice_listener():
    """Always-on wake-word mic loop (restored from v9.34).

    While ARIA is idle, the mic stays open. Any transcript containing
    "aria" is treated as a command: the wake word is stripped and the
    rest runs through handle_action("voice", ...), with barge-in so a
    new command cuts off current speech. A bare "Aria" with nothing
    after it gets "I'm listening."
    """
    try:
        with sr.Microphone(sample_rate=16000) as source:
            recognizer.adjust_for_ambient_noise(source, duration=1.0)
            add_log("Wake-word listener armed ('Aria' + command).")
            while RUNNING:
                try:
                    if agent.BUSY_PROCESSING or hud.CURRENT_STATE != "idle":
                        time.sleep(0.3)
                        continue
                    try:
                        audio = recognizer.listen(source, timeout=3, phrase_time_limit=8)
                    except sr.WaitTimeoutError:
                        continue
                    try:
                        transcript = speech.transcribe_local_or_cloud(audio, recognizer).lower()
                    except Exception:
                        continue
                    if "aria" in transcript:
                        add_log(f"Wake: '{transcript[:25]}'")
                        cleaned = transcript.replace("hey aria", "").replace("aria", "").strip()
                        if cleaned:
                            speech.interrupt_speech()  # barge-in: stop current speech first
                            handle_action("voice", typed_prompt=cleaned)
                        else:
                            speech.speak("I'm listening.")
                except Exception as e:
                    add_log(f"Wake listener error: {e}")
                    time.sleep(0.3)
    except Exception as e:
        add_log(f"Wake listener unavailable: {e}")


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
                     args=(speech.speak, lambda: agent.BUSY_PROCESSING, lambda: WHISPER_MODE, lambda: agent.LAST_ACTIVITY),
                     daemon=True).start()

    threading.Thread(target=agent.idle_consolidation_loop,
                     args=(agent.gemini_text, lambda: agent.BUSY_PROCESSING, lambda: agent.LAST_ACTIVITY),
                     daemon=True).start()

    threading.Thread(target=bridge.start_bridge_server, daemon=True).start()

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
    start_all()

    win_name = "A.R.I.A. // OS"
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win_name, _on_hud_mouse)

    try:
        while RUNNING:
            frame = hud.draw_hud()
            cv2.imshow(win_name, frame)

            key = cv2.waitKey(30) & 0xFF
            if key != 255:
                if hud.TYPING_ACTIVE:
                    if key in (13, 10):  # Enter: submit directive
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
                    elif 32 <= key <= 126:  # Printable character (including space)
                        hud.TYPING_BUFFER += chr(key)
                        agent.LAST_ACTIVITY = time.time()
                else:
                    if key in (ord('q'), ord('Q'), 27):  # ESC or Q
                        add_log("Shutdown requested by user.")
                        break
                    elif key in (ord('t'), ord('T'), 13):  # T or Enter: activate typing
                        hud.TYPING_ACTIVE = True
                        hud.TYPING_BUFFER = ""
                        add_log("Typing mode: type your directive and press Enter.")
                    elif key in (ord('v'), ord('V')):
                        hud.HUD_MODE = "chat_log" if hud.HUD_MODE == "visor" else "visor"
                    elif key in (ord('w'), ord('W')):
                        on = toggle_whisper_mode()
                        speech.speak(f"Whisper mode {'on' if on else 'off'}.")
                    elif key in (ord('h'), ord('H')):
                        hud.SHOW_COMMANDS = not hud.SHOW_COMMANDS
                    elif key in (ord('j'), ord('J')):
                        hud.CHAT_SCROLL = max(0, hud.CHAT_SCROLL + 1)
                    elif key in (ord('k'), ord('K')):
                        hud.CHAT_SCROLL = max(0, hud.CHAT_SCROLL - 1)

    except KeyboardInterrupt:
        pass
    finally:
        RUNNING = False
        cv2.destroyAllWindows()
        memory.spine_write_resume_card()
        print("[ARIA] Shutdown complete. Resume card recorded.", flush=True)


if __name__ == "__main__":
    main()
