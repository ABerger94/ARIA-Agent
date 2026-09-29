"""A.R.I.A. Speech & Audio Subsystem.

Edge TTS pipelined playback with pyttsx3 fallback, sentence streaming,
faster-whisper and Google cloud STT, and speech interrupt control.
"""

from __future__ import annotations

import asyncio
import io
import os
import queue
import re
import sys
import threading
import time
from typing import Callable, Optional, Tuple, List

import audioop
import numpy as np
import speech_recognition as sr

from aria.config import WORKSPACE_DIR

EDGE_TTS_VOICE = "en-US-AriaNeural"
WHISPER_EDGE_KWARGS = {"rate": "-10%", "pitch": "-8Hz", "volume": "-60%"}

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

# ---------------- Fallback TTS (pyttsx3) ----------------

def init_pyttsx3(on_log: Optional[Callable[[str], None]] = None):
    """Initialize pyttsx3 and attempt to select a Windows Natural voice."""
    import pyttsx3
    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", 170)
        voices = engine.getProperty("voices") or []
        for v in voices:
            name = (v.name or "").lower()
            if any(k in name for k in ["aria", "jenny", "guy", "natural"]):
                engine.setProperty("voice", v.id)
                if on_log:
                    on_log(f"Voice fallback: {v.name}")
                return engine
        return engine
    except Exception as e:
        if on_log:
            on_log(f"Voice fallback init failed: {e}")
        return None


# ---------------- Local STT (faster-whisper) ----------------

# v9.17: Google cloud transcription is the default. The local faster-whisper
# path stays in the file, dormant unless this flag is flipped to True.
_USE_LOCAL_STT = False

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


def fw_pcm(audio: sr.AudioData) -> np.ndarray:
    """sr.AudioData -> float32 mono PCM at 16000Hz in [-1, 1] for faster-whisper."""
    raw = audio.get_raw_data()
    sample_rate = audio.sample_rate
    sample_width = audio.sample_width

    if sample_width != 2:
        try:
            raw = audioop.lin2lin(raw, sample_width, 2)
        except Exception:
            pass

    if sample_rate != 16000 and sample_rate > 0:
        try:
            raw, _ = audioop.ratecv(raw, 2, 1, sample_rate, 16000, None)
        except Exception:
            pass

    return (np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0)


def ptt_join_audio(frames_list: List[bytes], sample_rate: int, sample_width: int) -> Tuple[sr.AudioData, float]:
    """Concatenate raw PCM chunks -> (sr.AudioData, seconds)."""
    frames = b"".join(frames_list)
    total_s = len(frames) / float(sample_rate * sample_width)
    return sr.AudioData(frames, sample_rate, sample_width), total_s


def transcribe(audio: sr.AudioData, recognizer: sr.Recognizer, use_local_stt: bool = False, on_log: Optional[Callable[[str], None]] = None) -> str:
    """Transcribe sr.AudioData to text. Prefers faster-whisper with strict VAD filtering, falls back to Google on STT failure."""
    if not use_local_stt:
        try:
            return recognizer.recognize_google(audio)
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
            return text
        except Exception as e:
            if on_log:
                on_log(f"Local STT failed, falling back to Google: {e}")

    try:
        return recognizer.recognize_google(audio)
    except Exception:
        return ""


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

        self._tts_engine = None
        self._voice_rotation_idx = 0
        self._voice_rotation_lock = threading.Lock()
        self._started = False

    def log(self, msg: str):
        if self.on_log:
            self.on_log(msg)

    def init_tts(self):
        if self._tts_engine is None:
            self._tts_engine = init_pyttsx3(on_log=self.on_log)

    def _whisper_edge_kwargs(self) -> dict:
        return dict(WHISPER_EDGE_KWARGS) if self.whisper_mode else {}

    def _init_edge_voice(self):
        try:
            import edge_tts
        except Exception as e:
            self.log("Voice: system fallback (edge-tts import broken)")
            return

        try:
            import pygame
            pygame.mixer.init()
        except Exception as e:
            self.log("Voice: system fallback (mixer init failed)")
            return

        probe_ok = False
        for attempt in range(1, 4):
            try:
                b = edge_tts_bytes("Voice check.")
                if b:
                    probe_ok = True
                    break
            except Exception as e:
                time.sleep(3)

        if probe_ok:
            self.edge_ready = True
            self.log("Voice: Edge TTS (AriaNeural)")
        else:
            self.log("Voice: system fallback (synthesis failed)")

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

                if is_edge and audio_buf:
                    import pygame
                    try:
                        if hasattr(audio_buf, "seek"):
                            audio_buf.seek(0)
                        pygame.mixer.music.load(audio_buf)
                        pygame.mixer.music.play()
                        deadline = time.time() + max(10, len(text) * 0.15)
                        while pygame.mixer.music.get_busy() and time.time() < deadline and not self.speech_stop.is_set():
                            time.sleep(0.04)
                    finally:
                        try:
                            pygame.mixer.music.unload()
                        except Exception:
                            pass
                else:
                    self.init_tts()
                    if self._tts_engine:
                        if self.whisper_mode:
                            v0 = self._tts_engine.getProperty("volume")
                            r0 = self._tts_engine.getProperty("rate")
                            try:
                                self._tts_engine.setProperty("volume", min(float(v0), 0.4))
                                self._tts_engine.setProperty("rate", max(60, int(r0) - 40))
                                self._tts_engine.say(text)
                                self._tts_engine.runAndWait()
                            finally:
                                self._tts_engine.setProperty("volume", v0)
                                self._tts_engine.setProperty("rate", r0)
                        else:
                            self._tts_engine.say(text)
                            self._tts_engine.runAndWait()
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

        threading.Thread(target=self._init_edge_voice, daemon=True, name="aria-voice-init").start()

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
        except Exception:
            pass
        if self._tts_engine:
            try:
                self._tts_engine.stop()
            except Exception:
                pass
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
        except Exception:
            pass
        return False

    def wait_until_done(self, timeout: float = 6.0):
        """Block until speech queue and playback finish."""
        deadline = time.time() + timeout
        while self.is_speaking() and time.time() < deadline:
            time.sleep(0.05)


def _edge_tts_bytes_strict(text: str, whisper_mode: bool = False) -> bytes:
    """Edge TTS synthesis that raises on failure (ImportError, network, timeout)."""
    import asyncio
    import edge_tts  # raises ImportError when the package is missing

    kwargs = WHISPER_EDGE_KWARGS if whisper_mode else {}

    async def _gen():
        chunks = []
        async for chunk in edge_tts.Communicate(text, EDGE_TTS_VOICE, **kwargs).stream():
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
        except Exception:
            pass


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


def transcribe_audio(audio_bytes: bytes, mime: str = "audio/webm") -> str:
    """Transcribe raw audio bytes using Gemini multimodal audio perception."""
    import base64
    from aria.agent.brain import gemini_text

    b64 = base64.b64encode(audio_bytes).decode("utf-8")
    clean_mime = mime.split(";")[0] if mime else "audio/webm"
    contents = [{"role": "user", "parts": [
        {"text": "Transcribe this voice command exactly. Output only the transcription, no commentary."},
        {"inline_data": {"mime_type": clean_mime, "data": b64}}
    ]}]
    return gemini_text("You are a speech transcriber.", contents).strip().strip('"')


# Singleton and module-level API
_DEFAULT_SPEECH_MANAGER = SpeechManager()
_SPEECH_QUEUE = _DEFAULT_SPEECH_MANAGER.speech_queue
_AUDIO_PLAY_QUEUE = _DEFAULT_SPEECH_MANAGER.audio_play_queue
_SPEECH_STOP = _DEFAULT_SPEECH_MANAGER.speech_stop

def voice_ready() -> bool:
    """True once the Edge voice probe has succeeded (greeting uses her real voice)."""
    return _DEFAULT_SPEECH_MANAGER.edge_ready


def speak(text: str, whisper_mode: bool = False):
    return _DEFAULT_SPEECH_MANAGER.speak(text, whisper_mode)

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

def transcribe_local_or_cloud(audio: sr.AudioData, recognizer_inst: Optional[sr.Recognizer] = None) -> str:
    r = recognizer_inst if recognizer_inst is not None else sr.Recognizer()
    return transcribe(audio, r, use_local_stt=(_USE_LOCAL_STT and fw_is_available()))
