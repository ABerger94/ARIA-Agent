"""
A.R.I.A. (Adaptive Robotic Intelligence Agent) — Root OS Launcher.
Backward-compatible root entrypoint wrapping the modular `aria` package.
"""
# Restored interactive HUD directive typing and terminal text input

import sys
import os
import subprocess

# Round-5 follow-up (2026-10-21 session log): stdout/stderr must be
# line-buffered from the very start. ARIA is run with output
# redirected to a file, and Python block-buffers redirected streams —
# in the captured session the process's whole conversation/tool
# output was still sitting in the buffer when the session ended and
# the log held only stderr noise. Guarded: some stream objects
# (embedded interpreters, IDEs) don't support reconfigure.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)
    except Exception:
        pass

# Optional alternate Python used to relaunch when pygame is missing
# (set ARIA_ALT_PYTHON to a full interpreter path). Defaults to no relaunch:
# the hardcoded machine-specific path this replaced only worked on one PC.
_ALT_PY = os.environ.get("ARIA_ALT_PYTHON", "")
if _ALT_PY and os.path.exists(_ALT_PY) and os.path.abspath(sys.executable).lower() != os.path.abspath(_ALT_PY).lower():
    try:
        import pygame
    except ImportError:
        subprocess.Popen([_ALT_PY] + sys.argv)
        sys.exit(0)

# Ensure package directory is on path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Crash net (2026-10-27): a fatal-class crash at startup on
# Alek's laptop surfaced only as a bare exception object dump —
# the real traceback was lost with stderr. faulthandler now
# writes every thread's stack to faulthandler.log in the install
# root on a fatal error, so a recurrence leaves evidence behind.
try:
    import faulthandler as _faulthandler
    _FH_FILE = open(os.path.join(_ROOT, "faulthandler.log"), "a", buffering=1)
    _faulthandler.enable(file=_FH_FILE, all_threads=True)
except Exception:
    pass

from aria import *
from aria.main import main, start_all, handle_action

if __name__ == "__main__":
    main()









# Code updated from GitHub (1790573379.6667154)


# Upgraded HUD: collapsible context tiles (waveforms, task chips, spotify) and cognitive avatar expressions



