"""File Commander symlink/root crossings (pass-16 runner-up,
closed in pass 18).

Verified mechanisms:
- The dispatcher validates only the top-level `directory`
  argument against the configured file_roots; the recursive
  readers (file_find_advanced / file_duplicates / disk_usage)
  then walked THROUGH symlinks, reading and reporting files
  outside the roots (find could even content-search them).
- file_organize's category folders are joined onto the
  approved directory unchecked: a category folder that is a
  symlink elsewhere redirected real moves outside it.

Fixes pinned here:
- each reader consults workspaces.file_allowed per file
  (a no-op when no roots are configured — legacy behaviour,
  pinned by the last test);
- organize skips (and reports) any move whose category folder
  resolves outside the directory being organized.

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_fileops_symlink_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir_v20
_data_dir_v20._ROOT = os.path.join(_TMPHOME, 'ARIA')

tools_pkg = types.ModuleType('aria.tools')
tools_pkg.__path__ = [PKG + '/tools']
sys.modules['aria.tools'] = tools_pkg
agent_pkg = types.ModuleType('aria.agent')
agent_pkg.__path__ = [PKG + '/agent']
sys.modules['aria.agent'] = agent_pkg
_ps = types.ModuleType('psutil')
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules['psutil'] = _ps
for _name in ('aria.scheduler', 'aria.vision', 'aria.hardware',
              'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)
sys.modules['aria.vision'].get_face_frame_jpeg = lambda: None
sys.modules['aria.hud'].tool_show_commands = lambda: 'ok'

import pytest

from aria import workspaces
from aria.tools import fileops


def _tree(tmp):
    """inside/ (scanned) with a real file; outside/ with a
    secret; inside/leak.txt is a symlink to the secret."""
    inside = tmp / "inside"
    outside = tmp / "outside"
    inside.mkdir()
    outside.mkdir()
    (inside / "real.txt").write_text("ordinary contents")
    secret = outside / "secret.txt"
    secret.write_text("topsecret payload")
    os.symlink(secret, inside / "leak.txt")
    return inside, outside, secret


def _roots_only(root):
    root = os.path.realpath(str(root))
    return lambda p: os.path.realpath(str(p)).startswith(root + os.sep)


def test_readers_do_not_cross_roots_through_symlinks(tmp_path,
                                                     monkeypatch):
    inside, _outside, _secret = _tree(tmp_path)
    monkeypatch.setattr(workspaces, "file_allowed",
                        _roots_only(inside))
    out = fileops.tool_file_find_advanced(
        str(inside), content_contains="topsecret")
    assert "Found 0 match(es)" in out
    out = fileops.tool_file_duplicates(str(inside))
    assert "No duplicates found" in out
    out = fileops.tool_disk_usage(str(inside))
    assert "across 1 files" in out  # only real.txt counted


def test_organize_never_moves_through_a_symlinked_category(tmp_path):
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    inside.mkdir()
    outside.mkdir()
    (inside / "note.txt").write_text("hello")
    os.symlink(outside, inside / "Documents")  # .txt -> Documents
    out = fileops.tool_file_organize(str(inside), dry_run=False)
    assert (inside / "note.txt").exists()       # not moved
    assert list(outside.iterdir()) == []        # nothing escaped
    assert "symlink" in out                     # and it says why


def test_no_roots_configured_keeps_legacy_behaviour(tmp_path):
    # workspaces.file_allowed with an empty roots list is always
    # True, so the per-file check is a no-op and the readers see
    # through the symlink exactly as before the fix. This pins
    # the pass-16 qualifier: the crossing only bites when a
    # roots policy exists.
    inside, _outside, _secret = _tree(tmp_path)
    assert workspaces.file_allowed(str(inside / "leak.txt")) is True
    out = fileops.tool_file_find_advanced(
        str(inside), content_contains="topsecret")
    assert "Found 1 match(es)" in out
