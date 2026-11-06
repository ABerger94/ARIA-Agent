"""Staged-image fallback must obey configured file roots (pass 26 B5#9).

vision_look's FILE_ARGUMENTS entry only lets dispatch enforce roots
for a SUPPLIED image_path; _first_look_image fell back to the frame's
staged file and read it with a bare open(), so a staged file outside
the configured roots could be read by the model. The fix applies
workspaces.file_allowed() to the resolved path (explicit AND staged)
before opening it.

Run per-file:  python3 -m pytest tests/test_staged_image_roots.py -v
"""
import base64
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_staged_roots_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, "ARIA")

tools_pkg = types.ModuleType("aria.tools")
tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = tools_pkg
agent_pkg = types.ModuleType("aria.agent")
agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = agent_pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules["psutil"] = _ps
sys.modules["cv2"] = types.ModuleType("cv2")
_hw = types.ModuleType("aria.hardware")
_hw.send_servo_command = lambda *a, **k: None
_hw.SERVO_POS = {"pan": 90, "tilt": 45}
sys.modules["aria.hardware"] = _hw
sys.modules["aria.agent.brain"] = types.ModuleType("aria.agent.brain")

import importlib
from pathlib import Path

config = importlib.import_module("aria.config")
workspaces = importlib.import_module("aria.workspaces")
vision = importlib.import_module("aria.vision")

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")

# Same shape dispatch.execute_tool returns (curly apostrophe, as in dispatch.py).
REFUSAL = "status=failed reason=\"File is outside this project\u2019s configured roots\""

_TMP = Path(tempfile.mkdtemp(prefix="aria_staged_roots_tree_", dir=_TMPHOME))
ALLOWED = _TMP / "allowed"
OUTSIDE = _TMP / "outside"
ALLOWED.mkdir()
OUTSIDE.mkdir()
(ALLOWED / "ok.png").write_bytes(PNG_1X1)
(OUTSIDE / "secret.png").write_bytes(PNG_1X1)


class FakePool:
    def __init__(self, text="A one-pixel image."):
        self._text = text
        self.calls = []

    def available(self):
        return True

    def vision(self, prompt, image_b64, mime="image/png", system=None,
               model=None, max_tokens=1024):
        self.calls.append({"prompt": prompt, "b64": image_b64, "mime": mime})
        return self._text


def _stage(path):
    """Point vision's frame import at a fake frame staging `path`."""
    fake_frame = types.ModuleType("aria.frame")
    fake_frame.staged_file = lambda: path
    sys.modules["aria.frame"] = fake_frame
    sys.modules["aria"].frame = fake_frame


def _set_roots(roots):
    workspaces.update_settings({"file_roots": [str(r) for r in roots]})


def test_staged_file_outside_roots_is_refused():
    _set_roots([ALLOWED])
    _stage(str(OUTSIDE / "secret.png"))
    pool = FakePool()
    out = vision.tool_vision_look("what is this?", pool=pool)
    assert out == REFUSAL, out
    assert pool.calls == [], "refused image must never reach the vision pool"


def test_staged_file_inside_roots_still_works():
    _set_roots([ALLOWED])
    _stage(str(ALLOWED / "ok.png"))
    pool = FakePool()
    out = vision.tool_vision_look("what is this?", pool=pool)
    assert out == "A one-pixel image.", out
    assert pool.calls[0]["mime"] == "image/png"


def test_explicit_path_outside_roots_is_refused():
    _set_roots([ALLOWED])
    _stage(str(ALLOWED / "ok.png"))
    pool = FakePool()
    out = vision.tool_vision_look("what is this?", str(OUTSIDE / "secret.png"), pool=pool)
    assert out == REFUSAL, out
    assert pool.calls == [], "refused image must never reach the vision pool"


def test_no_roots_configured_keeps_legacy_access():
    _set_roots([])
    _stage(str(OUTSIDE / "secret.png"))
    pool = FakePool()
    out = vision.tool_vision_look("what is this?", pool=pool)
    assert out == "A one-pixel image.", out
    assert len(pool.calls) == 1
