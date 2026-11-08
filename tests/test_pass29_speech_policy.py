"""Pass 29 #35: direct speech-queue paths must not bypass mute / app lock.

Bug: speech.speak() gated on VOICE_ENABLED + the app lock, but three
direct-queue sites (scheduler timed/on-return/brief busy paths,
brain.py streaming fallback) wrote straight to _SPEECH_QUEUE, and
module-level queue_stream_chunk checked the lock but NOT
VOICE_ENABLED. The workers never re-checked policy either, so a
sentence enqueued while allowed played after a mute.

Fix contract:
- one admission helper (speech_allowed: voice on AND app unlocked)
  used by every enqueue site: speak, enqueue_speech,
  queue_stream_chunk (module + manager), and the workers;
- a policy change after enqueue still blocks synthesis/playback —
  the workers re-check before doing work;
- the phone path is unaffected: the bridge synthesizes bytes
  directly via tts_bytes_for_bridge and never touches the queue.
"""
import importlib.util
import sys
import threading
import time
import types
from pathlib import Path

PKG = Path(__file__).parents[1] / "aria"

pkg = types.ModuleType("aria")
pkg.__path__ = [str(PKG)]
sys.modules.setdefault("aria", pkg)

try:
    import speech_recognition  # noqa: F401
except Exception:
    _sr = types.ModuleType("speech_recognition")
    _sr.Recognizer = object
    _sr.AudioData = object
    _sr.AudioSource = object
    sys.modules["speech_recognition"] = _sr

import pytest  # noqa: E402


def _load_speech():
    spec = importlib.util.spec_from_file_location(
        "aria_speech_policy", PKG / "speech.py")
    sp = importlib.util.module_from_spec(spec)
    sys.modules["aria_speech_policy"] = sp
    spec.loader.exec_module(sp)
    return sp


@pytest.fixture()
def sp(monkeypatch):
    sp = _load_speech()
    monkeypatch.setattr(sp, "_SESSION_VOICE", None)
    monkeypatch.setitem(sys.modules, "edge_tts", types.ModuleType("edge_tts"))
    pygame = types.ModuleType("pygame")
    pygame.mixer = types.SimpleNamespace(init=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "pygame", pygame)
    # Voice on + app unlocked by default; tests flip one lever at a time.
    monkeypatch.setattr(sp, "VOICE_ENABLED", True)
    monkeypatch.setattr(sp._config_silent, "get_setting",
                        lambda key, default=None: True
                        if key == "voice_enabled" else default)
    monkeypatch.setitem(sys.modules, "aria.applock",
                        types.SimpleNamespace(is_locked=lambda: False))
    return sp


def _manager(sp, logs):
    return sp.SpeechManager(workspace_dir="/tmp", on_log=logs.append)


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
        q.task_done()
    return out


def _wait_until(fn, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.02)
    return fn()


# ------------------------------------------------------------- admission

def test_speak_refused_when_muted(sp):
    monkeypatch_voice = sp.VOICE_ENABLED
    assert monkeypatch_voice is True
    sp.VOICE_ENABLED = False
    assert sp.speak("hello") is False
    assert sp._SPEECH_QUEUE.empty()
    sp.VOICE_ENABLED = True
    assert sp.speak("hello") is True
    assert _drain(sp._SPEECH_QUEUE) == ["hello"]


def test_speak_refused_when_setting_mutes_voice(sp, monkeypatch):
    monkeypatch.setattr(sp._config_silent, "get_setting",
                        lambda key, default=None: False
                        if key == "voice_enabled" else default)
    assert sp.speak("hello") is False
    assert sp._SPEECH_QUEUE.empty()


def test_speak_refused_when_app_locked(sp, monkeypatch):
    monkeypatch.setitem(sys.modules, "aria.applock",
                        types.SimpleNamespace(is_locked=lambda: True))
    assert sp.speak("hello") is False
    assert sp._SPEECH_QUEUE.empty()


def test_enqueue_speech_refused_when_muted_or_locked(sp, monkeypatch):
    sp.VOICE_ENABLED = False
    assert sp.enqueue_speech("busy reminder") is False
    assert sp._SPEECH_QUEUE.empty()
    sp.VOICE_ENABLED = True
    monkeypatch.setitem(sys.modules, "aria.applock",
                        types.SimpleNamespace(is_locked=lambda: True))
    assert sp.enqueue_speech("busy reminder") is False
    assert sp._SPEECH_QUEUE.empty()
    monkeypatch.setitem(sys.modules, "aria.applock",
                        types.SimpleNamespace(is_locked=lambda: False))
    assert sp.enqueue_speech("busy reminder") is True
    assert _drain(sp._SPEECH_QUEUE) == ["busy reminder"]


def test_queue_stream_chunk_now_checks_voice_enabled(sp):
    # Refinement: the old module-level chunk gate checked the app
    # lock but NOT VOICE_ENABLED — a muted session still streamed
    # sentences aloud.
    sp.VOICE_ENABLED = False
    assert sp.queue_stream_chunk("streamed sentence") is False
    assert sp._SPEECH_QUEUE.empty()
    sp.VOICE_ENABLED = True
    assert sp.queue_stream_chunk("streamed sentence") is True
    assert _drain(sp._SPEECH_QUEUE) == ["streamed sentence"]


def test_manager_methods_refuse_directly(sp):
    logs = []
    mgr = _manager(sp, logs)
    sp.VOICE_ENABLED = False
    assert mgr.speak("hello") is False
    assert mgr.queue_stream_chunk("chunk") is False
    assert mgr.speech_queue.empty()
    sp.VOICE_ENABLED = True
    assert mgr.speak("hello") is True
    assert mgr.queue_stream_chunk("chunk") is True
    assert _drain(mgr.speech_queue) == ["hello", "chunk"]


# ------------------------------------------------------- worker re-checks

def test_synth_worker_drops_after_mute(sp):
    logs = []
    mgr = _manager(sp, logs)
    t = threading.Thread(target=mgr._synth_worker, daemon=True)
    t.start()
    mgr.speech_queue.put("allowed sentence")
    assert _wait_until(lambda: not mgr.audio_play_queue.empty())
    assert mgr.audio_play_queue.get_nowait()[0] == "allowed sentence"
    mgr.audio_play_queue.task_done()
    # Policy flips AFTER the next sentence is enqueued: it must
    # never reach synthesis.
    sp.VOICE_ENABLED = False
    mgr.speech_queue.put("muted sentence")
    assert _wait_until(lambda: mgr.speech_queue.unfinished_tasks == 0)
    assert mgr.audio_play_queue.empty()
    assert any("held" in line for line in logs)


def test_play_worker_drops_after_lock(sp, monkeypatch):
    logs = []
    mgr = _manager(sp, logs)
    said = []

    class FakeSapi:
        def start(self):
            pass

        def say(self, text, whisper_mode=False):
            said.append(text)
            ev = threading.Event()
            ev.set()
            return ev

        def stop(self):
            pass

    mgr._sapi = FakeSapi()
    t = threading.Thread(target=mgr._play_worker, daemon=True)
    t.start()
    mgr.audio_play_queue.put(("allowed sentence", None, False))
    assert _wait_until(lambda: said == ["allowed sentence"])
    # Lock AFTER the next item is queued: no playback, no subtitle.
    monkeypatch.setitem(sys.modules, "aria.applock",
                        types.SimpleNamespace(is_locked=lambda: True))
    mgr.audio_play_queue.put(("locked sentence", None, False))
    assert _wait_until(lambda: mgr.audio_play_queue.unfinished_tasks == 0)
    assert said == ["allowed sentence"]
    assert any("held" in line for line in logs)


# ------------------------------------------------------- phone path exempt

def test_phone_audio_unaffected_by_mute_or_lock(sp, monkeypatch):
    # The bridge synthesizes bytes directly; it never consults the
    # speech gate — muting the laptop must not silence the phone.
    monkeypatch.setattr(sp, "_edge_tts_bytes_strict",
                        lambda text, whisper_mode=False, voice=None: b"ID3")
    sp.VOICE_ENABLED = False
    monkeypatch.setitem(sys.modules, "aria.applock",
                        types.SimpleNamespace(is_locked=lambda: True))
    assert sp.speak("hello") is False  # laptop gate still refuses
    audio, ctype = sp.tts_bytes_for_bridge("hello from the phone")
    assert audio == b"ID3" and ctype == "audio/mpeg"
