"""Pass 29 #48: new self-edit files must compile before writing.

Verified against the code at 820f2c3:
- tool_edit_own_code gated ALL validation on `current is not
  None` — for a nonexistent target the shrinkage guard and
  _validate_new_source (compile check) were both skipped, so a
  new .py file with invalid syntax was written with a success
  message ("Rewrote ... (N characters)."), and the restart
  watcher cannot restore an untracked file from git.

Fix contract pinned here:
- a new .py file whose content does not compile is refused and
  nothing is written;
- a new .py file with valid syntax is still written;
- existing-file behaviour is unchanged (shrink guard,
  dropped-definition and compile checks still apply).
"""
import importlib
import os
import sys
import tempfile
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
    root = tempfile.mkdtemp(prefix="aria_selfedit_newfile_")
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


def test_new_file_with_invalid_syntax_is_refused_and_unwritten():
    root = _layout()
    try:
        target = os.path.join(root, "aria", "newbad.py")
        out = selfedit.tool_edit_own_code(
            "aria/newbad.py", "def broken(:\n    return 1\n")
        assert "refused" in out.lower(), out
        assert "does not compile" in out, out
        assert not os.path.exists(target), "invalid file was written"
        # The launcher is a .py target too: same refusal.
        out = selfedit.tool_edit_own_code(
            "aria/newbad2.py", "x = \n")
        assert "does not compile" in out, out
        assert not os.path.exists(
            os.path.join(root, "aria", "newbad2.py"))
    finally:
        _restore()


def test_new_file_with_valid_syntax_still_written():
    root = _layout()
    try:
        out = selfedit.tool_edit_own_code(
            "aria/newgood.py", "def hello():\n    return 42\n")
        assert out.startswith("Rewrote"), out
        assert _read(os.path.join(root, "aria", "newgood.py")) == (
            "def hello():\n    return 42\n")
        # Empty content compiles (nothing to break).
        out = selfedit.tool_edit_own_code("aria/empty.py", "")
        assert out.startswith("Rewrote"), out
        assert _read(os.path.join(root, "aria", "empty.py")) == ""
    finally:
        _restore()


def test_new_non_py_targets_unaffected():
    root = _layout()
    try:
        # SOUL is not a .py target: no compile gate applies.
        out = selfedit.tool_edit_own_code("soul.md", "# soul v2\n")
        assert out.startswith("Rewrote"), out
        assert _read(os.path.join(root, "soul.md")) == "# soul v2\n"
    finally:
        _restore()


def test_existing_file_behaviour_unchanged():
    root = _layout()
    try:
        mod = os.path.join(root, "aria", "mod.py")
        # Bad-syntax rewrite of an existing file: still refused.
        out = selfedit.tool_edit_own_code(
            "aria/mod.py", "def broken(:\n")
        assert "does not compile" in out, out
        assert _read(mod) == "VALUE = 1\n"
        # Good rewrite of an existing file: still lands.
        out = selfedit.tool_edit_own_code("aria/mod.py", "VALUE = 2\n")
        assert out.startswith("Rewrote"), out
        assert _read(mod) == "VALUE = 2\n"
    finally:
        _restore()
