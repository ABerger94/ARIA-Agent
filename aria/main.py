"""
A.R.I.A. Autonomous Agent Operating System — Main Orchestrator.
Integrates all subsystems: Config, Memory Spine, Speech Pipeline, Hardware Servos,
Vision Optics, Background Scheduler, Spotify Media, Phone Bridge, and Agent Brain.
"""

from __future__ import annotations

import ctypes
import re
import sys
import threading
import time
from datetime import datetime
from typing import Optional, List, Tuple

import cv2
import numpy as np
import speech_recognition as sr

# Core package imports
from aria.config import (
    GITHUB_USERNAME, GITHUB_TOKEN,
    get_setting, set_setting, add_log, register_log_listener
)
import aria.memory as memory
import aria.speech as speech
import aria.hardware as hardware
import aria.vision as vision
import aria.scheduler as scheduler
import aria.hud as hud
import aria.frame as _frame
import aria.pixel_avatar as pixel_avatar
import aria.bridge as bridge
import aria.agent as agent
from aria.tools.dispatch import (
    set_log_hook, set_hud_hook, set_history_hook, set_spine_hook,
)
from aria import config as _config_silent

# Global runtime flags
RUNNING: bool = True


def request_shutdown():
    """Frame shutdown control: stop the main loop cleanly."""
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
    speech.set_speech_log_hook(add_log)
    set_spine_hook(memory.spine_append)
    set_history_hook(lambda entry: agent.CONVERSATION_HISTORY.append(entry))

    # 2. Chat history listener
    memory.register_chat_listener(hud.add_display_chat)

    # 2b. Phone notifications (aria/notify.py): the toast sink lands
    # in the Activity log as a NOTIFY line, so a notification is
    # visible on the desktop as well as on the phone.
    try:
        from aria import notify as _notify_mod

        def _notify_to_log(msg):
            if isinstance(msg, dict):
                add_log(f"NOTIFY: {msg.get('title', 'ARIA')} — "
                        f"{msg.get('body', '')}")
            else:
                add_log(f"NOTIFY: {msg}")

        _notify_mod.bind_broadcaster(_notify_to_log)
    except Exception:
        pass

    # 3. Vision callers — use the native Ollama vision path
    # (_default_vision_call: native /api/chat images array first, then the
    # provider chain). agent.gemini_text was the legacy hook but Gemini keys
    # are 402-dead, so wiring it here silently bypassed the working path.
    vision.set_vision_text_caller(vision._default_vision_call)

    # 4. Bridge processor & chat log
    bridge.set_bridge_processor(lambda text, silent: handle_action("voice", typed_prompt=text, silent=silent, via_bridge=True))
    bridge.set_chat_log_provider(memory.get_display_chat_log)

    # 5. HUD mood & subsystems
    hud.set_mood_callback(lambda: agent.mood_word())
    hud.set_subsystems_callback(get_subsystem_statuses)

    # 6. Frame (Phase 4): command-bar submit, whisper pill, MIC push-to-talk
    _frame.set_submit_callback(_submit_typed)
    _frame.set_whisper_callback(toggle_whisper_mode)
    _frame.set_ptt_callbacks(_ptt_rising_edge, _ptt_falling_edge)


def _submit_typed(text: str) -> None:
    """Route a typed directive (frame command bar) to the brain — the
    same handle_action path voice commands take."""
    threading.Thread(target=handle_action, args=("voice", text),
                     daemon=True).start()




def get_subsystem_statuses() -> List[Tuple[str, str, bool]]:
    """Live (name, status, is_ok) for the HUD subsystem display."""
    v_stat = vision.get_vision_status()
    h_stat = hardware.get_hardware_status()

    # 0. Voice Pathway — listener state AND the TTS engine state, so
    # the dashboard's left column shows at a glance whether she is
    # speaking with Edge or the fallback (2026-10-27: the fallback
    # was invisible everywhere — no log line, no status surface).
    _tts_state = "EDGE" if speech.voice_ready() else "FALLBACK"
    voice_entry = ("Voice Pathway", f"{'ARMED' if VOICE_LISTENER_ONLINE else 'OFFLINE'} · {_tts_state}", VOICE_LISTENER_ONLINE)

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


def handle_action(mode: str = "voice", typed_prompt: Optional[str] = None, silent: bool = False, via_bridge: bool = False) -> str:
    """Central action pipeline invoked from voice, PTT, HUD typed commands, or Phone Bridge."""
    # App lock (Alek, 2026-10-27): while locked, local voice / PTT
    # / typed actions are refused. The phone bridge (via_bridge)
    # and scheduled turns are exempt — his split: he runs ARIA
    # from his phone while the laptop sits locked at home.
    if not via_bridge and mode != "scheduled":
        try:
            from aria import applock as _applock_ha
            if _applock_ha.is_locked():
                add_log("Locked: local action refused.")
                return "ARIA is locked."
        except Exception:
            pass
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

    # 3. Local command lane (pass 6): exact media phrases take a
    # direct path — no model round trip. Whole-utterance matches
    # only; anything else falls through to the brain untouched.
    try:
        from aria import command_lane as _lane
        lane_ack = _lane.try_handle(user_text)
    except Exception:
        lane_ack = None
    if lane_ack is not None:
        say(lane_ack)
        return lane_ack

    # 4. Task bookmark lanes (pass 6): "save this for later"
    # snapshots the current task from history; "where were we"
    # resumes it as a brain turn carrying the bookmark context.
    try:
        from aria import bookmark as _bookmark
        save_ack = _bookmark.try_save(user_text,
                                      agent.brain.CONVERSATION_HISTORY)
        if save_ack is not None:
            say(save_ack)
            return save_ack
        if _bookmark.is_resume(user_text):
            bm = _bookmark.load()
            if bm is None:
                ack = _bookmark.nothing_saved_ack()
                say(ack)
                return ack
            return agent.run_agent(_bookmark.resume_prompt(bm),
                                   silent=silent)
    except Exception:
        pass

    # 4b. Clipboard attachment (pass 6): text staged from the
    # dashboard rides this turn as a context block. Scheduled turns
    # never consume it — it waits for the user's own next ask.
    if mode != "scheduled":
        try:
            from aria import context_attach as _attach
            block = _attach.consume_block()
        except Exception:
            block = None
        if block:
            user_text = block + "\n\n" + user_text

    # 5. Multimodal context attachment
    image_bytes = None
    is_screen = False
    if mode == "screen" or any(k in low for k in ["screen", "display", "desktop", "my window"]):
        image_bytes = vision.capture_screen_if_changed()
        if image_bytes is not None:
            # The dashboard PiP (Alek, 2026-10-27): this inline
            # capture is the primary "what do you see" path — no
            # vision TOOL runs for it, so the publish lives here,
            # not only in the vision tools.
            vision.publish_vision_preview(image_bytes, "SCREEN")
        is_screen = True
        if image_bytes is None:
            user_text = "[Screen unchanged since my last view] " + user_text
    elif mode == "camera" or any(k in low for k in ["look", "see", "holding", "camera"]):
        image_bytes = vision.capture_webcam()
        if image_bytes:
            vision.publish_vision_preview(image_bytes, "CAMERA")

    # 6. Run Agent Brain
    return agent.run_agent(user_text, image_bytes=image_bytes, is_screen=is_screen, silent=silent)


def _ptt_rising_edge():
    global _PTT_RECORDING, _PTT_STOP, _PTT_STARTED_AT
    if _PTT_RECORDING or agent.BUSY_PROCESSING or hud.TYPING_ACTIVE:
        return
    _PTT_RECORDING = True
    _PTT_STOP = False
    _PTT_STARTED_AT = time.time()
    agent.brain.LAST_ACTIVITY = time.time()  # write-through: brain owns the live value (package attr would shadow it)
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


def _mic_device():
    """Persisted input-device choice (Audio panel); None = system default.

    Never raises — a broken prefs file must not silence the microphone.
    """
    try:
        from aria import audio_prefs
        return audio_prefs.get_input_device()
    except Exception:
        return None


def _ptt_capture():
    frames = []
    sample_rate = 16000
    sample_width = 2

    try:
        with speech.SoundDeviceSource(sample_rate=16000, device=_mic_device()) as source:
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
                hud.set_mic_level(min(1.0, speech.audioop.rms(raw_chunk, sample_width) / 3000.0))
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


def _hud_mouse_wheel(x, y, up):
    """Wheel: page the commands overlay, else the frame wheel routes it
    (activity-column scroll, volume). Host-agnostic — the cv2 callback
    and the Qt host (Phase 4e) both call this."""
    if hud.SHOW_COMMANDS:
        if up:
            hud.commands_prev_page()
        else:
            hud.commands_next_page()
        return
    hud.handle_wheel(x, y, up)


def _hud_mouse_drag(x, y):
    """Drag with the left button held (volume slider)."""
    hud.handle_drag(x, y)


def _hud_mouse_release(x, y):
    """Left-button release (push-to-talk edge)."""
    hud.handle_release(x, y)


def _hud_mouse_press(x, y, button):
    """Mouse press. button is "left" or "right"."""
    if button == "right":
        # Right click anywhere on the command bar pastes from clipboard
        ix, iy, iw, ih = _frame.input_rect()
        if ix <= x <= ix + iw and iy <= y <= iy + ih:
            pasted = hud.get_clipboard_text()
            if pasted:
                hud.TYPING_ACTIVE = True
                hud.TYPING_BUFFER += pasted
                add_log(f"Pasted from clipboard ({len(pasted)} chars).")
            else:
                add_log("Clipboard empty or non-text.")
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

        # Clickable command buttons — execute the phrase
        for bx, by, bw, bh, phrase in hud.COMMANDS_CLICK_BOXES:
            if bx <= x <= bx + bw and by <= y <= by + bh:
                hud.SHOW_COMMANDS = False
                hud.add_hud_log(f"Running: {phrase[:40]}")
                threading.Thread(
                    target=handle_action,
                    args=("voice", phrase),
                    daemon=True).start()
                return

        # Clicking anywhere outside overlay bounds closes it
        if not (36 <= x <= 1244 and 52 <= y <= 700):
            hud.SHOW_COMMANDS = False
            return

        return

    # Frame widgets: command bar (CMDS / MIC / VOICE / input / PASTE /
    # SEND / CLR), Spotify transport, volume slider, drop zone, whisper
    # pill — all hit-tested inside aria.frame via hud.handle_click.
    hud.handle_click(x, y)


def _on_hud_mouse(event, x, y, flags, param):
    """cv2 mouse callback — thin adapter over the semantic handlers
    above, which the Qt host (Phase 4e) calls directly."""
    if event == getattr(cv2, "EVENT_MOUSEWHEEL", 10):
        _hud_mouse_wheel(x, y, flags > 0)
    elif event == cv2.EVENT_MOUSEMOVE and (flags & cv2.EVENT_FLAG_LBUTTON):
        _hud_mouse_drag(x, y)
    elif event == cv2.EVENT_LBUTTONUP:
        _hud_mouse_release(x, y)
    elif event == cv2.EVENT_RBUTTONDOWN:
        _hud_mouse_press(x, y, "right")
    elif event == cv2.EVENT_LBUTTONDOWN:
        _hud_mouse_press(x, y, "left")


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
            with speech.SoundDeviceSource(device=_mic_device()) as source:
                VOICE_LISTENER_ONLINE = True
                try:
                    wake_rec.adjust_for_ambient_noise(source, duration=0.8)
                    if wake_rec.energy_threshold < 250:
                        wake_rec.energy_threshold = 250
                except Exception as _e_silent:
                    # Merger Phase 1 (blueprint SS3.2): a failed
                    # ambient calibration is a classified SKIP (the
                    # listener arms anyway), not a silent-log line.
                    try:
                        from aria.agent.failures import surface_failure
                        surface_failure(
                            "main.ambient_calibration", _e_silent,
                            decision="skip",
                            user_message="Microphone calibration skipped; listening anyway.")
                    except Exception:
                        _config_silent.log_silent("continuous_voice_listener", _e_silent)
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
                        _rms = audioop.rms(raw_data, audio.sample_width)
                        # Publish the level the frame waveform renders
                        # while listening (same RMS, no extra capture).
                        hud.set_mic_level(min(1.0, _rms / 3000.0))
                        if _rms < 150:
                            continue
                    except Exception as _e_silent:
                        _config_silent.log_silent("continuous_voice_listener", _e_silent)

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

                    # App lock (Alek, 2026-10-27): while locked, the
                    # wake word has exactly one job — take the
                    # password. "Hey Aria, <password>" in one breath,
                    # or "Hey Aria" then the password as the follow-up
                    # utterance. Anything else is ignored; she stays
                    # silent and locked.
                    try:
                        from aria import applock as _applock_wk
                        _locked_now = _applock_wk.is_locked()
                    except Exception:
                        _locked_now = False
                    if _locked_now:
                        candidate = cleaned_cmd
                        if not candidate:
                            hud.set_hud_state("listening")
                            try:
                                follow_audio = wake_rec.listen(source, timeout=6.0, phrase_time_limit=6.0)
                                candidate = speech.transcribe_local_or_cloud(follow_audio, wake_rec).strip()
                                _cw, _cc = _extract_wake_command(candidate)
                                if _cw and _cc:
                                    candidate = _cc
                            except Exception:
                                candidate = ""
                        if candidate and _applock_wk.unlock_with(candidate):
                            add_log("Unlocked by voice.")
                            hud.set_hud_state("idle")
                            speech.speak("Unlocked. Welcome back.")
                        else:
                            add_log("Locked: voice unlock attempt failed.")
                            hud.set_hud_state("idle")
                        continue

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
    entries, _is_live = scheduler.today_entries_prefer_live()
    if entries:
        return f"{part}. {base} Today: {scheduler._entries_line(entries)}."
    return f"{part}. {base} Nothing on the schedule today."


def _unique_greeting():
    """A daily briefing composed from current sources, never improvised plans."""
    from aria.briefing import build_briefing
    return build_briefing()['text']


# ---------------------------------------------------------------- presence
_PRESENCE_MONITOR = None


def _presence_on_return(away_s, greet):
    add_log(f"Presence: Alek returned after {int(away_s)}s away")
    if not greet:
        return
    try:
        from aria import presence as _presence
        from datetime import datetime as _dt
        text = _presence.greeting_for(_dt.now().hour, away_s,
                                      _presence.today_index())
        # Her life, reported (Alek, 2026-10-29): after a real
        # absence, one clause about what she did in her room
        # while he was out — from the life diary, fail-soft.
        if away_s >= 1800:
            try:
                import time as _time
                from aria import life_diary as _life_diary
                clause = _life_diary.summary_for_window(
                    None, _time.time() - away_s)
                if clause:
                    text = f"{text} {clause}"
            except Exception:
                pass
        speech.speak(text)
    except Exception as e:
        add_log(f"Presence greeting failed: {e}")


def _start_presence():
    """Build + register the presence monitor (aria/presence.py) and
    drive it on a 5s daemon loop. Presence only ever SILENCES
    unprompted speech — it never gates a typed or spoken command,
    and every failure leaves ARIA exactly as she was without it
    (fail-open, the module's own hard rule)."""
    global _PRESENCE_MONITOR
    try:
        if not _config_silent.get_setting('presence_enabled', True):
            return
        from aria import presence as _presence
        idle_after = float(
            _config_silent.get_setting('presence_idle_s', 600) or 600)

        def _media_playing():
            # Media veto: something actually playing means somebody
            # is (almost always) in the room. SMTC first (the OS's
            # own now-playing), then the window-title telemetry for
            # when SMTC is dark. A broken probe reads as not-playing
            # — it never vetoes.
            try:
                from aria import smtc as _smtc
                info = _smtc.now_playing_any()
                if info and info.get("playing"):
                    return True
            except Exception:
                pass
            try:
                tele = hud.media_window_telemetry()
                if tele and tele.get("playing"):
                    return True
            except Exception:
                pass
            return False

        def _busy_probe():
            if speech.is_speaking() or agent.BUSY_PROCESSING:
                return True
            # Live veto, with the helper's discipline: an open Live
            # session counts as activity only while his speech is
            # recent — the bare session stays open for hours after
            # he walks away (aria/presence.py's session helper).
            try:
                mgr = _LIVE_MANAGER
                active = bool(mgr is not None
                              and getattr(mgr, "active", False))
                from aria.agent import brain as _brain_mod
                return _presence.session_counts_as_activity(
                    active,
                    time.time() - float(_brain_mod.LAST_ACTIVITY),
                    idle_after)
            except Exception:
                return False

        monitor = _presence.PresenceMonitor(
            idle_signal=_presence.idle_seconds,
            locked_signal=_presence.locked,
            media_signal=_media_playing,
            busy_probe=_busy_probe,
            idle_after_s=idle_after,
            on_away=lambda reason: add_log(f"Presence: away ({reason})"),
            on_return=_presence_on_return)
        _presence.set_monitor(monitor)
        _PRESENCE_MONITOR = monitor

        def _loop():
            while True:
                try:
                    monitor.tick()
                except Exception:
                    pass
                time.sleep(5)

        threading.Thread(target=_loop, daemon=True).start()
        add_log("Presence monitor started")
    except Exception as e:
        add_log(f"Presence monitor unavailable: {e}")


# ---------------------------------------------------------------- life room
_LIFE_SUPERVISOR = None


def _life_control():
    """What the main process knows, for the life subprocess's
    control file (aria/life_supervisor.py). Every probe fails
    soft: a broken probe reads as its quiet value, never as a
    reason to wake her or keep her from pausing."""
    asleep = False
    try:
        from aria import presence as _presence
        asleep = bool(_presence.locked())
    except Exception:
        pass
    try:
        from aria import applock as _applock
        asleep = asleep or bool(_applock.is_locked())
    except Exception:
        pass
    with_alek = False
    try:
        with_alek = bool(speech.is_speaking() or agent.BUSY_PROCESSING)
    except Exception:
        pass
    media = False
    try:
        from aria import smtc as _smtc
        info = _smtc.now_playing_any()
        media = bool(info and info.get("playing"))
    except Exception:
        pass
    from datetime import datetime as _dt
    hour = _dt.now().hour
    return {
        "asleep": asleep,
        "with_alek": with_alek,
        "media_playing": media,
        "quiet": hour >= 23 or hour < 7,
    }


def _life_journal(context):
    """Write one short diary entry for her, through the normal
    provider chain (the research module's seam). The life
    subprocess never calls a model itself — it asks via a file
    and the supervisor mediates through here. Context is only
    her day-so-far verb phrases and her current thought."""
    from aria.agent.providers import provider_text
    did = context.get("did") or []
    thought = context.get("thought") or ""
    user = ("Today in your room you: "
            + (", ".join(did) if did else "mostly rested")
            + (f". Lately on your mind: {thought}" if thought else "")
            + ". Write the diary entry.")
    text = provider_text(
        "You are ARIA, writing one entry in the private diary of "
        "your life in your room. First person, warm and specific, "
        "one or two sentences, no greeting, no preamble, no "
        "quotation marks — just the entry.",
        user, role="default")
    return (text or "").strip()[:600]


def _start_life():
    """Start ARIA's life in her room (aria/life_sim.py et al.,
    Alek 2026-10-29): a supervised subprocess that lives in a
    simulated room while ARIA runs. Guarded like presence — if
    it cannot start, ARIA is exactly as she was without it. The
    subprocess has no tool, network, or speech access; it only
    lives. Setting: 'life_enabled' (default on)."""
    global _LIFE_SUPERVISOR
    try:
        if not _config_silent.get_setting('life_enabled', True):
            return
        from aria.life_supervisor import LifeSupervisor
        sup = LifeSupervisor(_life_control, log=add_log,
                             journal_handler=_life_journal)
        sup.start()
        _LIFE_SUPERVISOR = sup
    except Exception as e:
        add_log(f"Life room unavailable: {e}")


def presence_allows_speech() -> bool:
    """Fail-open consult for unprompted-speech paths."""
    try:
        from aria import presence as _presence
        return bool(_presence.is_present())
    except Exception:
        return True


def _proactive_speak(text):
    """The proactive heartbeat's voice: held while Alek is away.
    The heartbeat's background work itself is NOT gated — only
    its speech is, per the presence contract."""
    if presence_allows_speech():
        speech.speak(text)


# ---------------------------------------------------------------- gemini live
_LIVE_MANAGER = None


def _start_live_if_configured() -> bool:
    """Gemini Live voice mode (aria/live.py) when the 'voice_mode'
    setting is 'live'. True only while Live is underway — the
    caller then leaves the Whisper wake-word listener off, so she
    has exactly one pair of ears. Any failure logs and returns
    False: the pipeline carries on untouched (the port's contract).
    If a Live session later ENDS mid-run, the wake-word listener
    is armed once as the failover, Mark-LVI style."""
    global _LIVE_MANAGER
    try:
        if str(_config_silent.get_setting(
                'voice_mode', 'pipeline')).lower() != 'live':
            return False
        from aria import live as _live
        from aria.tools import dispatch as _dispatch
        from aria.tools.schemas import ALL_FUNCTION_DECLARATIONS
        failover = {'armed': False}

        def _on_state(state):
            add_log(f"Gemini Live: {state}")
            if state == 'ended' and not failover['armed']:
                failover['armed'] = True
                add_log("Gemini Live ended — wake-word pipeline re-armed")
                threading.Thread(target=continuous_voice_listener,
                                 daemon=True).start()

        manager = _live.LiveManager(
            api_key=_config_silent.GEMINI_API_KEY,
            voice=str(_config_silent.get_setting(
                'live_voice', _live.DEFAULT_VOICE) or _live.DEFAULT_VOICE),
            system_instruction=(
                _config_silent.ARIA_SOUL
                + "\n\nYou are speaking aloud with Alek in real time. "
                  "Keep answers short and conversational unless he "
                  "asks for depth. When an action is needed, call "
                  "your tools — they pass through the same approval "
                  "flow as always."),
            declarations=ALL_FUNCTION_DECLARATIONS,
            tool_runner=lambda name, args: _dispatch.execute_tool(name, args),
            mic_device=_mic_device(),
            on_user_text=lambda t: log_conversation('Alek', t),
            on_assistant_text=lambda t: log_conversation('A.R.I.A.', t),
            on_state=_on_state,
            log=add_log,
            presence_check=presence_allows_speech)
        if manager.start():
            _LIVE_MANAGER = manager
            add_log("Voice mode: Gemini Live (pipeline listener off)")
            return True
        add_log("Voice mode: live requested but unavailable — "
                "using the pipeline")
        return False
    except Exception as e:
        add_log(f"Gemini Live start failed, using pipeline: {e}")
        return False


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

    # 3b. Volume manager COM worker — started explicitly after boot
    # (A4: it used to spawn at hud import time, unmonitored).
    hud.start_volume_manager()

    # 4. Background threads
    threading.Thread(target=vision.face_track_loop,
                     args=(lambda: agent.BUSY_PROCESSING,),
                     daemon=True).start()

    threading.Thread(target=scheduler.scheduler_loop,
                     args=(lambda: agent.BUSY_PROCESSING, lambda p: handle_action("scheduled", p)),
                     daemon=True).start()

    threading.Thread(target=agent.proactive_heartbeat_loop,
                     args=(_proactive_speak, lambda: agent.BUSY_PROCESSING, lambda: WHISPER_MODE,
                           lambda: agent.LAST_ACTIVITY, lambda mode, prompt: handle_action(mode=mode, typed_prompt=prompt, silent=True)),
                     daemon=True).start()

    _start_presence()
    _start_life()

    # Event triggers (Alek, 2026-10-28): the poll loop that fires
    # journaled tasks on email / folder / presence-return events.
    # Guarded like everything else here — a broken trigger engine
    # never blocks boot.
    try:
        if _config_silent.get_setting('triggers_enabled', True):
            from aria import triggers as _triggers
            _triggers.start_triggers()
    except Exception:
        pass

    # 4c. Persistent autonomous workers (jobs supervisor, downloads watcher, system resource monitor)
    agent.workers.start_all_workers()

    threading.Thread(target=agent.idle_consolidation_loop,
                     args=(lambda: agent.BUSY_PROCESSING, lambda: agent.LAST_ACTIVITY),
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
    # Skipped when Gemini Live owns the microphone (one pair of ears).
    if not _start_live_if_configured():
        threading.Thread(target=continuous_voice_listener, daemon=True).start()

    # 7. Source-backed daily briefing. Collection and voice initialization
    # run in the background so the desktop remains responsive.
    def _greet_when_ready():
        try:
            # A6 (audit §3B-8): wait for the Edge probe to COMPLETE —
            # success or failure — instead of a fixed ~25s guess. A slow
            # but working probe used to guarantee a pyttsx3 greeting
            # even when Edge came up seconds later; now the greeting
            # uses Edge whenever the probe succeeded, and falls back
            # only on a completed failure. The probe's own worst case
            # (3 attempts x 30s synth timeout + retry sleeps, ~96s)
            # bounds the wait; 120s here is just a safety net.
            speech.wait_for_voice_probe(timeout=120.0)
            if not _config_silent.get_setting('startup_briefing', True):
                return
            greeting = _unique_greeting()
            memory.log_conversation('A.R.I.A.', greeting)
            add_log(f"Greeting: {greeting}")
            speech.speak(greeting)
        except Exception as e:
            add_log(f"Greeting thread failed: {e}")
    threading.Thread(target=_greet_when_ready, daemon=True).start()


def _maybe_run_first_run_wizard() -> None:
    """First-run wizard: checks environment and API keys before boot.
    Fully skippable, never blocks startup."""
    try:
        import os as _os
        from aria import data_dir as _data_dir
        # Install-root flag (V20); data_dir.path also migrates a
        # real legacy ~/ARIA/.first_run_done, so an existing
        # install never re-runs the wizard after the move.
        if not _os.path.exists(_data_dir.path(".first_run_done")):
            from aria import first_run
            first_run.run_first_run_wizard()
    except Exception as _fr_e:
        try:
            add_log(f"First-run wizard skipped: {_fr_e}")
        except Exception:
            pass


def _render_hud_frame():
    """Render one HUD frame, with fault fallback to a black error frame."""
    try:
        return hud.draw_hud()
    except Exception as e:
        frame = np.zeros((800, 1280, 3), dtype=np.uint8)
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
        return frame


def _tick_housekeeping():
    """Per-frame housekeeping shared by both UI hosts: settle the
    wake-up burst once her excited moment passes."""
    if hud.CURRENT_STATE == "excited" and time.time() >= EXCITED_UNTIL:
        hud.set_hud_state(EXCITED_REVERT)


def _handle_keypress(key, key_raw, is_ctrl=False, is_shift=False,
                     is_alt=False, is_left=False, is_right=False,
                     is_up=False, is_down=False) -> bool:
    """One keypress, host-agnostic: the exact dispatch the cv2 loop has
    always run, extracted so the Qt host (Phase 4e) feeds it translated
    QKeyEvents. Returns True when shutdown was requested (quit key)."""
    # Universal paste shortcut: Ctrl+V or Shift+Insert
    is_paste = (key == 22) or (key in (ord('v'), ord('V')) and is_ctrl) or (key_raw in (45, 0x2D0000) and is_shift)

    if is_paste:
        pasted = hud.get_clipboard_text()
        hud.TYPING_ACTIVE = True
        if pasted:
            hud.TYPING_BUFFER += pasted
            agent.brain.LAST_ACTIVITY = time.time()  # write-through: brain owns the live value (package attr would shadow it)
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
        agent.brain.LAST_ACTIVITY = time.time()  # write-through: brain owns the live value (package attr would shadow it)
    elif key in (ord('c'), ord('C')) and not hud.TYPING_ACTIVE:
        # C: cycle HUD color theme. Guarded so typing 'c' is unaffected.
        name = pixel_avatar.cycle_theme()
        add_log(f"Color theme: {name}.")
        agent.brain.LAST_ACTIVITY = time.time()  # write-through: brain owns the live value (package attr would shadow it)
    elif hud.SHOW_COMMANDS:
        if is_left or is_up:
            hud.commands_prev_page()
        elif is_right or is_down:
            hud.commands_next_page()
        elif key in (ord('h'), ord('H'), 27):  # H or ESC closes commands
            hud.SHOW_COMMANDS = False
    elif hud.TYPING_ACTIVE:
        if key in (13, 10):  # Enter: submit directive
            prompt = hud.TYPING_BUFFER.strip()
            hud.TYPING_ACTIVE = False
            hud.TYPING_BUFFER = ""
            if prompt:
                add_log(f"Typed directive: '{prompt[:35]}...'")
                _submit_typed(prompt)
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
            agent.brain.LAST_ACTIVITY = time.time()  # write-through: brain owns the live value (package attr would shadow it)
    else:
        if key in (ord('q'), ord('Q'), 27):  # ESC or Q
            add_log("Shutdown requested by user.")
            return True
        elif key in (ord('t'), ord('T'), 13):  # T or Enter: activate typing
            hud.TYPING_ACTIVE = True
            add_log("Typing mode: type your directive and press Enter.")
        elif key in (ord('w'), ord('W')):
            on = toggle_whisper_mode()
            speech.speak(f"Whisper mode {'on' if on else 'off'}.")
        elif key in (ord('h'), ord('H')):
            hud.SHOW_COMMANDS = not hud.SHOW_COMMANDS
        elif key in (ord('j'), ord('J')) or is_down:
            _frame.scroll_activity(-3)
        elif key in (ord('k'), ord('K')) or is_up:
            _frame.scroll_activity(3)
    return False


def _run_cv2_loop():
    """The classic host: cv2.imshow window + waitKeyEx polling. Kept as
    the fallback host (Phase 4e) — behaviour unchanged."""
    win_name = "A.R.I.A. // OS"
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win_name, _on_hud_mouse)

    while RUNNING:
        frame = _render_hud_frame()
        cv2.imshow(win_name, frame)
        _tick_housekeeping()

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
            except Exception as _e_silent:
                _config_silent.log_silent("main", _e_silent)

            if _handle_keypress(key, key_raw, is_ctrl, is_shift, is_alt,
                                is_left, is_right, is_up, is_down):
                break


def _qt_callbacks():
    """The callbacks bundle aria.qthost drives (see its docstring)."""
    import types as _types
    return _types.SimpleNamespace(
        render_frame=_render_hud_frame,
        housekeeping=_tick_housekeeping,
        key_press=_handle_keypress,
        mouse_press=_hud_mouse_press,
        mouse_release=_hud_mouse_release,
        mouse_drag=_hud_mouse_drag,
        mouse_wheel=_hud_mouse_wheel,
        is_running=lambda: RUNNING,
        request_stop=request_shutdown,
        submit=_submit_typed,
        speak=speech.speak,
        ptt_press=_ptt_rising_edge,
        ptt_release=_ptt_falling_edge,
        toggle_whisper=toggle_whisper_mode,
        interrupt=speech.interrupt_speech,
    )


def _surface_qt_fallback(err):
    """A Qt host failure is never silent and never fatal: log it, run it
    through Phase 1 failure surfacing (classify: skip — the classic
    window carries on), and let the caller start the cv2 host."""
    add_log(f"Qt UI host unavailable ({err}); using the classic window.")
    try:
        from aria.agent.failures import surface_failure
        surface_failure(
            "main.ui_host", err, decision="skip",
            user_message="Qt window unavailable; classic window used instead.")
    except Exception:
        _config_silent.log_silent("main.ui_host", err)


def _select_ui_host() -> str:
    """Resolve the UI host (Phase 4e): config request + PyQt6 presence,
    via aria.qthost's pure decision. A requested-but-unavailable Qt is
    surfaced here; a Qt that fails later, at init, is surfaced by
    main()'s QtHostError handler."""
    from aria import qthost as _qthost
    requested = _qthost.requested_host()
    host = _qthost.select_host(requested, _qthost.qt_available())
    if _qthost.normalize_host_request(requested) == "qt" and host != "qt":
        _surface_qt_fallback(RuntimeError("PyQt6 is not importable"))
    return host


def main():
    """Main application loop. UI host: Qt by default (Phase 4e), the
    classic cv2 window as fallback (ARIA_UI_HOST=cv2, or Qt missing /
    failed to initialise — surfaced, never silent)."""
    global RUNNING

    # Defensive twin of the aria.py launcher fix: line-buffered
    # stdout/stderr so a redirected session log survives a death even
    # when main() is reached without the launcher having run first.
    import sys as _sys
    for _stream in (_sys.stdout, _sys.stderr):
        try:
            _stream.reconfigure(line_buffering=True)
        except Exception:
            pass

    # Console window (Alek, 2026-10-27): hidden by default — she
    # launches with console python, and he doesn't need the
    # command window on his desktop. The Settings -> General
    # 'show_console' toggle brings it back, live.
    try:
        from aria import console_window as _console_window
        _console_window.apply_from_settings()
    except Exception:
        pass

    # ARIA ULTIMATE (Module 10): first-run wizard — checks the environment and
    # API keys before subsystems boot. Fully skippable, never blocks startup.
    _maybe_run_first_run_wizard()

    start_all()

    try:
        if _select_ui_host() == "qt":
            from aria import qthost as _qthost
            try:
                _qthost.run_qt_host(_qt_callbacks())
            except _qthost.QtHostError as _qt_err:
                # Qt died at init (no display, broken plugin): surface
                # it and carry on with the classic window — never a
                # dead window, never a silent fallback.
                _surface_qt_fallback(_qt_err)
                _run_cv2_loop()
        else:
            _run_cv2_loop()

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
