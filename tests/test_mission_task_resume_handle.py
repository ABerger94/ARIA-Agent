"""Pass 28 #19 (mission half): a task_resume STARTED result must
parse as a delegated-task child handle, so a mission step that
resumes a blocked child waits on the resumed task instead of
certifying the submission — or stranding the step as uncertain.
"""
import http.client  # noqa: F401  (mirrors the missions harness imports)
import importlib
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pkg = types.ModuleType("aria")
pkg.__path__ = [str(Path(__file__).parents[1] / "aria")]
sys.modules.setdefault("aria", pkg)

for _name in ("aria.scheduler", "aria.hud", "aria.frame",
              "aria.hardware", "aria.spotify",
              "aria.agent.taskqueue"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

_vision = types.ModuleType("aria.vision")
_vision.get_face_frame_jpeg = lambda: None
_vision.publish_phone_frame = lambda jpeg: True
_vision.get_phone_frame_jpeg = lambda: None
_vision.get_phone_frame_status = lambda: {"active": False,
                                          "age_s": -1.0}
_vision.describe_phone_view = lambda q="": "[stubbed]"
sys.modules.setdefault("aria.vision", _vision)
_speech = types.ModuleType("aria.speech")
_speech.speak = lambda t: None
_speech.tts_bytes_for_bridge = lambda text: (b"", "no speech")
_speech.transcribe_audio = lambda audio, mime: ""
_speech._SPEECH_QUEUE = types.SimpleNamespace(
    unfinished_tasks=0, put=lambda t: None)
sys.modules.setdefault("aria.speech", _speech)

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
data_dir = importlib.import_module("aria.data_dir")
missions = importlib.import_module("aria.missions")
outcome = importlib.import_module("aria.outcome")


def test_task_resume_started_parses_as_delegated_child():
    started = outcome.started(
        "task_resume", task_id="abc12345", resumed_from="old1",
        project="default", skipped=1)
    kind, handle = missions._parse_child_handle("task_resume", started)
    assert kind == "delegated_task", (kind, handle)
    assert handle["task_id"] == "abc12345", handle
    assert handle["project"] == "default", handle
    # And delegate_task still parses as before.
    kind2, handle2 = missions._parse_child_handle(
        "delegate_task",
        outcome.started("delegate_task", task_id="zz99",
                        project="default", goal="x"))
    assert kind2 == "delegated_task" and handle2["task_id"] == "zz99"
