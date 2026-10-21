"""Self-edit tool tests (2026-10-21 fix package).

aria/tools/selfedit.py is the writer the brain's restart watcher
always needed: write_file is sandboxed to the workspace, so ARIA's
"code edits" never touched her source and the watcher's
validate/announce/restart path never fired. These tests pin:

  * the allowlist — only *.py inside the repo's aria/ package, the
    root aria.py launcher, and the SOUL file (config.SOUL_PATH, the
    exact path aria.agent.brain hashes); traversal, outside
    absolute paths, repo-root non-launcher files, and non-.py
    targets are all refused and write nothing;
  * edit_own_code / patch_own_code landing real writes, with
    patch refusing not-found and non-unique matches atomically;
  * the integration contract: a self-edit changes the hash that
    aria.agent.brain's _snapshot_hashes() computes for that file,
    which is what the post-turn restart check compares against
    _BOOT_HASHES.

Suite conventions: the aria package is stub-bypassed; config path
roots are redirected at a temp repo layout for every test.
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
    """A temp repo layout mirroring production: aria/ package,
    aria.py launcher, soul.md, plus tempting off-limits files."""
    root = tempfile.mkdtemp(prefix="aria_selfedit_")
    os.makedirs(os.path.join(root, "aria", "tools"))
    os.makedirs(os.path.join(root, "workspace"))
    files = {
        os.path.join(root, "aria", "__init__.py"): "",
        os.path.join(root, "aria", "mod.py"): "VALUE = 1\n",
        os.path.join(root, "aria", "tools", "thing.py"): "X = 'a'\nX = 'a'\n",
        os.path.join(root, "aria", "data.txt"): "not code\n",
        os.path.join(root, "aria.py"): "# launcher\n",
        os.path.join(root, "soul.md"): "# soul\n",
        os.path.join(root, "evil.py"): "# repo root, not the launcher\n",
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


# ------------------------------------------------------------ allowlist
def test_a_refusals_write_nothing():
    root = _layout()
    try:
        mod = os.path.join(root, "aria", "mod.py")
        before = _read(mod)
        refused = [
            "../outside.py",                       # traversal
            "aria/../../outside.py",               # traversal, dressed up
            os.path.join(root, "..", "outside.py"),  # absolute, outside
            "/etc/passwd.py",                      # absolute, outside
            "evil.py",                             # repo root != launcher
            "aria/data.txt",                       # not a .py target
            "workspace/file.py",                   # outside the package
            "tests/test_x.py",                     # outside the package
            "",                                    # empty
        ]
        for bad in refused:
            out = selfedit.tool_edit_own_code(bad, "HACKED = True\n")
            assert out.startswith("[Self-edit refused"), (bad, out)
            out = selfedit.tool_patch_own_code(bad, "VALUE = 1", "VALUE = 2")
            assert out.startswith("[Self-edit refused"), (bad, out)
        assert _read(mod) == before
        assert not os.path.exists(os.path.join(root, "outside.py"))
    finally:
        _restore()


def test_b_edit_writes_allowlisted_targets():
    root = _layout()
    try:
        out = selfedit.tool_edit_own_code("aria/mod.py", "VALUE = 2\n")
        assert "Rewrote" in out and "restart" in out, out
        assert _read(os.path.join(root, "aria", "mod.py")) == "VALUE = 2\n"
        out = selfedit.tool_edit_own_code("aria.py", "# launcher v2\n")
        assert "Rewrote" in out, out
        out = selfedit.tool_edit_own_code("soul.md", "# soul v2\n")
        assert "Rewrote" in out, out
        assert _read(os.path.join(root, "soul.md")) == "# soul v2\n"
        # New file inside the package is fine (watcher picks it up).
        out = selfedit.tool_edit_own_code("aria/newmod.py", "NEW = 1\n")
        assert "Rewrote" in out, out
        assert _read(os.path.join(root, "aria", "newmod.py")) == "NEW = 1\n"
        # Windows-style separators resolve the same way.
        out = selfedit.tool_patch_own_code(
            "aria\\mod.py", "VALUE = 2", "VALUE = 3")
        assert "Patched" in out, out
    finally:
        _restore()


def test_c_patch_exact_match_rules():
    root = _layout()
    try:
        thing = os.path.join(root, "aria", "tools", "thing.py")
        # Not found -> refused, file untouched.
        out = selfedit.tool_patch_own_code(
            "aria/tools/thing.py", "NOPE", "YEP")
        assert "not found" in out, out
        assert _read(thing) == "X = 'a'\nX = 'a'\n"
        # Non-unique -> refused, file untouched.
        out = selfedit.tool_patch_own_code(
            "aria/tools/thing.py", "X = 'a'", "X = 'b'")
        assert "matches 2 places" in out, out
        assert _read(thing) == "X = 'a'\nX = 'a'\n"
        # Unique with context -> lands.
        out = selfedit.tool_patch_own_code(
            "aria/tools/thing.py", "X = 'a'\nX = 'a'", "X = 'b'\nX = 'a'")
        assert "Patched" in out and "restart" in out, out
        assert _read(thing) == "X = 'b'\nX = 'a'\n"
        # Patch on a missing file -> clean failure, not a crash.
        out = selfedit.tool_patch_own_code("aria/ghost.py", "a", "b")
        assert "does not exist" in out, out
        # Empty old_text -> clean failure.
        out = selfedit.tool_patch_own_code("aria/mod.py", "", "b")
        assert "old_text is empty" in out, out
    finally:
        _restore()


# --------------------------------------------- brain-watcher contract
def test_d_selfedit_changes_brain_snapshot_hash():
    """The post-turn restart check compares _snapshot_hashes()
    against _BOOT_HASHES (aria/agent/brain.py). A self-edit must
    move exactly that hash for exactly that file."""
    # Headless stub set (tests/test_headless.py conventions) so the
    # real brain module imports without cv2/mic/TTS.
    for name in ("aria.scheduler", "aria.vision", "aria.hardware",
                 "aria.spotify", "aria.hud"):
        sys.modules[name] = types.ModuleType(name)
    # aria.agent.shortcuts imports these two at module level.
    sys.modules["aria.spotify"].tool_media_key = (
        lambda action: "stub")
    sys.modules["aria.spotify"].tool_spotify = (
        lambda action, query="": "stub")
    sys.modules["aria.hud"].tool_show_commands = lambda: "ok"
    sys.modules["aria.hud"].tool_hide_commands = lambda: "ok"
    sys.modules["aria.hud"].set_hud_state = lambda *a, **k: None
    sys.modules["aria.hud"].draw_hud = lambda *a, **k: None
    sys.modules["aria.hud"].set_hud_subtitle = lambda *a, **k: None
    _speech = types.ModuleType("aria.speech")
    _speech.speak = lambda t: None
    _speech.edge_tts_bytes = lambda text, whisper_mode=False: b""
    _speech.queue_stream_chunk = lambda s: None
    _speech.extract_sentences = lambda buf, first_clause=False: ([], buf)
    _speech._SPEECH_STOP = types.SimpleNamespace(is_set=lambda: False)
    _speech._SPEECH_QUEUE = types.SimpleNamespace(unfinished_tasks=0)
    _speech._AUDIO_PLAY_QUEUE = types.SimpleNamespace(unfinished_tasks=0)
    sys.modules["aria.speech"] = _speech
    brain = importlib.import_module("aria.agent.brain")

    root = _layout()
    try:
        brain._repo_dir = lambda: root
        boot = brain._snapshot_hashes()
        target = os.path.join(root, "aria", "mod.py")
        assert target in boot and boot[target] is not None
        out = selfedit.tool_patch_own_code(
            "aria/mod.py", "VALUE = 1", "VALUE = 42")
        assert "Patched" in out, out
        cur = brain._snapshot_hashes()
        assert cur[target] != boot[target], \
            "self-edit did not move the brain watcher's hash"
        others = [p for p in cur
                  if p != target and cur[p] != boot.get(p)]
        assert others == [], others
    finally:
        _restore()
