"""Pass 29 #49: self-edit writes must not race between writers.

Verified against the code at 820f2c3:
- _atomic_write used a fixed `f"{path}.tmp-selfedit"` temp name:
  every concurrent writer of the same target shared it, so two
  writers interleaved content in one temp file and the writer
  whose temp had already been os.replace'd away got
  FileNotFoundError on its own replace;
- tool_patch_own_code read the file, validated, then wrote the
  stale content it had read — a change landing in between was
  silently overwritten (lost update), with no conflict reported.

Fix contract pinned here:
- each write uses a unique same-directory temp file
  (tempfile.mkstemp) + os.replace, and a per-target lock is held
  across read-validate-write;
- the file is re-read just before writing: a change since the
  read is refused as an explicit stale-edit conflict and the
  newer content survives.
"""
import importlib
import os
import sys
import tempfile
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")


def _pkg(name, path):
    m = types.ModuleType(name)
    m.__path__ = [path]
    sys.modules[name] = m
    return m


_pkg("aria", PKG)
_pkg("aria.tools", os.path.join(PKG, "tools"))
_pkg("aria.agent", os.path.join(PKG, "agent"))
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

config = importlib.import_module("aria.config")
selfedit = importlib.import_module("aria.tools.selfedit")

_REAL_ROOT = config.ROOT_DIR
_REAL_SOUL = config.SOUL_PATH


def _layout():
    root = tempfile.mkdtemp(prefix="aria_selfedit_race_")
    os.makedirs(os.path.join(root, "aria", "tools"))
    files = {
        os.path.join(root, "aria", "__init__.py"): "",
        os.path.join(root, "aria", "mod.py"): "VALUE = 1\n",
        os.path.join(root, "aria.py"): "# launcher\n",
        os.path.join(root, "soul.md"): "# soul\n",
    }
    for path, text in files.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    config.ROOT_DIR = root
    config.SOUL_PATH = os.path.join(root, "soul.md")
    return root


def _restore():
    config.ROOT_DIR = _REAL_ROOT
    config.SOUL_PATH = _REAL_SOUL


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_concurrent_writers_each_land_a_complete_file():
    root = _layout()
    try:
        target = os.path.join(root, "aria", "shared.py")
        n = 8
        contents = [
            f"# writer {i}\nVALUE = {i}\n" + "# filler line\n" * 500
            for i in range(n)
        ]
        barrier = threading.Barrier(n)
        results = [None] * n

        def _write(i):
            barrier.wait(timeout=30)
            results[i] = selfedit.tool_edit_own_code(
                "aria/shared.py", contents[i])

        threads = [threading.Thread(target=_write, args=(i,))
                   for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not any(t.is_alive() for t in threads), "writer hung"
        for i, out in enumerate(results):
            assert out.startswith("Rewrote"), (i, out)
            assert "FileNotFoundError" not in out, (i, out)
        # Exactly one writer's complete content won — no interleave.
        assert _read(target) in contents
        # No temp files left behind.
        leftovers = [name for name in os.listdir(
            os.path.join(root, "aria"))
            if name.startswith(".selfedit-")
            or name.endswith(".tmp-selfedit")]
        assert leftovers == [], leftovers
    finally:
        _restore()


def test_patch_against_changed_base_reports_conflict():
    root = _layout()
    try:
        mod = os.path.join(root, "aria", "mod.py")
        external = "VALUE = 99\n# landed while the patch was prepared\n"
        real_validate = selfedit._validate_new_source

        def _mutating_validate(target, current, new):
            # Simulate another writer landing between the patch's
            # read and its write: the validation pause point.
            with open(target, "w", encoding="utf-8") as f:
                f.write(external)
            return real_validate(target, current, new)

        selfedit._validate_new_source = _mutating_validate
        try:
            out = selfedit.tool_patch_own_code(
                "aria/mod.py", "VALUE = 1", "VALUE = 2")
        finally:
            selfedit._validate_new_source = real_validate
        assert "failed" in out.lower(), out
        assert "changed" in out and "stale" in out, out
        # The newer content survives; the stale patch did not land.
        assert _read(mod) == external
    finally:
        _restore()


def test_edit_against_changed_base_reports_conflict():
    root = _layout()
    try:
        mod = os.path.join(root, "aria", "mod.py")
        external = "VALUE = 77\n# landed while the edit was prepared\n"
        real_validate = selfedit._validate_new_source

        def _mutating_validate(target, current, new):
            with open(target, "w", encoding="utf-8") as f:
                f.write(external)
            return real_validate(target, current, new)

        selfedit._validate_new_source = _mutating_validate
        try:
            out = selfedit.tool_edit_own_code(
                "aria/mod.py", "VALUE = 2\n")
        finally:
            selfedit._validate_new_source = real_validate
        assert "failed" in out.lower(), out
        assert "changed" in out and "stale" in out, out
        assert _read(mod) == external
    finally:
        _restore()


def test_normal_patch_and_edit_still_land():
    root = _layout()
    try:
        out = selfedit.tool_patch_own_code(
            "aria/mod.py", "VALUE = 1", "VALUE = 42")
        assert out.startswith("Patched"), out
        assert _read(os.path.join(root, "aria", "mod.py")) == "VALUE = 42\n"
        out = selfedit.tool_edit_own_code(
            "aria/mod.py", "VALUE = 43\n")
        assert out.startswith("Rewrote"), out
        assert _read(os.path.join(root, "aria", "mod.py")) == "VALUE = 43\n"
    finally:
        _restore()
