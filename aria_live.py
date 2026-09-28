"""
A.R.I.A. Gemini Multimodal Live WebSocket Engine
Handles bidirectional real-time audio streaming, low-latency native voice synthesis,
and live function execution with Google's Gemini Multimodal Live WebSocket API.
"""

import os
import sys
import json
import time
import base64
import queue
import asyncio
import logging
import threading
import sounddevice as sd
import numpy as np

try:
    import websockets
except ImportError:
    websockets = None

logger = logging.getLogger("ARIA_LIVE")

DEFAULT_MODEL = "models/gemini-3.8-live"
FALLBACK_MODEL = "models/gemini-2.5-flash-native-audio-latest"
DEFAULT_VOICE = "Aoede"  # Aoede, Kore, Puck, Fenrir, Charon
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000


class GeminiLiveBridge:
    """Manages persistent WebSocket connection to Gemini Multimodal Live API."""

    def __init__(self, api_key, model=DEFAULT_MODEL, voice=DEFAULT_VOICE,
                 system_instruction=None, tool_declarations=None, tool_executor=None,
                 on_transcript=None, on_state_change=None, on_log=None):
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.system_instruction = system_instruction
        self.tool_declarations = tool_declarations or []
        self.tool_executor = tool_executor
        self.on_transcript = on_transcript
        self.on_state_change = on_state_change
        self.on_log = on_log or (lambda msg: print(f"[LIVE] {msg}"))

        self.ws = None
        self.loop = None
        self._thread = None
        self._running = False
        self._ready_event = threading.Event()

        # Audio Queues & Streams
        self._mic_stream = None
        self._is_recording = False
        self._out_stream = None
        self._audio_out_queue = queue.Queue()
        self._interrupted = threading.Event()
        self._playback_thread = None

        # Turn transcript
        self.current_turn_transcript = []
        self._lock = threading.Lock()

    def log(self, msg):
        try:
            self.on_log(msg)
        except Exception:
            pass

    def set_state(self, state):
        if self.on_state_change:
            try:
                self.on_state_change(state)
            except Exception:
                pass

    def is_ready(self):
        return self._running and self._ready_event.is_set() and self.ws is not None

    def start(self):
        """Starts background asyncio event loop thread and audio playback worker."""
        if self._running:
            return True
        if not websockets:
            self.log("websockets library not available.")
            return False
        if not self.api_key:
            self.log("Gemini Live: no API key provided.")
            return False

        self._running = True
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="aria-live-ws")
        self._thread.start()

        self._playback_thread = threading.Thread(target=self._playback_worker, daemon=True, name="aria-live-audio")
        self._playback_thread.start()

        # Wait up to 4s for initial connection
        ready = self._ready_event.wait(timeout=4.0)
        if ready:
            self.log(f"Gemini Live online: {self.model} ({self.voice})")
        else:
            self.log("Gemini Live connecting in background...")
        return ready

    def stop(self):
        """Clean shutdown of live engine."""
        self._running = False
        self.interrupt()
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._close_ws(), self.loop)
        self.stop_mic()
        if self._out_stream:
            try:
                self._out_stream.stop()
                self._out_stream.close()
            except Exception:
                pass
            self._out_stream = None

    def interrupt(self):
        """Instant barge-in / speech interrupt: halts playback and flushes audio queue."""
        self._interrupted.set()
        while not self._audio_out_queue.empty():
            try:
                self._audio_out_queue.get_nowait()
                self._audio_out_queue.task_done()
            except Exception:
                break
        if self._out_stream and self._out_stream.active:
            try:
                self._out_stream.abort()
            except Exception:
                pass
        self._interrupted.clear()

    def _playback_worker(self):
        """Pipes 24kHz PCM chunks to sounddevice OutputStream."""
        while self._running:
            try:
                chunk = self._audio_out_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if self._interrupted.is_set():
                self._audio_out_queue.task_done()
                continue

            if self._out_stream is None or not self._out_stream.active:
                try:
                    self._out_stream = sd.OutputStream(
                        samplerate=OUTPUT_SAMPLE_RATE,
                        channels=1,
                        dtype='int16',
                        blocksize=1024
                    )
                    self._out_stream.start()
                except Exception as e:
                    self.log(f"Audio out error: {e}")
                    self._audio_out_queue.task_done()
                    continue

            try:
                self._out_stream.write(chunk)
            except Exception as e:
                self.log(f"Audio write error: {e}")
            finally:
                self._audio_out_queue.task_done()

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._connection_supervisor())

    async def _close_ws(self):
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass

    async def _connection_supervisor(self):
        url = (f"wss://generativelanguage.googleapis.com/ws/"
               f"google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
               f"?key={self.api_key}")

        while self._running:
            try:
                async with websockets.connect(url, max_size=16 * 1024 * 1024, ping_interval=20, ping_timeout=20) as ws:
                    self.ws = ws
                    setup_msg = {
                        "setup": {
                            "model": self.model,
                            "generationConfig": {
                                "responseModalities": ["AUDIO"],
                                "speechConfig": {
                                    "voiceConfig": {
                                        "prebuiltVoiceConfig": {
                                            "voiceName": self.voice
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if self.system_instruction:
                        setup_msg["setup"]["systemInstruction"] = {
                            "parts": [{"text": self.system_instruction}]
                        }
                    if self.tool_declarations:
                        setup_msg["setup"]["tools"] = [
                            {"functionDeclarations": self.tool_declarations}
                        ]

                    await ws.send(json.dumps(setup_msg))
                    init_resp = await asyncio.wait_for(ws.recv(), timeout=12.0)
                    resp_data = json.loads(init_resp)
                    if "setupComplete" in resp_data:
                        self._ready_event.set()
                        self.log("Gemini Live session established.")

                    await self._recv_loop(ws)
            except Exception as e:
                self._ready_event.clear()
                if not self._running:
                    break
                self.log(f"Live WebSocket reconnecting: {e}")
                await asyncio.sleep(2.0)

    async def _recv_loop(self, ws):
        while self._running:
            try:
                msg_raw = await ws.recv()
            except websockets.exceptions.ConnectionClosed:
                break
            except Exception:
                break

            try:
                msg = json.loads(msg_raw)
            except Exception:
                continue

            if "toolCall" in msg:
                asyncio.create_task(self._handle_tool_call(msg["toolCall"]))
                continue

            sc = msg.get("serverContent")
            if not sc:
                continue

            mt = sc.get("modelTurn")
            if mt:
                self.set_state("speaking")
                for p in mt.get("parts", []):
                    if "inlineData" in p:
                        pcm_bytes = base64.b64decode(p["inlineData"]["data"])
                        pcm_arr = np.frombuffer(pcm_bytes, dtype=np.int16)
                        self._audio_out_queue.put(pcm_arr)

            ot = sc.get("outputTranscription")
            if ot:
                chunk_text = ot.get("text", "")
                if chunk_text:
                    self.current_turn_transcript.append(chunk_text)
                    if self.on_transcript:
                        try:
                            self.on_transcript(chunk_text, is_final=False)
                        except Exception:
                            pass

            if sc.get("turnComplete"):
                full_text = "".join(self.current_turn_transcript).strip()
                if full_text and self.on_transcript:
                    try:
                        self.on_transcript(full_text, is_final=True)
                    except Exception:
                        pass
                self.current_turn_transcript = []
                self.set_state("idle")

            if sc.get("interrupted"):
                self.interrupt()
                self.current_turn_transcript = []
                self.set_state("idle")

    async def _handle_tool_call(self, tool_call):
        self.set_state("thinking")
        fcalls = tool_call.get("functionCalls", [])
        responses = []
        for fc in fcalls:
            call_id = fc.get("id")
            name = fc.get("name")
            args = fc.get("args", {})
            self.log(f"Live Tool: {name}")

            result = "OK"
            if self.tool_executor:
                try:
                    result = self.tool_executor(name, args)
                except Exception as e:
                    result = f"Tool error: {e}"
            responses.append({
                "id": call_id,
                "response": {"output": {"result": str(result)}}
            })

        tool_resp_msg = {
            "toolResponse": {
                "functionResponses": responses
            }
        }
        if self.ws:
            try:
                await self.ws.send(json.dumps(tool_resp_msg))
            except Exception as e:
                self.log(f"Failed sending live toolResponse: {e}")

    def send_audio_chunk(self, pcm_bytes):
        """Sends a chunk of 16kHz int16 PCM audio."""
        if not self.is_ready():
            return False
        b64_data = base64.b64encode(pcm_bytes).decode("utf-8")
        payload = {
            "realtimeInput": {
                "mediaChunks": [
                    {
                        "mimeType": f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                        "data": b64_data
                    }
                ]
            }
        }
        asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(payload)), self.loop)
        return True

    def commit_turn(self):
        """Signals user turn completion."""
        if not self.is_ready():
            return False
        self.set_state("thinking")
        payload = {
            "clientContent": {
                "turnComplete": True
            }
        }
        asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(payload)), self.loop)
        return True

    def send_text_prompt(self, text):
        """Sends user text prompt."""
        if not self.is_ready():
            return False
        self.set_state("thinking")
        payload = {
            "clientContent": {
                "turns": [
                    {
                        "role": "user",
                        "parts": [{"text": text}]
                    }
                ],
                "turnComplete": True
            }
        }
        asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(payload)), self.loop)
        return True

    def start_mic(self):
        """Direct mic stream."""
        if self._is_recording:
            return
        self._is_recording = True
        self.set_state("listening")

        def mic_callback(indata, frames, time_info, status):
            if not self._is_recording:
                return
            pcm_bytes = indata.tobytes()
            self.send_audio_chunk(pcm_bytes)

        try:
            self._mic_stream = sd.InputStream(
                samplerate=INPUT_SAMPLE_RATE,
                channels=1,
                dtype='int16',
                blocksize=3200,
                callback=mic_callback
            )
            self._mic_stream.start()
        except Exception as e:
            self.log(f"Mic start error: {e}")
            self._is_recording = False

    def stop_mic(self):
        """Stops direct mic stream."""
        self._is_recording = False
        if self._mic_stream:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None
