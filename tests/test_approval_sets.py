"""Pass 26: face_tracking belongs in DESTRUCTIVE_TOOLS (approval
half of the face-tracking fix).

face_tracking drives the head servos (vision.py face_track_loop
calls send_servo_command directly), so it is a physical actuation
like move_head_servos — approval-gated under confirm-risky, not
just safe-mode-blocked (the SAFE_MODE set + loop re-check are the
companion agent's files; this test pins only the approval set).

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_approval_sets_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, 'ARIA')

import pytest

approval = importlib.import_module('aria.approval')
config = importlib.import_module('aria.config')
data_dir = importlib.import_module('aria.data_dir')


@pytest.fixture()
def state(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "ARIA").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(data_dir, "_ROOT", str(h / "ARIA"))
    monkeypatch.setattr(
        config, "add_log", lambda msg, *a, **k: None)
    approval.set_mode("confirm-risky")
    yield None
    approval.set_mode("auto")


def test_face_tracking_in_destructive_tools():
    assert "face_tracking" in approval.DESTRUCTIVE_TOOLS


def test_face_tracking_is_gated_under_confirm_risky(state):
    assert approval.needs_approval("face_tracking", {})
    # Control: a plain read tool stays ungated.
    assert not approval.needs_approval("phone_status", {})
