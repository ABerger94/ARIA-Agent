"""Actual supported-platform dependencies; no sys.modules subsystem stubs."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows product acceptance')


def test_clean_native_startup_and_com_helper(tmp_path):
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, str(root / 'aria.py'), '--diagnostics'], cwd=root,
        env={**os.environ,'ARIA_DATA_ROOT':str(tmp_path),'QT_QPA_PLATFORM':'offscreen'},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90)
    assert run.returncode == 0, run.stdout
    assert '"native_dashboard": true' in run.stdout
    assert '"com_helper": true' in run.stdout


def test_windows_runtime_dependencies_import():
    import cv2
    import numpy
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from aria import speech
    assert cv2.__version__ and numpy.__version__ and QWebEngineView
    assert callable(speech.interrupt_speech)
