"""Pass 29 #51: file_organize must claim the destination
atomically.

Verified against the code at 820f2c3:
- the execute loop re-checked `os.path.exists(dest)` and then
  called shutil.move(src, dest) as two separate operations.
  The shipped mitigations are real (plan-time _unique_dest
  reservation + the move-time re-check), so a destination
  created after planning is now caught — but only a destination
  created in the microsecond window between the :262 check and
  the :267 move still gets overwritten (POSIX rename /
  shutil.move copy overwrites).

Fix contract pinned here:
- the move claims the destination atomically (os.link +
  unlink on the same filesystem; exclusive-create placeholder
  + copy + os.replace over our own placeholder across
  devices) instead of check-then-move;
- a destination that appears in that window is never
  overwritten: the source is preserved and the skip is
  reported;
- normal moves still work, including the cross-device
  fallback.
"""
import errno
import os
import shutil
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_fileops_atomic_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
from aria import data_dir as _data_dir_v20  # noqa: E402
_data_dir_v20._ROOT = os.path.join(_TMPHOME, "ARIA")

tools_pkg = types.ModuleType("aria.tools")
tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = tools_pkg
agent_pkg = types.ModuleType("aria.agent")
agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = agent_pkg

from aria.tools import fileops  # noqa: E402


def _setup(tmp_path):
    d = tmp_path / "inbox"
    docs = d / "Documents"
    docs.mkdir(parents=True)
    (d / "a.txt").write_text("source-a")
    return d, docs


def test_destination_created_between_check_and_move_survives(tmp_path, monkeypatch):
    # Inject the race at the exact point each implementation
    # moves: the new code's os.link, the old code's shutil.move.
    # Whichever fires, a destination created in the window
    # between the check and the move must never be overwritten.
    d, docs = _setup(tmp_path)
    dest = docs / "a.txt"
    real_link = os.link
    real_move = shutil.move

    def _racing_link(src, dst, *args, **kwargs):
        dest.write_text("racing writer was here")
        return real_link(src, dst, *args, **kwargs)

    def _racing_move(src, dst, *args, **kwargs):
        dest.write_text("racing writer was here")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", _racing_link)
    monkeypatch.setattr(shutil, "move", _racing_move)
    out = fileops.tool_file_organize(str(d), dry_run=False)
    assert dest.read_text() == "racing writer was here"
    # The source was not destroyed: it is still in place.
    assert (d / "a.txt").exists()
    assert (d / "a.txt").read_text() == "source-a"
    assert "0/1" in out or "skipped" in out.lower() or "exists" in out.lower(), out


def test_cross_device_fallback_copies_and_removes_source(tmp_path, monkeypatch):
    d, docs = _setup(tmp_path)
    (d / "b.txt").write_text("source-b")

    def _no_link(src, dst, *args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", _no_link)
    out = fileops.tool_file_organize(str(d), dry_run=False)
    assert "Organized 2/2" in out, out
    assert (docs / "a.txt").read_text() == "source-a"
    assert (docs / "b.txt").read_text() == "source-b"
    assert not (d / "a.txt").exists()
    assert not (d / "b.txt").exists()
    # No temp files left behind.
    leftovers = [p.name for p in docs.iterdir()
                 if p.name.startswith(".organize-")]
    assert leftovers == [], leftovers


def test_normal_moves_still_work(tmp_path):
    d, docs = _setup(tmp_path)
    (d / "pic.jpg").write_bytes(b"\x89PNG fake")
    out = fileops.tool_file_organize(str(d), dry_run=False)
    assert "Organized 2/2" in out, out
    assert (docs / "a.txt").read_text() == "source-a"
    assert (d / "Images" / "pic.jpg").read_bytes() == b"\x89PNG fake"
    assert not (d / "a.txt").exists()
