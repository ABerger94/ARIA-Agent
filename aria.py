"""
A.R.I.A. (Adaptive Robotic Intelligence Agent) — Root OS Launcher.
Backward-compatible root entrypoint wrapping the modular `aria` package.
"""
# Restored interactive HUD directive typing and terminal text input

import sys
import os
import subprocess

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

# GC regime (crash round 4, aria/gcregime.py): install BEFORE any
# ARIA subsystem import takes effect — automatic cyclic collection
# must be off before the first COM wrapper can exist. A normal
# `import aria.gcregime` would execute aria/__init__ first (it
# imports hud/speech/...), so load the file directly and register
# it under its package name; later package imports reuse this exact
# module object. If this bootstrap ever fails, aria.main.main()
# installs the regime defensively before any thread starts.
try:
    import importlib.util as _ilu
    _gc_spec = _ilu.spec_from_file_location(
        "aria.gcregime", os.path.join(_ROOT, "aria", "gcregime.py"))
    _gc_mod = _ilu.module_from_spec(_gc_spec)
    _gc_spec.loader.exec_module(_gc_mod)
    sys.modules.setdefault("aria.gcregime", _gc_mod)
    _gc_mod.install()
    del _ilu, _gc_spec, _gc_mod
except Exception:
    pass

from aria import *
from aria.main import main, start_all, handle_action

if __name__ == "__main__":
    main()









# Code updated from GitHub (1790573379.6667154)


# Upgraded HUD: collapsible context tiles (waveforms, task chips, spotify) and cognitive avatar expressions



