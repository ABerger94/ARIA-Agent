"""
A.R.I.A. (Adaptive Robotic Intelligence Agent) — Root OS Launcher.
Backward-compatible root entrypoint wrapping the modular `aria` package.
"""
# Restored interactive HUD directive typing and terminal text input

import sys
import os
import subprocess

# Optional alternate Python used to relaunch when PyAudio/pygame are missing
# (set ARIA_ALT_PYTHON to a full interpreter path). Defaults to no relaunch:
# the hardcoded machine-specific path this replaced only worked on one PC.
_ALT_PY = os.environ.get("ARIA_ALT_PYTHON", "")
if _ALT_PY and os.path.exists(_ALT_PY) and os.path.abspath(sys.executable).lower() != os.path.abspath(_ALT_PY).lower():
    try:
        import pyaudio
        import pygame
    except ImportError:
        subprocess.Popen([_ALT_PY] + sys.argv)
        sys.exit(0)

# Ensure package directory is on path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from aria import *
from aria.main import main, start_all, handle_action

if __name__ == "__main__":
    main()









# Code updated from GitHub (1790573379.6667154)


# Upgraded HUD: collapsible context tiles (waveforms, task chips, spotify) and cognitive avatar expressions



