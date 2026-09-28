"""
A.R.I.A. (Adaptive Robotic Intelligence Agent) — Root OS Launcher.
Backward-compatible root entrypoint wrapping the modular `aria` package.
"""
# Restored interactive HUD directive typing and terminal text input

import sys
import os

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
