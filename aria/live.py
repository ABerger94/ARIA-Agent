"""
aria/live.py — Gemini Live voice mode (Gap fill 2, Port 8).

A selectable voice backend: a persistent Gemini Live session
(google-genai) carrying her REAL voice — the prebuilt Live voice
from the 'live_voice' setting, 'Kore' by default, the voice Alek
picked from Mark-LVI's set — instead of the Whisper -> provider
chain -> Edge TTS pipeline. The pipeline stays the default and
the fallback: Live starts only when the 'voice_mode' setting is
'live', and any failure (no key, google-genai missing, connect
refused, quota resting) hands control straight back to it. That
contract is Mark-LVI's own failover lesson: Live returning must
never cost her a working voice.

Safety shape, non-negotiable (ChatGPT gap consult, 2026-10-27):
Live function calls enter through the SAME dispatcher as every
other path — aria.tools.dispatch.execute_tool — so the approval
flow and the Command Center confirm banner cover Live calls
identically. This module has no execution path of its own.

Her tool declarations are already Gemini function_declarations
(aria.tools.schemas.TOOLS_DECLARATION); Live consumes them as-is.

Audio: mic PCM 16 kHz int16 up (sounddevice), model audio 24 kHz
int16 down to a sounddevice output stream. While presence says
AWAY (aria/presence.py), the mic pump does not stream — an open
session in an empty room is not a listening post.

Out of scope for v1 (Mark-LVI has them; they are separate fills):
vision injection, phone-mic relay, session-resumption handles.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Dict, List, Optional

try:
    from google import genai as _genai
    from google.genai import types as _genai_types
    HAVE_GENAI = True
except Exception:  # package absent or broken: Live is simply off
    _genai = None
    _genai_types = None
    HAVE_GENAI = False

# Model ladder (Mark-LVI core/gemini.py's live_model order): the
# newest Live model first, the known-good native-audio preview as
# the rung beneath it. A connect failure walks down, never up.
LIVE_MODELS = (
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-preview-12-2025",
)

DEFAULT_VOICE = "Kore"
MIC_RATE = 16000
OUT_RATE = 24000
_MIC_CHUNK = 1024


def build_live_config(types_mod, *, voice: str, system_instruction: str,
                      declarations: List[Dict[str, Any]]):
    """The LiveConnectConfig for one session. Pure: the caller
    passes the google.genai types module, so tests can drive this
    with a stand-in and pin the shape (AUDIO modality, the chosen
    prebuilt voice, her declarations offered verbatim, both
    transcriptions on — the conversation feed needs them)."""
    return types_mod.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types_mod.SpeechConfig(
            voice_config=types_mod.VoiceConfig(
                prebuilt_voice_config=types_mod.PrebuiltVoiceConfig(
                    voice_name=voice or DEFAULT_VOICE))),
        system_instruction=system_instruction,
        tools=[{"function_declarations": declarations}],
        output_audio_transcription={},
        input_audio_transcription={},
        session_resumption=types_mod.SessionResumptionConfig(),
    )


async def _execute_tool_call(tool_runner, fc) -> Dict[str, Any]:
    """Run one Live function call through the shared dispatcher
    (in an executor — dispatch is synchronous and may wait on a
    human approval) and shape the result as a FunctionResponse
    payload dict: {id, name, response: {result}}."""
    name = getattr(fc, "name", "") or ""
    args = dict(getattr(fc, "args", None) or {})
    loop = asyncio.get_running_loop()
    try:
        result, _ok = await loop.run_in_executor(
            None, lambda: tool_runner(name, args))
        text = result if isinstance(result, str) else str(result)
    except Exception as e:  # a tool failure is an answer, not a crash
        text = f"[Tool Error: {e}]"
    return {"id": getattr(fc, "id", None), "name": name,
            "response": {"result": text}}


async def run_session(session, *, tool_runner,
                      on_user_text: Optional[Callable] = None,
                      on_assistant_text: Optional[Callable] = None,
                      on_audio: Optional[Callable] = None,
                      on_turn: Optional[Callable] = None) -> None:
    """Drive one connected Live session until its receive stream
    ends. Audio chunks go to on_audio (24 kHz int16 bytes);
    transcriptions accumulate per turn and flush to the callbacks
    on turn_complete; function calls are answered in-line through
    tool_runner and replied with send_tool_response."""
    in_buf: List[str] = []
    out_buf: List[str] = []
    async for response in session.receive():
        content = getattr(response, "server_content", None)
        if content is not None:
            model_turn = getattr(content, "model_turn", None)
            if model_turn is not None:
                for part in getattr(model_turn, "parts", None) or []:
                    inline = getattr(part, "inline_data", None)
                    data = getattr(inline, "data", None) if inline else None
                    if data and on_audio is not None:
                        on_audio(data)
            it = getattr(content, "input_transcription", None)
            if it is not None and getattr(it, "text", None):
                in_buf.append(it.text)
            ot = getattr(content, "output_transcription", None)
            if ot is not None and getattr(ot, "text", None):
                out_buf.append(ot.text)
            if getattr(content, "turn_complete", False):
                if in_buf and on_user_text is not None:
                    on_user_text("".join(in_buf).strip())
                if out_buf and on_assistant_text is not None:
                    on_assistant_text("".join(out_buf).strip())
                in_buf, out_buf = [], []
                if on_turn is not None:
                    on_turn()
        tool_call = getattr(response, "tool_call", None)
        if tool_call is not None:
            responses = []
            for fc in getattr(tool_call, "function_calls", None) or []:
                responses.append(
                    await _execute_tool_call(tool_runner, fc))
            if responses:
                await session.send_tool_response(
                    function_responses=responses)


class LiveManager:
    """Owns the Live session thread for the process. start()
    returns True only when the session machinery is underway —
    the caller treats False as 'use the pipeline', and a session
    that dies mid-run reports through on_state('ended') so the
    caller can re-arm the pipeline listener."""

    def __init__(self, *, api_key: str, voice: str = DEFAULT_VOICE,
                 system_instruction: str = "",
                 declarations: Optional[List[Dict[str, Any]]] = None,
                 tool_runner: Optional[Callable] = None,
                 mic_device=None,
                 on_user_text: Optional[Callable] = None,
                 on_assistant_text: Optional[Callable] = None,
                 on_state: Optional[Callable] = None,
                 log: Optional[Callable] = None,
                 client_factory: Optional[Callable] = None,
                 presence_check: Optional[Callable] = None):
        self._api_key = api_key
        self._voice = voice or DEFAULT_VOICE
        self._system_instruction = system_instruction
        self._declarations = declarations or []
        self._tool_runner = tool_runner
        self._mic_device = mic_device
        self._on_user_text = on_user_text
        self._on_assistant_text = on_assistant_text
        self._on_state = on_state or (lambda state: None)
        self._log = log or (lambda msg: None)
        self._client_factory = client_factory
        self._presence_check = presence_check
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.active = False

    # -- internals ------------------------------------------------
    def _make_client(self):
        if self._client_factory is not None:
            return self._client_factory(self._api_key)
        return _genai.Client(api_key=self._api_key)

    async def _mic_pump(self, session, loop) -> None:
        """Stream mic PCM while the session lives. sounddevice's
        callback runs on its own thread; chunks cross into the
        loop through a queue. Presence AWAY pauses the stream —
        nothing leaves the machine from an empty room."""
        import queue as _queue
        chunks: "_queue.Queue[bytes]" = _queue.Queue(maxsize=50)

        def _cb(indata, frames, time_info, status):
            try:
                chunks.put_nowait(bytes(indata))
            except Exception:
                pass

        import sounddevice as sd
        stream = sd.RawInputStream(
            samplerate=MIC_RATE, channels=1, dtype="int16",
            blocksize=_MIC_CHUNK, device=self._mic_device,
            callback=_cb)
        stream.start()
        try:
            while not self._stop.is_set():
                try:
                    data = chunks.get(timeout=0.25)
                except Exception:
                    continue
                if self._presence_check is not None:
                    try:
                        if not self._presence_check():
                            continue
                    except Exception:
                        pass
                await session.send_realtime_input(audio=_genai_types.Blob(
                    data=data, mime_type="audio/pcm;rate=16000"))
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    async def _play_sink(self):
        """Returns (on_audio, closer): a sounddevice output stream
        at the model's 24 kHz and the callable feeding it."""
        import sounddevice as sd
        stream = sd.RawOutputStream(
            samplerate=OUT_RATE, channels=1, dtype="int16",
            blocksize=2048)
        stream.start()

        def _sink(data: bytes) -> None:
            try:
                stream.write(data)
            except Exception:
                pass

        return _sink, stream

    async def _run(self) -> None:
        client = self._make_client()
        config = build_live_config(
            _genai_types, voice=self._voice,
            system_instruction=self._system_instruction,
            declarations=self._declarations)
        last_err: Optional[Exception] = None
        for model in LIVE_MODELS:
            if self._stop.is_set():
                return
            try:
                async with client.aio.live.connect(
                        model=model, config=config) as session:
                    self.active = True
                    self._on_state("live")
                    self._log(f"Gemini Live connected ({model}, "
                              f"voice {self._voice})")
                    sink, stream = await self._play_sink()
                    mic_task = asyncio.create_task(
                        self._mic_pump(session, asyncio.get_running_loop()))
                    try:
                        await run_session(
                            session, tool_runner=self._tool_runner,
                            on_user_text=self._on_user_text,
                            on_assistant_text=self._on_assistant_text,
                            on_audio=sink)
                    finally:
                        mic_task.cancel()
                        try:
                            stream.stop()
                            stream.close()
                        except Exception:
                            pass
                    return  # a clean session end is not a failure
            except Exception as e:
                last_err = e
                self._log(f"Gemini Live connect failed on {model}: {e}")
                continue
        raise RuntimeError(f"Gemini Live unavailable: {last_err}")

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as e:
            self._log(f"Gemini Live ended: {e}")
        finally:
            self.active = False
            self._on_state("ended")

    # -- surface --------------------------------------------------
    def start(self) -> bool:
        """False = Live cannot run here (no package, no key, no
        tool runner); the caller falls back to the pipeline and
        nothing else changes."""
        if not HAVE_GENAI or _genai is None:
            self._log("Gemini Live unavailable: google-genai not installed")
            return False
        if not self._api_key:
            self._log("Gemini Live unavailable: no GEMINI_API_KEY")
            return False
        if self._tool_runner is None:
            self._log("Gemini Live unavailable: no tool dispatcher wired")
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name="aria-live")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
