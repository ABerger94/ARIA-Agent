"""Pass 28 #26: Python execution must terminate the script's
descendants and bound its output.

Before: tool_run_python used subprocess.run(capture_output=True)
— on timeout only the script itself died, so a child it had
spawned kept running orphaned; and a flooding script grew the
captured output unboundedly in memory before any truncation.

Fix contract pinned here (mirroring shelltool's established
shape):
* the script runs in its own process group; on timeout the
  whole group is killed, not just the script;
* stdout/stderr are drained by reader threads that keep
  draining past the store cap (so a flooding script cannot
  deadlock on a full pipe) but store at most the cap, and the
  result names the truncation with the true total.
"""
import importlib
import os
import sys
import time
import types
from pathlib import Path

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg

config = importlib.import_module("aria.config")
data_dir = importlib.import_module("aria.data_dir")
workspaces = importlib.import_module("aria.workspaces")
sandbox = importlib.import_module("aria.tools.sandbox")

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(data_dir, "_ROOT", str(tmp_path / "ARIA"))
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(tmp_path / "ws"))
    yield types.SimpleNamespace(tmp=tmp_path)


def test_timeout_kills_descendants(env):
    marker = Path(str(env.tmp / "ws")) / "child_survived_26.txt"
    code = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c',\n"
        "  \"import time; time.sleep(3); "
        f"open(r'{marker}', 'w').write('alive')\"])\n"
        "time.sleep(30)\n"
    )
    out = sandbox.tool_run_python(code, timeout=2)
    assert "timed out" in out, out
    # The child would write its marker ~3s after the spawn; give
    # it 5s past the kill — if the tree died with the script, the
    # marker never appears.
    time.sleep(5)
    assert not marker.exists(), "descendant outlived the timeout"


def test_flooding_script_output_is_bounded(env):
    code = "print('x' * 100000)"
    out = sandbox.tool_run_python(code, timeout=10)
    assert len(out) < 20000, len(out)
    assert "output truncated" in out, out
    assert "100001" in out, out
