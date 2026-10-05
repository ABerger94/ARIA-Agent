"""
A.R.I.A. (Adaptive Robotic Intelligence Agent) — Root OS Launcher.
Backward-compatible root entrypoint wrapping the modular `aria` package.
"""
# Restored interactive HUD directive typing and terminal text input

import sys
import os
import subprocess

# Relaunch under Python 3.9 if launched under an environment missing PyAudio/pygame
_PY39 = r"C:\Users\Allen\AppData\Local\Programs\Python\Python39\python.exe"
if os.path.exists(_PY39) and os.path.abspath(sys.executable).lower() != os.path.abspath(_PY39).lower():
    try:
        import pyaudio
        import pygame
    except ImportError:
        subprocess.Popen([_PY39] + sys.argv)
        sys.exit(0)

# Ensure package directory is on path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from aria import *
from aria.main import main, start_all, handle_action

if __name__ == "__main__":
    main()
# Restored text input bar, typing mode, and console input - 1790568581.162417

# Phone bridge fixed and restored - 1790568946.4906015

# Gemini Live removed & updated - 1790569409.1373053

# HUD update reload - 1790569713.8210106

# Reload to apply git pull updates - 1790570509.8665287

# Reload to apply git pull updates - 1790570595.6300135

# Code updated from github - 1790571743.48976

# Code pulled from github - 1790572397.087658

# Directive paste & send enabled - 1790573064.434058

# Code updated from GitHub (1790573379.6667154)
# Voice pathway fixed & continuous wake listener restored - 1790576480.3857312

# Theme changed to ocean - 1790577498.402519
# Phone bridge speech playback fix (dual HTML5/WebAudio + silent wav unlock + direct TTS response) - 1790620634.6790798
# STT phantom wake-word hallucination fix applied - 1790641499.1844068
