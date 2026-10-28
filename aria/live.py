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

App lock (aria/applock.py): Live is a LAPTOP voice path, so the
lock covers it fully — while locked, the mic pump drops every
chunk, the play sink drops every sample, tool calls are refused
without reaching the dispatcher, and a lock watcher shuts the
session down outright. (The phone bridge is the exempt path;
Live is not it.)

Session-resumption handles are in: a resumable handle captured
from the server is handed back on the next connect, a rejected
handle is dropped for one fresh retry on the same model, and the
last model that connected is latched so a reconnect starts there.

Out of scope for v1 (Mark-LVI has them; they are separate fills):
vision injection, phone-mic relay.
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
                      declarations: List[Dict[str, Any]],
                      handle: Optional[str] = None):
    """The LiveConnectConfig for one session. Pure: the caller
    passes the google.genai types module, so tests can drive this
    with a stand-in and pin the shape (AUDIO modality, the chosen
    prebuilt voice, her declarations offered verbatim, both
    transcriptions on — the conversation feed needs them).

    ``handle`` is the session-resumption handle captured from a
    previous session (None = start fresh, exactly the old
    behaviour). Some types modules / fakes only accept a no-arg
    SessionResumptionConfig; in that case the handle is assigned
    as an attribute instead of a constructor kwarg."""
    try:
        resumption = types_mod.SessionResumptionConfig(handle=handle)
    except TypeError:
        resumption = types_mod.SessionResumptionConfig()
        try:
            resumption.handle = handle
        except Exception:
            pass
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
        session_resumption=resumption,
    )


def _app_locked() -> bool:
    """Current app-lock state (aria/applock.py). Lazy import and
    fail-open on any error, mirroring speech.py's gates: a broken
    lock module must not kill her voice, and in the test harness
    (this file loaded standalone) there is no package to import."""
    try:
        from aria import applock as _applock_lv
        return bool(_applock_lv.is_locked())
    except Exception:
        return False


async def _execute_tool_call(tool_runner, fc) -> Dict[str, Any]:
    """Run one Live function call through the shared dispatcher
    (in an executor — dispatch is synchronous and may wait on a
    human approval) and shape the result as a FunctionResponse
    payload dict: {id, name, response: {result}}.

    App lock: while locked the call is refused HERE, before the
    dispatcher — Live never passes through handle_action's locked
    guard, so this is the gate that covers it."""
    name = getattr(fc, "name", "") or ""
    if _app_locked():
        return {"id": getattr(fc, "id", None), "name": name,
                "response": {"result": "ARIA is locked."}}
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
                      on_turn: Optional[Callable] = None,
                      on_resumption: Optional[Callable] = None) -> None:
    """Drive one connected Live session across ALL its turns.

    The google-genai SDK's ``session.receive()`` yields exactly
    ONE model turn per call — its loop breaks the moment a
    turn_complete / interaction-complete message arrives (verified
    in the installed SDK source, google-genai live.py
    ``AsyncSession.receive``). The first version of this function
    consumed a single receive() pass, so a Live session silently
    ended after its first completed turn and the manager handed
    control back to the wake-word pipeline (ChatGPT improvement
    pass 2 runner-up, verified + fixed 2026-10-28). The receive
    loop below re-enters receive() on the SAME connection after
    every completed turn, which is the SDK's documented multi-turn
    pattern.

    Endings keep their old meanings: a receive() pass that yields
    nothing at all ends the session cleanly (and guards against
    spinning on an empty stream); a genuine disconnect raises out
    of receive() (the SDK raises APIError when the websocket
    closes) and propagates to LiveManager exactly as before; the
    app-lock watcher still cancels the session task from outside.

    Audio chunks go to on_audio (24 kHz int16 bytes);
    transcriptions accumulate per turn and flush to the callbacks
    on turn_complete; function calls are answered in-line through
    tool_runner and replied with send_tool_response.

    Session resumption: every response may carry a
    ``session_resumption_update``. Only a resumable update with a
    ``new_handle`` is kept (a handle replayed mid-turn is what the
    resumable flag exists to prevent) — it is handed to
    ``on_resumption`` so the next connect can continue this
    conversation instead of starting fresh."""
    in_buf: List[str] = []
    out_buf: List[str] = []
    while True:
        got_any = False
        async for response in session.receive():
            got_any = True
            if on_resumption is not None:
                try:
                    _sru = getattr(response, "session_resumption_update", None)
                    if _sru is not None:
                        _new_handle = getattr(_sru, "new_handle", None)
                        if getattr(_sru, "resumable", False) and _new_handle:
                            try:
                                on_resumption(_new_handle)
                            except Exception:
                                pass
                except Exception:
                    pass
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
        if not got_any:
            # An empty pass = the stream is over. Never re-enter
            # receive() in a hot loop waiting for a turn that is
            # not coming.
            break


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
        self._resume_handle: Optional[str] = None
        self._live_model: Optional[str] = None

    # -- session resumption ---------------------------------------
    @property
    def resume_handle(self) -> Optional[str]:
        """The resumption handle captured from the last session,
        or None when the next connect should start fresh."""
        return self._resume_handle

    @property
    def live_model(self) -> Optional[str]:
        """The latched Live model — the last rung that connected
        successfully — or None before any successful connect."""
        return self._live_model

    def drop_resumption(self) -> None:
        """Forget the stored resumption handle; the next connect
        starts a fresh session."""
        self._resume_handle = None

    def _store_resumption(self, handle) -> None:
        if handle:
            self._resume_handle = handle

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
                    data = chunks.get_nowait()
                except Exception:
                    # Poll, never block: a blocking Queue.get here
                    # starved the whole Live event loop (run_session
                    # included) whenever the mic was quiet.
                    await asyncio.sleep(0.05)
                    continue
                if _app_locked():
                    # App lock: nothing leaves the machine while
                    # locked (the lock watcher also shuts the
                    # session down; this drop covers the window
                    # before it fires).
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
            if _app_locked():
                # App lock: the laptop stays silent — Live audio
                # bypasses speech.py's gates, so it is gated here.
                return
            try:
                stream.write(data)
            except Exception:
                pass

        return _sink, stream

    async def _lock_watch(self, session_task) -> None:
        """App lock shutdown: poll the lock while a session lives.
        On lock, stop the manager and cancel the session task —
        a locked laptop does not keep a Live session open. (The
        mic/audio/tool gates cover the sub-second window before
        this fires; on_state('ended') then re-arms the pipeline,
        whose wake loop is itself lock-aware, so voice unlock
        keeps working.)"""
        try:
            while not self._stop.is_set():
                if _app_locked():
                    self._log("App locked — shutting Gemini Live down")
                    self._stop.set()
                    session_task.cancel()
                    return
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        client = self._make_client()
        last_err: Optional[Exception] = None
        # Failover latch: start at the last model that connected
        # successfully instead of always walking from rung 0. A
        # clean session end leaves the latch where it is.
        start_idx = 0
        if self._live_model in LIVE_MODELS:
            start_idx = LIVE_MODELS.index(self._live_model)  # type: ignore[arg-type]
        models_to_try = LIVE_MODELS[start_idx:]
        had_failure = False
        failover_notified = False
        for model in models_to_try:
            if self._stop.is_set():
                return
            retried_fresh = False
            while True:
                if self._stop.is_set():
                    return
                handle_in_use = self._resume_handle
                config = build_live_config(
                    _genai_types, voice=self._voice,
                    system_instruction=self._system_instruction,
                    declarations=self._declarations,
                    handle=handle_in_use)
                try:
                    async with client.aio.live.connect(
                            model=model, config=config) as session:
                        # Connected — latch this model for the
                        # next _run before anything else can fail.
                        self._live_model = model
                        self.active = True
                        if had_failure and not failover_notified:
                            failover_notified = True
                            try:
                                self._on_state("failover")
                            except Exception:
                                pass
                            self._log(
                                f"Gemini Live failed over to {model}")
                        self._on_state("live")
                        self._log(f"Gemini Live connected ({model}, "
                                  f"voice {self._voice})")
                        sink, stream = await self._play_sink()
                        mic_task = asyncio.create_task(
                            self._mic_pump(
                                session, asyncio.get_running_loop()))
                        session_task = asyncio.create_task(run_session(
                            session, tool_runner=self._tool_runner,
                            on_user_text=self._on_user_text,
                            on_assistant_text=self._on_assistant_text,
                            on_audio=sink,
                            on_resumption=self._store_resumption))
                        lock_task = asyncio.create_task(
                            self._lock_watch(session_task))
                        try:
                            await session_task
                        except asyncio.CancelledError:
                            # The lock watcher cancels the session
                            # task when the app locks — that is a
                            # clean end, not a failure. Any other
                            # cancellation propagates.
                            if not self._stop.is_set():
                                raise
                            self._log("Gemini Live session ended "
                                      "(app lock)")
                        finally:
                            lock_task.cancel()
                            mic_task.cancel()
                            try:
                                stream.stop()
                                stream.close()
                            except Exception:
                                pass
                        return  # a clean session end is not a failure
                except Exception as e:
                    last_err = e
                    err_text = str(e).lower()
                    # Resumption handle rejected: the server will
                    # not accept the stored handle (expired, or
                    # its session is gone). Drop it once and retry
                    # the SAME model fresh — replaying a dead
                    # handle on every retry would be the feature
                    # preventing any reconnect at all.
                    if (handle_in_use is not None and not retried_fresh
                            and ("handle" in err_text
                                 or "resumption" in err_text)):
                        self._resume_handle = None
                        self._log(
                            "Resumption handle rejected — starting "
                            "a fresh session")
                        retried_fresh = True
                        continue
                    had_failure = True
                    self._log(
                        f"Gemini Live connect failed on {model}: {e}")
                    # The latch moves on: a rung that fails to
                    # connect is not retried first next time.
                    if self._live_model == model:
                        _idx = LIVE_MODELS.index(model)
                        self._live_model = (
                            LIVE_MODELS[_idx + 1]
                            if _idx + 1 < len(LIVE_MODELS) else None)
                    break
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
