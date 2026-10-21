"""Process-wide garbage-collection regime (crash round 4, 2026-10-21).

WHY THIS MODULE EXISTS — the three-dump evidence chain
------------------------------------------------------
ARIA died three times on the user's laptop with the same native
fault: a Windows access violation inside comtypes' ``Release()``,
called from a comtypes wrapper's ``__del__``, called from a cyclic
garbage collection running on a thread that did NOT own the wrapper's
COM apartment (comtypes 1.2.0 was verified installed throughout —
this was never a version problem):

  * Dump #1 (pre-merger code): the main thread collected during a
    HUD render and finalised a volume-manager (pycaw) wrapper.
  * Dump #2 (COM-containment hotfix live): the SAPI thread died
    inside ``pyttsx3.init()`` itself — comtypes generates its
    SpeechLib type library at engine init, and a collection
    finalised a generation temporary mid-generation; a second fault
    hit the main thread during a render-path collection.
  * Dump #3 (round 3, lazy SAPI init, live): the SAPI thread sat
    idle and innocent — an MCP worker thread's first
    ``import requests`` triggered a collection that finalised a
    pycaw wrapper owned by the volume worker's apartment.

The lesson of dump #3 is the one this module encodes: patching
creation sites cannot win. Reference cycles are invisible, they form
wherever frames, callbacks, and tracebacks touch a wrapper, and ANY
thread's automatic collection can finalise ANY orphaned wrapper.
Python's cyclic GC is process-wide and thread-agnostic; COM
apartments are neither.

THE REGIME
----------
``install()`` performs one collection immediately — at install time
the process is at startup, no COM wrapper can exist yet, so
import/startup cycles are reaped safely by refcounting plus this
sweep — and then DISABLES automatic cyclic collection process-wide
(``gc.disable()``). From that moment:

  * Plain (non-cyclic) objects are still freed promptly by
    refcounting, exactly as before; only cycle reaping changes.
  * Cycles are collected ONLY by the two COM-owner threads, on
    their own threads, in their owning apartments, on a timer:
    the VolumeManager worker (aria/hud.py) and the SAPI engine
    thread (aria/speech.py ``_SapiVoice``). Each also collects once
    more at teardown / immediately after engine creation.

THE CONTRACT (for future code)
------------------------------
  * Never call ``gc.collect()`` outside gcregime.install() and the
    two owner threads named above, and never call ``gc.enable()``
    anywhere. A static test (tests/test_gc_regime.py) enforces the
    call-site list.
  * Any FUTURE COM consumer must own its objects on ONE dedicated
    thread, CoInitialize there, and collect on that same thread on
    a timer — or not ship.
  * Memory trade-off, stated plainly: cycles that hold no COM
    wrappers now wait for an owner thread's timer instead of being
    reaped immediately. The owner cadence is 30 s, the cycles are
    small, and the alternative is a native crash. Accepted.

Idempotence: install() is safe to call any number of times, from any
import path. The installed flag lives on the ``gc`` module itself
(not on this module) because aria.py loads this file directly,
before the ``aria`` package exists, and aria.main imports it again
through the package — two module objects, one regime. The flag is
the shared truth.
"""

from __future__ import annotations

import gc

_FLAG = "_aria_gc_regime_installed"


def install() -> bool:
    """Install the GC regime. Returns True on the call that actually
    installs it, False on every subsequent (no-op) call.

    Order matters: collect FIRST, while no COM wrapper can exist,
    then disable. Disabling first would strand the startup cycles
    until an owner thread's first timer tick — harmless but untidy,
    and collecting first is free safety.
    """
    if getattr(gc, _FLAG, False):
        return False
    try:
        gc.collect()
    except Exception:
        pass  # a failed startup sweep must never block the boot
    gc.disable()
    setattr(gc, _FLAG, True)
    return True


def installed() -> bool:
    """True once install() has run (from any import path)."""
    return bool(getattr(gc, _FLAG, False))
