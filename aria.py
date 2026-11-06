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

# Small helper/diagnostic entry points must run before importing desktop dependencies.
if '--diagnostics' in sys.argv:
    from aria.diagnostics import main as _diagnostics
    sys.exit(_diagnostics())
if '--com-host' in sys.argv:
    from aria.comhost import main as _com_main
    sys.exit(_com_main())
if '--life-process' in sys.argv:
    _index = sys.argv.index('--life-process')
    sys.argv = [sys.argv[0]] + sys.argv[_index + 1:]
    from aria.life_process import run as _life_main
    sys.exit(_life_main(sys.argv[1] if len(sys.argv) > 1 else None))
if '--desktop-node' in sys.argv:
    from aria.desktop_node import main as _node_main
    sys.exit(_node_main(sys.argv[sys.argv.index('--desktop-node') + 1:]))
if '--run-script' in sys.argv:
    # Frozen-build script runner (pass 25): tool_run_python used
    # to pass a .py file to ARIA.exe, which ignored the argument
    # and booted a second ARIA instead of running the script. This
    # flag runs the file with the bundled interpreter and exits
    # with its code, before any of the app imports below — so the
    # frozen build can run code without a system Python.
    import runpy
    import traceback as _traceback
    _index = sys.argv.index('--run-script')
    _script = sys.argv[_index + 1] if len(sys.argv) > _index + 1 else None
    if not _script:
        print("aria.py: --run-script needs a script path",
              file=sys.stderr)
        sys.exit(2)
    try:
        runpy.run_path(_script, run_name="__main__")
    except SystemExit as _e:
        sys.exit(_e.code if isinstance(_e.code, int) else 0)
    except BaseException:
        _traceback.print_exc()
        sys.exit(1)
    sys.exit(0)

from aria import *
from aria.main import main, start_all, handle_action

if __name__ == "__main__":
    main()









# Code updated from GitHub (1790573379.6667154)


# Upgraded HUD: collapsible context tiles (waveforms, task chips, spotify) and cognitive avatar expressions



