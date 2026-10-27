"""A.R.I.A. Speech & Audio Subsystem.

Edge TTS pipelined playback with pyttsx3 fallback, sentence streaming,
faster-whisper and Google cloud STT, and speech interrupt control.
"""

from __future__ import annotations

import io
import os
import queue
import re
import sys
import threading
import time
from typing import Callable, Dict, Optional, Tuple, List

import audioop
import numpy as np
import speech_recognition as sr

from aria.config import WORKSPACE_DIR
from aria import config as _config_silent

# The pipeline's Edge voice: AriaNeural — her verified voice on the
# laptop (standalone synthesis witness, 2026-10-20: "It sounded like
# her"). A 2026-10-27 experiment made MichelleNeural (Kore's closest
# Edge stand-in for the Gemini Live mode in aria/live.py) the pipeline
# default; Alek's witness the same morning was "still using fallback
# voice — it doesn't sound like ARIA," so the default is AriaNeural
# again and Michelle is only a rung on the ladder below. The
# 'tts_voice' setting still overrides this per install; her Live
# voice (Kore) lives in aria/live.py and is unaffected.
EDGE_TTS_VOICE = "en-US-AriaNeural"

# If the configured voice cannot synthesize (a retired/renamed voice,
# a bad saved setting, a Microsoft-side change), the probe walks this
# ladder instead of giving up on Edge entirely — one bad voice name
# must never mean a whole session on the SAPI fallback.
EDGE_VOICE_LADDER = ("en-US-AriaNeural", "en-US-JennyNeural",
                     "en-US-MichelleNeural")
EDGE_PROBE_RETRY_SLEEP_S = 3.0

_SESSION_VOICE: Optional[str] = None   # set by the probe on success


def _configured_edge_voice() -> str:
    try:
        v = _config_silent.get_setting("tts_voice", EDGE_TTS_VOICE)
    except Exception:
        v = None
    return v.strip() if isinstance(v, str) and v.strip() else EDGE_TTS_VOICE


def edge_voice_candidates() -> List[str]:
    """Probe order: the configured voice first (the user's Settings
    choice wins), then the ladder, duplicates removed."""
    out: List[str] = []
    for v in (_configured_edge_voice(),) + EDGE_VOICE_LADDER:
        if v not in out:
            out.append(v)
    return out


def active_edge_voice() -> str:
    """The voice synthesis actually uses: the probe-proven session
    voice once one exists, else the configured voice."""
    return _SESSION_VOICE or _configured_edge_voice()


def _set_session_voice(voice: Optional[str]) -> None:
    global _SESSION_VOICE
    _SESSION_VOICE = voice
WHISPER_EDGE_KWARGS = {"rate": "-10%", "pitch": "-8Hz", "volume": "-60%"}

# A2: Edge availability supervision — re-probe cadence while Edge is
# down, and how many consecutive per-sentence synthesis failures revoke
# readiness mid-session.
EDGE_REPROBE_INTERVAL_S = 60.0
EDGE_FAIL_STREAK_LIMIT = 3

STOP_WORDS = {
    "stop", "quiet", "shut up", "enough", "silence",
    "stop talking", "hush", "be quiet", "cut it out",
}

def extract_sentences(text: str, first_clause: bool = False):
    """Extract complete sentences from streaming text buffer without splitting decimals or abbreviations.
    If first_clause is True, allows emitting a leading clause on comma/dash/colon when >= 3 words to cut initial voice latency.
    """
    tokens = re.split(r'(\b(?:Mr|Mrs|Ms|Dr|Prof|vs|etc|e\.g|i\.e)\.|\d+\.\d+|[.!?]+(?:\s+|$)|[\n]+)', text)
    if first_clause and len(tokens) == 1 and not re.search(r'[.!?\n]', text):
        c_tokens = re.split(r'([,:;—–]+(?:\s+))', text)
        if len(c_tokens) > 1:
            first_part = c_tokens[0] + c_tokens[1]
            words = first_part.strip().split()
            if len(words) >= 3:
                rem = "".join(c_tokens[2:])
                return [first_part.strip()], rem
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

_CODEY_RE = re.compile(
    r"https?://|www\.|```|\.(py|txt|json|md|bat|js|html)\b|[A-Za-z]:\\|/[\w\-.]+/[\w\-.]+"
)


def whisper_shorten(text, max_sentences=2, hard_cap=240):
    """Cap a reply to its shortest complete form for whisper mode."""
    t = (text or "").strip()
    if not t:
        return t
    if _CODEY_RE.search(t):
        return (t[:hard_cap].rstrip() + " …[shortened]"
                if len(t) > hard_cap else t)
    sents, _rem = extract_sentences(t)
    if not sents:
        return (t[:hard_cap].rstrip() + " …[shortened]"
                if len(t) > hard_cap else t)
    short = " ".join(sents[:max_sentences]).strip()
    return short if short else t

# ---------------- Fallback TTS (COM host) ----------------

def _get_sapi_client():
    """The shared COM-host client (aria.comclient singleton). A
    function, not a module-level binding, so tests can substitute a
    fake client by patching this one name."""
    from aria.comclient import get_client
    return get_client()


class _SapiVoice:
    """Desktop SAPI fallback voice — now a facade over the COM host
    process (crash round 5, aria/comhost.py).

    History: this class used to own the pyttsx3 engine on a
    dedicated in-process COM thread. Four faulthandler dumps proved
    any in-process COM is a process-killer here — pyttsx3.init()
    generates its SpeechLib type library as cyclic comtypes
    wrappers, and the cyclic GC finalised them on foreign threads
    (Release() access violation) — so the engine, its CoInitialize,
    lazy init and iterate() pump moved into the helper process
    unchanged. What remains is the interface SpeechManager always
    used: say() returns an Event that the helper's async
    "sapi.finished" event sets; if the host is down, the Event is
    set immediately and the failure logged, exactly as an
    engineless say always behaved.
    """

    def __init__(self, on_log: Optional[Callable[[str], None]] = None):
        self.on_log = on_log
        self._start_lock = threading.Lock()
        self._started = False
        self._pending: Dict[int, threading.Event] = {}
        self._pending_lock = threading.Lock()

    def _log(self, msg: str):
        if self.on_log:
            self.on_log(msg)

    def start(self):
        """Register with the COM host client (idempotent)."""
        with self._start_lock:
            if self._started:
                return
            self._started = True
        try:
            client = _get_sapi_client()
            client.add_event_listener(self._on_host_event)
            client.add_down_listener(self._on_host_down)
        except Exception as e:
            self._log(f"Voice fallback unavailable: {e}")

    def say(self, text: str, whisper_mode: bool = False) -> threading.Event:
        """Queue an utterance in the COM host; returns an Event set
        when it completes (immediately, if the host is down)."""
        self.start()
        done = threading.Event()
        # Hold the pending lock across send+register so a fast
        # finished event can never slip between them.
        with self._pending_lock:
            try:
                client = _get_sapi_client()
                rid = client.send(
                    "sapi.say",
                    {"text": text, "whisper": bool(whisper_mode)})
            except Exception as e:
                self._log(f"Voice fallback unavailable: {e}")
                done.set()
                return done
            self._pending[rid] = done
        return done

    def stop(self):
        """Interrupt the current utterance (handled in the COM host)."""
        self.start()
        try:
            _get_sapi_client().send("sapi.stop")
        except Exception:
            pass

    # -- comhost client callbacks ------------------------------------

    def _on_host_event(self, msg):
        if msg.get("event") != "sapi.finished":
            return
        with self._pending_lock:
            done = self._pending.pop(msg.get("id"), None)
        if done is not None:
            if not msg.get("ok", True):
                self._log("Voice fallback utterance failed in comhost: "
                          f"{msg.get('error', 'unknown error')}")
            done.set()

    def _on_host_down(self):
        # The helper died: every in-flight utterance is lost with
        # it. Release the waiters rather than leaving them hanging
        # (SpeechManager also has its own timeouts as backstop).
        with self._pending_lock:
            pending = self._pending
            self._pending = {}
        if pending:
            self._log("Voice fallback host exited; "
                      f"{len(pending)} utterance(s) dropped.")
        for done in pending.values():
            done.set()


# ---------------- Mic capture (sounddevice) ----------------

class _SoundDeviceStream:
    """Blocking byte stream over a sounddevice InputStream, exposing the
    PyAudio-style read() interface sr.Recognizer expects."""

    def __init__(self, stream):
        self._stream = stream

    def read(self, size):
        data, _overflowed = self._stream.read(size)
        return data.tobytes()

    def close(self):
        try:
            self._stream.stop()
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass


# sr.AudioSource is the base class Recognizer.listen() type-checks its
# source against. Resolve it defensively: minimal stub environments
# (the headless test harness) provide a bare speech_recognition module
# without it, and a hard AttributeError here would break the import.
_SRAudioSource = getattr(sr, "AudioSource", object)


class SoundDeviceSource(_SRAudioSource):
    """Microphone capture via sounddevice (PortAudio) as an sr.AudioSource.

    Drop-in replacement for sr.Microphone, which is backed by PyAudio —
    unmaintained upstream and without wheels for newer Pythons (the
    3.14 install failure that started the Fork A audit). sounddevice
    ships an ABI-independent wheel with PortAudio bundled. Capture is
    16 kHz mono int16, blocksize 1024 (the constants Mark-XXXIX-OR uses
    for the same job). Everything downstream — Recognizer.listen,
    adjust_for_ambient_noise, AudioData — sees the identical interface.
    """

    def __init__(self, sample_rate: int = 16000, chunk: int = 1024, device=None):
        self.SAMPLE_RATE = sample_rate
        self.SAMPLE_WIDTH = 2  # int16
        self.CHANNELS = 1
        self.CHUNK = chunk
        self._device = device
        self.stream = None

    def __enter__(self):
        import sounddevice as sd
        raw = sd.InputStream(
            samplerate=self.SAMPLE_RATE, channels=self.CHANNELS,
            dtype="int16", blocksize=self.CHUNK, device=self._device)
        raw.start()
        self.stream = _SoundDeviceStream(raw)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


# ---------------- Local STT (faster-whisper) ----------------

_FW_AVAILABLE = False
_FW_MODEL = None
_FW_LOCK = threading.Lock()
_FW_NO_SPEECH_CUTOFF = 0.45

try:
    from faster_whisper import WhisperModel as _FwWhisperModel
    _FW_AVAILABLE = True
except Exception:
    _FwWhisperModel = None
    _FW_AVAILABLE = False


def fw_is_available() -> bool:
    return _FW_AVAILABLE


def get_fw_model(workspace_dir: str = WORKSPACE_DIR, on_log: Optional[Callable[[str], None]] = None):
    """Lazy singleton for local WhisperModel."""
    global _FW_MODEL
    if not _FW_AVAILABLE:
        return None
    with _FW_LOCK:
        if _FW_MODEL is None:
            try:
                models_dir = os.path.join(workspace_dir, "models")
                os.makedirs(models_dir, exist_ok=True)
                _FW_MODEL = _FwWhisperModel(
                    "base", device="cpu", compute_type="int8",
                    download_root=models_dir
                )
            except Exception as e:
                if on_log:
                    on_log(f"Local STT unavailable: {e}")
                return None
        return _FW_MODEL


def _surface_fw_pcm(exc):
    """Merger Phase 1 (blueprint SS3.2): a PCM conversion failure is a
    classified SKIP in the failure record — transcription continues on
    the unconverted audio — not a silent-log line. Throttled inside
    surface_failure (this runs per utterance)."""
    try:
        from aria.agent.failures import surface_failure
        surface_failure("speech.fw_pcm", exc, decision="skip",
                        user_message="Audio conversion hiccup; transcription continued anyway.")
    except Exception:
        _config_silent.log_silent("fw_pcm", exc)


def fw_pcm(audio: sr.AudioData) -> np.ndarray:
    """sr.AudioData -> float32 mono PCM at 16000Hz in [-1, 1] for faster-whisper."""
    raw = audio.get_raw_data()
    sample_rate = audio.sample_rate
    sample_width = audio.sample_width

    if sample_width != 2:
        try:
            raw = audioop.lin2lin(raw, sample_width, 2)
        except Exception as _e_silent:
            _surface_fw_pcm(_e_silent)

    if sample_rate != 16000 and sample_rate > 0:
        try:
            raw, _ = audioop.ratecv(raw, 2, 1, sample_rate, 16000, None)
        except Exception as _e_silent:
            _surface_fw_pcm(_e_silent)

    return (np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0)


def ptt_join_audio(frames_list: List[bytes], sample_rate: int, sample_width: int) -> Tuple[sr.AudioData, float]:
    """Concatenate raw PCM chunks -> (sr.AudioData, seconds)."""
    frames = b"".join(frames_list)
    total_s = len(frames) / float(sample_rate * sample_width)
    return sr.AudioData(frames, sample_rate, sample_width), total_s


_STT_HALLUCINATIONS = [
    r"^(?:thank\s+you(?:\s+very\s+much)?|thanks(?:\s+for\s+watching)?)[.!]?$",
    r"^please\s+subscribe[.!]?$",
    r"^subtitles\s+by.*$",
    r"^(?:bye|goodbye|see\s+you(?:\s+later|\s+next\s+time)?)[.!]?$",
    r"^(?:so,\s+yeah|you'?re\s+welcome)[.!]?$",
    r"^searching\s+spotify.*$",
    r"^(?:boom|in\s+the\s+1970s)[.!]?$",
    r"^(?:silence|coughing|throat\s+clearing|laughter)[.!]?$",
]


def clean_stt_transcript(text: str) -> str:
    """Filter out common Whisper silence hallucinations and repetition loops."""
    if not text:
        return ""
    t = text.strip()
    low = t.lower().strip(" ,.-!?:;—–\t\n")
    if not low:
        return ""
    for pat in _STT_HALLUCINATIONS:
        if re.match(pat, low):
            return ""
    words = low.split()
    if len(words) >= 2 and len(set(words)) == 1:
        return ""
    return t


_GOOGLE_STT_TIMEOUT_S = 10.0


def _recognize_google(recognizer: sr.Recognizer, audio: sr.AudioData,
                      timeout: float = _GOOGLE_STT_TIMEOUT_S) -> str:
    """recognize_google with a hard timeout. The library call takes no
    timeout argument, so a hung request would stall the wake listener
    indefinitely (audit §1C). The call runs on a daemon thread; on
    timeout the thread is abandoned (it dies with the process)."""
    result: List[str] = []
    error: List[Exception] = []

    def _call():
        try:
            result.append(recognizer.recognize_google(audio))
        except Exception as e:
            error.append(e)

    t = threading.Thread(target=_call, daemon=True, name="aria-google-stt")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"Google STT timed out after {timeout:.0f}s")
    if error:
        raise error[0]
    return result[0] if result else ""


def transcribe(audio: sr.AudioData, recognizer: sr.Recognizer, use_local_stt: bool = False, on_log: Optional[Callable[[str], None]] = None) -> str:
    """Transcribe sr.AudioData to text. Prefers faster-whisper with strict VAD filtering, falls back to Google on STT failure."""
    if not use_local_stt:
        try:
            return clean_stt_transcript(_recognize_google(recognizer, audio))
        except Exception:
            return ""

    model = get_fw_model(on_log=on_log)
    if model is not None:
        try:
            pcm = fw_pcm(audio)
            segments, _ = model.transcribe(
                pcm, language="en",
                initial_prompt="Aria.",
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400, threshold=0.5),
                condition_on_previous_text=False
            )
            kept = [s for s in segments if getattr(s, "no_speech_prob", 0.0) <= _FW_NO_SPEECH_CUTOFF]
            text = " ".join(s.text.strip() for s in kept).strip()
            return clean_stt_transcript(text)
        except Exception as e:
            if on_log:
                on_log(f"Local STT failed, falling back to Google: {e}")

    try:
        return clean_stt_transcript(_recognize_google(recognizer, audio))
    except Exception:
        return ""


def _ensure_mixer(on_log: Optional[Callable[[str], None]] = None) -> bool:
    """Make sure pygame's mixer is initialized for playback; log and
    return False when the audio device won't init. Called per
    utterance from the play worker — a device that was asleep at
    boot can still come back mid-session, which the old boot-time
    probe gate made impossible."""
    try:
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        return bool(pygame.mixer.get_init())
    except Exception as e:
        if on_log:
            on_log(f"Voice playback: mixer init failed: {e}")
        return False


# ---------------- SpeechManager ----------------

class SpeechManager:
    """Coordinates Edge TTS synthesis, pyttsx3 fallback, playback queues, and interrupt."""

    def __init__(self,
                 workspace_dir: str = WORKSPACE_DIR,
                 voice: str = EDGE_TTS_VOICE,
                 on_subtitle: Optional[Callable[[str], None]] = None,
                 on_state: Optional[Callable[[str], None]] = None,
                 on_log: Optional[Callable[[str], None]] = None,
                 on_conversation: Optional[Callable[[str, str], None]] = None):
        self.workspace_dir = workspace_dir
        self.voice = voice
        self.on_subtitle = on_subtitle
        self.on_state = on_state
        self.on_log = on_log
        self.on_conversation = on_conversation

        self.speech_queue = queue.Queue()
        self.audio_play_queue = queue.Queue(maxsize=6)
        self.speech_stop = threading.Event()
        self.edge_ready = False
        self.whisper_mode = False

        # A2: Edge availability is supervised, not latched (see
        # _edge_supervisor). edge_probe_done fires when the FIRST probe
        # round completes — the startup greeting waits on it (A6).
        self.edge_probe_done = threading.Event()
        self._edge_revoked = threading.Event()
        self._probe_lock = threading.Lock()
        self._edge_fail_streak = 0
        self._edge_fail_lock = threading.Lock()

        # A3: the pyttsx3 engine lives on its own COM-initialised
        # thread inside _SapiVoice; nothing else touches it directly.
        self._sapi = _SapiVoice(on_log=on_log)
        self._voice_rotation_idx = 0
        self._voice_rotation_lock = threading.Lock()
        self._started = False

    def log(self, msg: str):
        if self.on_log:
            self.on_log(msg)

    def init_tts(self):
        """Ensure the SAPI engine thread is running (idempotent)."""
        self._sapi.start()

    def _whisper_edge_kwargs(self) -> dict:
        return dict(WHISPER_EDGE_KWARGS) if self.whisper_mode else {}

    def _probe_edge_once(self) -> bool:
        """Run one Edge probe round; log the outcome; return success.

        A1 (audit §1A #2): the probe must say WHY Edge is unavailable.
        Step 0 actually imports edge_tts — the previous body was
        `try: pass`, which made the "(edge-tts import broken)" branch
        unreachable and a broken install indistinguishable from a
        network failure. Synthesis goes through the STRICT path so the
        real error survives: edge_tts_bytes() swallows every failure
        into b"" — probed through it, the retry loop could never see an
        exception, its sleep(3) was dead code, and the actual reason
        (e.g. Microsoft's HTTP 403 to pre-Sec-MS-GEC clients) was
        discarded. Serialised: the supervisor is the only caller, but
        the lock keeps that true if that ever changes.
        """
        with self._probe_lock:
            try:
                import edge_tts  # noqa: F401
            except Exception as e:
                self.log(f"Voice: system fallback (edge-tts import broken: {e})")
                return False
            _etts_ver = str(getattr(edge_tts, "__version__", "") or "unknown")
            try:
                _etts_tuple = tuple(int(p) for p in _etts_ver.split(".")[:3]
                                      if p.isdigit())
            except Exception:
                _etts_tuple = ()

            # NOTE: the mixer is deliberately NOT probed here. It
            # used to be step 2 of this probe, and a mixer-init
            # failure (audio device asleep/absent at boot) failed
            # the WHOLE probe before a single synthesis was tried —
            # Edge was declared dead for a playback-side reason and
            # the session latched onto SAPI (the 2026-10-27
            # "still fallback" witness, where standalone synthesis
            # on the same laptop was known-good). Synthesis is the
            # only question this probe answers now; playback ensures
            # the mixer per utterance (_ensure_mixer) and falls back
            # per utterance, never per session.

            # Walk the voice ladder: the configured voice gets the
            # historical 3 attempts; each ladder voice gets one. A
            # voice-name failure (retired voice, bad saved setting)
            # fails fast and deterministically, so the ladder costs
            # almost nothing when the configured voice is healthy —
            # and a single bad name can no longer latch the whole
            # session onto SAPI (2026-10-27 witness: "still using
            # fallback voice — it's not using the edge voice").
            configured = _configured_edge_voice()
            failures: List[Tuple[str, Optional[Exception]]] = []
            for idx, voice in enumerate(edge_voice_candidates()):
                attempts = 3 if idx == 0 else 1
                last_err: Optional[Exception] = None
                for attempt in range(1, attempts + 1):
                    try:
                        if _edge_tts_bytes_strict("Voice check.",
                                                   voice=voice):
                            self.edge_ready = True
                            _set_session_voice(voice)
                            with self._edge_fail_lock:
                                self._edge_fail_streak = 0
                            if voice == configured:
                                self.log(f"Voice: Edge TTS ({voice}, "
                                         f"edge-tts {_etts_ver})")
                            else:
                                self.log(
                                    f"Voice: Edge TTS ({voice}) — "
                                    f"configured voice {configured} "
                                    f"failed: {failures[0][1]}")
                            return True
                    except Exception as e:
                        last_err = e
                        if attempt < attempts:
                            time.sleep(EDGE_PROBE_RETRY_SLEEP_S)
                failures.append((voice, last_err))

            detail = "; ".join(f"{v}: {e}" for v, e in failures)
            hint = ""
            if _etts_tuple and _etts_tuple < (7, 3):
                # Microsoft retired the v1 Sec-MS-GEC token in
                # Oct 2026 (edge-tts issue #490): pre-7.3 clients
                # 403 on EVERY handshake, whatever the voice or the
                # clock. Alek's 2026-10-27 startup line showed the
                # v1 token (64 hex chars, version 1-143) while
                # requirements.txt already pins 7.3.1 — the install
                # had not reached the interpreter ARIA runs on.
                # Name the way out in the log itself.
                hint = (f" — edge-tts {_etts_ver} predates Microsoft's "
                        "v2 token format; fix: py -3.12 -m pip "
                        "install -U edge-tts, then restart")
            self.log(f"Voice: system fallback (Edge synthesis failed "
                     f"for every voice — {detail}){hint}")
            return False

    def _edge_supervisor(self):
        """Own Edge availability for the whole session (A2: de-latch).

        Runs the first probe round (setting edge_probe_done for the
        greeting), then keeps watching: while Edge is down it re-probes
        every EDGE_REPROBE_INTERVAL_S, and a mid-session revocation
        (repeated synthesis failures) wakes it immediately. Before A2
        the probe ran exactly once at boot and edge_ready was never
        re-checked — one boot-time network blip meant pyttsx3 for the
        entire session (audit §1A #3).
        """
        try:
            self._probe_edge_once()
        finally:
            self.edge_probe_done.set()
        while True:
            self._edge_revoked.wait(timeout=EDGE_REPROBE_INTERVAL_S)
            self._edge_revoked.clear()
            if not self.edge_ready:
                self._probe_edge_once()

    def _note_edge_result(self, ok: bool):
        """Track per-sentence Edge synthesis results from the synth
        worker. EDGE_FAIL_STREAK_LIMIT consecutive failures revoke
        edge_ready (utterances route to pyttsx3) and wake the
        supervisor to re-probe; any success resets the streak."""
        with self._edge_fail_lock:
            if ok:
                self._edge_fail_streak = 0
                return
            self._edge_fail_streak += 1
            streak = self._edge_fail_streak
        if not ok and streak >= EDGE_FAIL_STREAK_LIMIT and self.edge_ready:
            self.edge_ready = False
            self.log(f"Voice: Edge TTS lost ({streak} consecutive synthesis "
                     "failures) — system fallback until re-probe succeeds")
            self._edge_revoked.set()

    def _synth_worker(self):
        while True:
            text = self.speech_queue.get()
            if self.speech_stop.is_set():
                self.speech_queue.task_done()
                continue
            if not self.edge_ready:
                self.audio_play_queue.put((text, None, False))
                self.speech_queue.task_done()
                continue

            audio_buf = None
            success = False
            try:
                raw_bytes = edge_tts_bytes(text, whisper_mode=self.whisper_mode)
                if raw_bytes:
                    audio_buf = io.BytesIO(raw_bytes)
                    success = True
            except Exception as e:
                self.log(f"Edge synth err: {e}")
            self._note_edge_result(success)

            if not self.speech_stop.is_set():
                self.audio_play_queue.put((text, audio_buf, success))
            self.speech_queue.task_done()

    def _play_worker(self):
        while True:
            item = self.audio_play_queue.get()
            text, audio_buf, is_edge = item
            if self.speech_stop.is_set():
                self.audio_play_queue.task_done()
                continue
            try:
                if self.on_subtitle:
                    self.on_subtitle(text)
                if self.on_state:
                    self.on_state("speaking")

                use_edge = bool(is_edge and audio_buf)
                if use_edge and not _ensure_mixer(self.log):
                    # Synthesis produced Edge audio but the playback
                    # device won't init: speak THIS utterance via the
                    # SAPI path below instead of dropping it, and let
                    # the next utterance try Edge again.
                    use_edge = False
                if use_edge:
                    import pygame
                    try:
                        if hasattr(audio_buf, "seek"):
                            audio_buf.seek(0)
                        from aria import audio_levels
                        levels = audio_levels.mp3_envelope(audio_buf.getvalue(), pygame)
                        # Viseme schedule from the same audio
                        # (Mark-LVI's formant read): the holo head
                        # articulates real mouth shapes from it
                        # instead of flapping on loudness. Empty
                        # when undecodable — the envelope path below
                        # is the fallback, unchanged.
                        frames = audio_levels.mp3_visemes(audio_buf.getvalue(), pygame)
                        pygame.mixer.music.load(audio_buf)
                        pygame.mixer.music.play()
                        playback_started = time.monotonic()
                        deadline = time.time() + max(10, len(text) * 0.15)
                        while pygame.mixer.music.get_busy() and time.time() < deadline and not self.speech_stop.is_set():
                            if frames:
                                index = int((time.monotonic() - playback_started) / .02)
                                if index < len(frames):
                                    audio_levels.publish_viseme(*frames[index])
                                else:
                                    audio_levels.publish(0.0)
                                time.sleep(0.02)
                            else:
                                index = int((time.monotonic() - playback_started) / .04)
                                audio_levels.publish(levels[index] if index < len(levels) else 0.0)
                                time.sleep(0.04)
                    finally:
                        from aria import audio_levels
                        audio_levels.publish(0.0)
                        try:
                            pygame.mixer.music.unload()
                        except Exception as _e_silent:
                            _config_silent.log_silent("_play_worker", _e_silent)
                else:
                    # A3: SAPI playback is marshalled to the engine's
                    # owner thread; block here until it completes (the
                    # pacing the direct runAndWait() used to provide),
                    # bailing promptly if an interrupt lands.
                    self.init_tts()
                    done = self._sapi.say(text, whisper_mode=self.whisper_mode)
                    while not done.wait(0.05):
                        if self.speech_stop.is_set():
                            self._sapi.stop()
                            break
            except Exception as e:
                self.log(f"Speech play err: {e}")
            finally:
                if self.on_state:
                    self.on_state("idle")
                self.audio_play_queue.task_done()

    def start(self):
        """Start voice initialization and background worker threads."""
        if self._started:
            return
        self._started = True
        self.init_tts()

        threading.Thread(target=self._edge_supervisor, daemon=True, name="aria-voice-init").start()

        def _supervise(target, name):
            while True:
                t = threading.Thread(target=target, daemon=True, name=name)
                t.start()
                t.join()
                self.log(f"{name} exited — restarting")
                time.sleep(1)

        threading.Thread(target=_supervise, args=(self._synth_worker, "aria-speech-synth"), daemon=True).start()
        threading.Thread(target=_supervise, args=(self._play_worker, "aria-speech-play"), daemon=True).start()

    def speak(self, text: str, whisper_mode: Optional[bool] = None):
        """Queue text to be spoken."""
        if whisper_mode is not None:
            self.whisper_mode = whisper_mode
        if self.whisper_mode:
            text = whisper_shorten(text)
        self.log(f"Speech: {text[:28]}...")
        if self.on_conversation:
            self.on_conversation("A.R.I.A.", text)
        self.speech_stop.clear()
        self.speech_queue.put(text)

    def queue_stream_chunk(self, chunk: str):
        """Directly queue a streaming text sentence."""
        if not self.speech_stop.is_set():
            self.speech_queue.put(chunk)

    def interrupt(self) -> int:
        """Interrupt and flush all queued speech immediately."""
        self.speech_stop.set()
        drained = 0
        while True:
            try:
                self.speech_queue.get_nowait()
                self.speech_queue.task_done()
                drained += 1
            except queue.Empty:
                break
        while True:
            try:
                self.audio_play_queue.get_nowait()
                self.audio_play_queue.task_done()
                drained += 1
            except queue.Empty:
                break
        try:
            import pygame
            pygame.mixer.music.stop()
        except Exception as _e_silent:
            _config_silent.log_silent("interrupt", _e_silent)
        try:
            self._sapi.stop()
        except Exception as _e_silent:
            _config_silent.log_silent("interrupt", _e_silent)
        self.log(f"Speech interrupted ({drained} queued cleared)")
        return drained

    def is_speaking(self) -> bool:
        """Check if speech audio is currently playing or queued."""
        if not self.speech_queue.empty() or not self.audio_play_queue.empty():
            return True
        try:
            import pygame
            if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                return True
        except Exception as _e_silent:
            _config_silent.log_silent("is_speaking", _e_silent)
        return False

    def wait_until_done(self, timeout: float = 6.0):
        """Block until speech queue and playback finish."""
        deadline = time.time() + timeout
        while self.is_speaking() and time.time() < deadline:
            time.sleep(0.05)


def _edge_tts_bytes_strict(text: str, whisper_mode: bool = False,
                           voice: Optional[str] = None) -> bytes:
    """Edge TTS synthesis that raises on failure (ImportError, network,
    timeout). Voice: the probe-proven session voice by default (see
    active_edge_voice) — NOT re-read from settings per sentence, so a
    configured voice the probe already proved broken is never retried
    sentence by sentence."""
    import asyncio
    import edge_tts  # raises ImportError when the package is missing

    kwargs = WHISPER_EDGE_KWARGS if whisper_mode else {}
    voice = voice or active_edge_voice()

    async def _gen():
        chunks = []
        async for chunk in edge_tts.Communicate(text, voice, **kwargs).stream():
            if chunk.get("type") == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    data = asyncio.run(asyncio.wait_for(_gen(), timeout=max(30, int(len(text) * 0.1))))
    if not data:
        raise RuntimeError("edge-tts returned empty audio")
    return data


def edge_tts_bytes(text: str, whisper_mode: bool = False) -> bytes:
    """Synthesize with Edge TTS directly into in-memory MP3 bytes.

    Never raises: returns b"" on any failure so desktop playback can fall
    back to pyttsx3. Callers that need the failure reason (phone bridge)
    should use tts_bytes_for_bridge instead.
    """
    try:
        return _edge_tts_bytes_strict(text, whisper_mode)
    except Exception:
        return b""


def _sapi_tts_wav_ex(text: str, timeout: int = 60) -> Tuple[bytes, str]:
    """Offline Windows fallback: synthesize with a SAPI voice via PowerShell
    into WAV bytes. Separate process, so it never conflicts with the desktop
    pyttsx3 engine. Returns (wav_bytes, error_reason) — error_reason is ""
    on success and a short diagnosis on failure, so callers can report WHY
    synthesis failed instead of failing silently."""
    import subprocess
    import tempfile
    script = (
        "$ErrorActionPreference='Stop';"
        "$out=$args[0];$text=$args[1];"
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "try{"
        "$v=$s.GetInstalledVoices()|Where-Object{$_.VoiceInfo.Gender -eq 'Female'}|Select-Object -First 1;"
        "if($v){$s.SelectVoice($v.VoiceInfo.Name)};"
        "$s.SetOutputToWaveFile($out);"
        "$s.Speak($text)"
        "}finally{$s.Dispose()}"
    )
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", script, path, text[:1000]],
            capture_output=True, timeout=timeout)
        err = (r.stderr or b"").decode("utf-8", errors="replace").strip()
        if r.returncode != 0:
            reason = (err or f"powershell exit {r.returncode}")[-300:]
            return b"", f"sapi powershell failed: {reason}"
        if not os.path.exists(path):
            return b"", "sapi produced no output file" + (f": {err[-200:]}" if err else "")
        size = os.path.getsize(path)
        if size < 1000:
            return b"", f"sapi wav too small ({size}b)" + (f": {err[-200:]}" if err else "")
        with open(path, "rb") as f:
            return f.read(), ""
    except subprocess.TimeoutExpired:
        return b"", f"sapi timed out after {timeout}s"
    except Exception as e:
        return b"", f"sapi error: {type(e).__name__}: {e}"[-300:]
    finally:
        try:
            os.remove(path)
        except Exception as _e_silent:
            _config_silent.log_silent("_sapi_tts_wav_ex", _e_silent)


def _sapi_tts_wav(text: str, timeout: int = 60) -> bytes:
    """Bytes-only wrapper kept for the historical contract (tests monkeypatch
    this signature). For the failure reason, use _sapi_tts_wav_ex."""
    wav, _ = _sapi_tts_wav_ex(text, timeout)
    return wav


def tts_bytes_for_bridge(text: str) -> Tuple[bytes, str]:
    """TTS for the phone bridge: (audio_bytes, content_type).

    Edge TTS first; offline Windows SAPI voice as fallback. Raises
    RuntimeError with the underlying reason when nothing produces audio —
    the bridge turns that into a real error response instead of silence.
    """
    edge_err = ""
    try:
        return _edge_tts_bytes_strict(text), "audio/mpeg"
    except Exception as e:
        edge_err = str(e) or type(e).__name__
    if sys.platform == "win32":
        wav, sapi_err = _sapi_tts_wav_ex(text)
        if wav:
            return wav, "audio/wav"
        raise RuntimeError(f"edge-tts failed ({edge_err}); SAPI fallback failed ({sapi_err or 'unknown'})")
    raise RuntimeError(f"edge-tts failed ({edge_err})")


def _ffmpeg_to_pcm16(audio_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Decode any audio container to mono 16-bit PCM via ffmpeg. Raises on
    failure (ffmpeg missing or decode error)."""
    import subprocess
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-i", "pipe:0", "-ac", "1", "-ar", str(sample_rate),
         "-f", "s16le", "pipe:1"],
        input=audio_bytes, capture_output=True, timeout=60)
    if proc.returncode != 0 or not proc.stdout:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg exit {proc.returncode}: {err[-300:]}")
    return proc.stdout


def transcribe_audio(audio_bytes: bytes, mime: str = "audio/webm") -> str:
    """Transcribe raw audio bytes with the same recognizer pipeline the mic
    listener uses (local faster-whisper when available, else Google STT).

    WAV/FLAC/AIFF go through sr.AudioFile directly; anything else
    (webm/opus from the phone bridge) is decoded to PCM with ffmpeg first.
    Raises RuntimeError when conversion is unavailable - the bridge turns that
    into a real error response, never silence.
    """
    if not audio_bytes:
        raise RuntimeError("transcribe got empty audio")
    clean_mime = (mime or "audio/webm").split(";")[0].strip().lower()
    recognizer = sr.Recognizer()
    if clean_mime in ("audio/wav", "audio/x-wav", "audio/wave",
                      "audio/flac", "audio/x-flac",
                      "audio/aiff", "audio/x-aiff", "audio/x-aifc"):
        try:
            with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
                audio = recognizer.record(source)
        except Exception as e:
            raise RuntimeError(f"transcribe could not read {clean_mime}: {e}")
    else:
        try:
            pcm = _ffmpeg_to_pcm16(audio_bytes)
        except FileNotFoundError:
            raise RuntimeError(
                "transcribe needs ffmpeg to decode webm audio and it is not on PATH")
        except Exception as e:
            raise RuntimeError(f"transcribe decode failed: {e}")
        audio = sr.AudioData(pcm, sample_rate=16000, sample_width=2)
    return transcribe_local_or_cloud(audio, recognizer).strip().strip('"')


# Singleton and module-level API
_DEFAULT_SPEECH_MANAGER = SpeechManager()
_SPEECH_QUEUE = _DEFAULT_SPEECH_MANAGER.speech_queue
_AUDIO_PLAY_QUEUE = _DEFAULT_SPEECH_MANAGER.audio_play_queue
_SPEECH_STOP = _DEFAULT_SPEECH_MANAGER.speech_stop

# OPS Controls tab: master voice on/off (output + wake-word input).
VOICE_ENABLED = bool(_config_silent.get_setting("voice_enabled", True))

def voice_ready() -> bool:
    """True once the Edge voice probe has succeeded (greeting uses her real voice)."""
    return _DEFAULT_SPEECH_MANAGER.edge_ready


def voice_probe_completed() -> bool:
    """True once the first Edge probe round has finished (either way)."""
    return _DEFAULT_SPEECH_MANAGER.edge_probe_done.is_set()


def wait_for_voice_probe(timeout: Optional[float] = None) -> bool:
    """Block until the first Edge probe round completes; False on timeout."""
    return _DEFAULT_SPEECH_MANAGER.edge_probe_done.wait(timeout)


def speak(text: str, whisper_mode: bool = False):
    if not VOICE_ENABLED:
        return None
    return _DEFAULT_SPEECH_MANAGER.speak(text, whisper_mode)


def queue_stream_chunk(chunk: str):
    """Queue one completed sentence from a streaming LLM reply (A5)."""
    return _DEFAULT_SPEECH_MANAGER.queue_stream_chunk(chunk)

def interrupt_speech() -> int:
    return _DEFAULT_SPEECH_MANAGER.interrupt()

def is_speaking() -> bool:
    return _DEFAULT_SPEECH_MANAGER.is_speaking()

def wait_until_done(timeout: float = 6.0):
    return _DEFAULT_SPEECH_MANAGER.wait_until_done(timeout)

def start_speech_worker():
    return _DEFAULT_SPEECH_MANAGER.start()

def set_speech_state_hook(fn):
    """Wire HUD state updates (speaking/idle) from the speech playback worker."""
    _DEFAULT_SPEECH_MANAGER.on_state = fn

def set_speech_log_hook(fn):
    """Wire the speech subsystem's log into the app log (Activity).

    The default manager is constructed with on_log=None and nothing
    ever set it, so EVERY speech log line — the Edge probe's verdict
    ('Voice: Edge TTS (...)' / 'Voice: system fallback (...)'),
    synthesis errors, fallback drops — was silently discarded (Alek,
    2026-10-27: 'there is no voice line in activity'). The manager
    and its SAPI fallback voice hold separate on_log attributes;
    set both."""
    _DEFAULT_SPEECH_MANAGER.on_log = fn
    try:
        _DEFAULT_SPEECH_MANAGER._sapi.on_log = fn
    except Exception:
        pass

def transcribe_local_or_cloud(audio: sr.AudioData, recognizer_inst: Optional[sr.Recognizer] = None) -> str:
    r = recognizer_inst if recognizer_inst is not None else sr.Recognizer()
    return transcribe(audio, r, use_local_stt=fw_is_available())
