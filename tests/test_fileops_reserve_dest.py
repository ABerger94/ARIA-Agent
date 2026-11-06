"""Pass 26 B1#7: file_organize must not overwrite one source with another.

Planning bug: _unique_dest checked only on-disk existence, and the
planning loop never recorded planned destinations. With
Documents/a.txt already present, sources a.txt and a_1.txt both
planned Documents/a_1.txt; the execute loop's shutil.move then
overwrote the first with the second, destroying it.

Fix contract:
- planned destinations are reserved during planning (case-insensitive
  comparison, Windows-style), so each source gets a distinct dest;
- the execute loop re-checks existence at move time and SKIPS (never
  overwrites) a destination that appeared since planning.
"""
import os
import sys
import tempfile
import types

import pytest

_TMPHOME = tempfile.mkdtemp(prefix="aria_fileops_reserve_")
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
    (docs / "a.txt").write_text("pre-existing")
    (d / "a.txt").write_text("source-a")
    (d / "a_1.txt").write_text("source-a1")
    return d, docs


def test_planned_destinations_are_reserved(tmp_path):
    d, docs = _setup(tmp_path)
    out = fileops.tool_file_organize(str(d), dry_run=False)
    assert "Organized 2/2" in out, out
    contents = {p.name: p.read_text() for p in docs.iterdir()}
    assert contents["a.txt"] == "pre-existing"
    # Both sources survive under distinct destinations.
    assert sorted(contents.values()) == [
        "pre-existing", "source-a", "source-a1"]
    assert len(contents) == 3


def test_case_insensitive_reservation(tmp_path):
    d = tmp_path / "inbox"
    docs = d / "Documents"
    docs.mkdir(parents=True)
    (docs / "A.txt").write_text("pre-existing")
    (d / "a.txt").write_text("source-a")
    (d / "a_1.txt").write_text("source-a1")
    out = fileops.tool_file_organize(str(d), dry_run=False)
    assert "Organized 2/2" in out, out
    names = [p.name.lower() for p in docs.iterdir()]
    assert len(names) == len(set(names))
    assert len(list(docs.iterdir())) == 3


def test_move_time_collision_is_skipped_not_overwritten(tmp_path, monkeypatch):
    # Force the planner to hand both sources the same destination
    # (simulating a stale plan / a race): the execute loop must
    # skip the second move instead of overwriting the first.
    d, docs = _setup(tmp_path)
    real_unique = fileops._unique_dest
    calls = {"n": 0}

    def _same_dest(dest_dir, name, reserved=None):
        calls["n"] += 1
        return os.path.join(dest_dir, "a_1.txt")

    monkeypatch.setattr(fileops, "_unique_dest", _same_dest)
    out = fileops.tool_file_organize(str(d), dry_run=False)
    assert (docs / "a_1.txt").read_text() == "source-a"
    # The second source was not destroyed: it is still in place
    # (skipped, not moved over the first).
    assert (d / "a_1.txt").exists()
    assert (d / "a_1.txt").read_text() == "source-a1"
    assert "1/2" in out or "skipped" in out.lower() or "exists" in out.lower(), out
