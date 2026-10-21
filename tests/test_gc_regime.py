"""GC-regime tests (crash round 4, 2026-10-21).

Evidence chain (three laptop faulthandler dumps, comtypes 1.2.0
verified installed throughout): ARIA's crash is a comtypes wrapper
finalised by the CYCLIC GC on a thread other than its owning COM
apartment -> Release() access violation. Round 2 patched creation
sites; witness round 3 still crashed — an MCP worker thread's first
`import requests` triggered a collection that finalised a pycaw
wrapper owned by the volume worker. Patching creation sites cannot
win: ANY thread's collection can finalise ANY orphaned wrapper.

The round-4 fix is a regime (aria/gcregime.py): automatic collection
OFF process-wide; collection performed ONLY by the two COM-owner
threads (volume worker, SAPI engine thread), in-apartment, on a
30 s wall-time timer. These tests pin:

  * install() collects once, then disables, and is idempotent;
  * the regime's core promise, with a poison-wrapper simulation: a
    cyclic object "owned" by a dedicated thread is NOT finalised by
    allocation pressure on other threads while the regime is in
    force, and IS finalised on the owner thread when the owner
    collects;
  * the contract's call-site list, statically: no gc.collect( /
    gc.enable( anywhere in aria/ outside gcregime.py, hud.py,
    speech.py;
  * install() placement: the aria.py launcher installs before
    `from aria import *`, and main() installs defensively.

Suite conventions: the aria package is stubbed in sys.modules so
aria.gcregime loads without executing aria/__init__; suites run in
per-file processes, and every test still restores global GC state
in a finally block.
"""
import gc
import os
import re
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
PKG = os.path.join(ROOT, "aria")

_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules.setdefault("aria", _pkg)

import importlib
gcregime = importlib.import_module("aria.gcregime")

_FLAG = "_aria_gc_regime_installed"


def _reset_regime():
    """Restore pre-install GC state (enabled, flag cleared, swept)."""
    if hasattr(gc, _FLAG):
        delattr(gc, _FLAG)
    gc.enable()
    gc.collect()


def test_a_install_disables_gc_and_is_idempotent():
    _reset_regime()
    try:
        assert gc.isenabled()
        assert gcregime.install() is True
        assert not gc.isenabled()
        assert gcregime.installed()
        # Second and third calls are no-ops, not errors, and do not
        # re-enable or re-install.
        assert gcregime.install() is False
        assert gcregime.install() is False
        assert not gc.isenabled()
    finally:
        _reset_regime()
    assert gc.isenabled()
    assert not gcregime.installed()


def test_b_install_collects_once_before_disabling():
    _reset_regime()
    real_collect = gc.collect
    calls = []

    def _recording_collect(*a, **k):
        calls.append((threading.get_ident(), gc.isenabled()))
        return real_collect(*a, **k)

    gc.collect = _recording_collect
    try:
        assert gcregime.install() is True
        # The startup sweep ran, and it ran BEFORE the disable —
        # collection was still enabled at sweep time.
        assert len(calls) == 1
        assert calls[0][1] is True
        assert not gc.isenabled()
    finally:
        gc.collect = real_collect
        _reset_regime()


class _Poison:
    """Stands in for a comtypes wrapper: records the thread its
    __del__ runs on. For a real wrapper, __del__ on a foreign thread
    is the access violation; here it is the assertion signal."""

    def __init__(self, calls):
        self.calls = calls
        self.self = None

    def __del__(self):
        self.calls.append(threading.get_ident())


def test_c_poison_cycle_finalised_only_on_owner_thread():
    _reset_regime()
    calls = []
    created = threading.Event()
    collect_now = threading.Event()
    collected = threading.Event()
    owner_idents = []

    def _owner():
        owner_idents.append(threading.get_ident())
        poison = _Poison(calls)
        poison.self = poison  # trapped in a reference cycle
        del poison  # the cycle is the only reference left
        created.set()
        if collect_now.wait(timeout=10):
            gc.collect()  # owner-thread collection, in-"apartment"
        collected.set()

    try:
        assert gcregime.install() is True
        owner = threading.Thread(target=_owner, daemon=True,
                                 name="poison-owner")
        owner.start()
        assert created.wait(timeout=5)

        # Foreign-thread pressure: heavy cyclic allocation on THIS
        # thread plus a second foreign thread. With automatic
        # collection off, none of it may finalise the owner's cycle —
        # this is exactly the MCP-thread `import requests` scenario
        # from dump #3, minus the native crash.
        def _pressure():
            junk = []
            for i in range(50000):
                a = [i]
                a.append(a)
                junk.append(a)
                if len(junk) > 64:
                    junk.clear()

        t = threading.Thread(target=_pressure, daemon=True)
        t.start()
        _pressure()
        t.join(timeout=10)
        time.sleep(0.1)
        assert calls == [], (
            f"poison __del__ ran under foreign pressure: {calls}")

        # The owner's own collection finalises it — on the owner.
        collect_now.set()
        assert collected.wait(timeout=5)
        owner.join(timeout=5)
        assert calls == owner_idents, (calls, owner_idents)
    finally:
        collect_now.set()
        _reset_regime()


_ALLOWED_COLLECT_FILES = {"gcregime.py", "hud.py", "speech.py"}


def test_d_no_gc_collect_or_enable_outside_owner_files():
    offenders = []
    for dirpath, _dirs, files in os.walk(PKG):
        for name in files:
            if not name.endswith(".py") or name in _ALLOWED_COLLECT_FILES:
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            for m in re.finditer(r"gc\.(collect|enable)\s*\(", src):
                offenders.append(f"{path}: gc.{m.group(1)}(")
    assert offenders == [], offenders
    # And the allowed files really do carry the regime: gcregime
    # disables, both owner threads collect.
    with open(os.path.join(PKG, "gcregime.py"), encoding="utf-8") as fh:
        assert "gc.disable()" in fh.read()
    for name in ("hud.py", "speech.py"):
        with open(os.path.join(PKG, name), encoding="utf-8") as fh:
            assert "gc.collect()" in fh.read(), name


def test_e_install_placement_launcher_and_main():
    with open(os.path.join(ROOT, "aria.py"), encoding="utf-8") as fh:
        launcher = fh.read()
    # The launcher references the regime before the package import
    # that pulls in every subsystem.
    assert launcher.index("gcregime") < launcher.index("from aria import *")
    with open(os.path.join(PKG, "main.py"), encoding="utf-8") as fh:
        main_src = fh.read()
    main_fn = main_src.index("def main():")
    segment = main_src[main_fn:main_fn + 3000]
    assert "_install_gc_regime()" in segment
    # main() installs BEFORE the start_all() statement spawns any
    # thread (match the statement, not comment mentions of it).
    start_stmt = re.search(r"(?m)^    start_all\(\)", segment)
    assert start_stmt is not None
    assert segment.index("_install_gc_regime()") < start_stmt.start()
