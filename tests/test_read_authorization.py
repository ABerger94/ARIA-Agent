"""Pass 36 #39: one shared project-aware read-authorization check.

process_file / vision_look used only file_allowed, which is True
for ANY path when file_roots is empty — the reserved projects/
subtree (named-project private storage) was readable through
them. workspaces.read_authorization_error resolves the effective
path, refuses the reserved subtree, then applies the configured
roots; the dispatch file-argument gate, tool_process_file, and
vision_look/_first_look all use it.
"""
import importlib
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMPHOME = tempfile.mkdtemp(prefix='aria_read_auth_')
os.environ['HOME'] = _TMPHOME

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

for _name in ('aria.scheduler', 'aria.vision', 'aria.hardware',
              'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules.setdefault(_name, types.ModuleType(_name))
tools_pkg = types.ModuleType('aria.tools')
tools_pkg.__path__ = [str(Path(__file__).parents[1] / 'aria' / 'tools')]
sys.modules.setdefault('aria.tools', tools_pkg)
agent_pkg = types.ModuleType('aria.agent')
agent_pkg.__path__ = [str(Path(__file__).parents[1] / 'aria' / 'agent')]
sys.modules.setdefault('aria.agent', agent_pkg)

config = importlib.import_module('aria.config')
data_dir = importlib.import_module('aria.data_dir')
runtime = importlib.import_module('aria.runtime')
approval = importlib.import_module('aria.approval')
workspaces = importlib.import_module('aria.workspaces')
dispatch = importlib.import_module('aria.tools.dispatch')


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(data_dir, '_ROOT', str(tmp_path / 'ARIA'))
    monkeypatch.setattr(config, 'WORKSPACE_DIR', str(root))
    (root / "projects" / "abc123").mkdir(parents=True)
    (root / "projects" / "abc123" / "secret.txt").write_text(
        "another project's private notes")
    (root / "notes.txt").write_text("ordinary workspace notes")
    dispatch.reset_turn_state()
    approval.tool_set_approval_mode('auto')
    yield root
    dispatch.reset_turn_state()


def test_reserved_subtree_is_refused(ws):
    secret = str(ws / "projects" / "abc123" / "secret.txt")
    assert not workspaces.is_read_authorized(secret)
    err = workspaces.read_authorization_error(secret)
    assert err and "reserved projects/" in err
    # The same path, addressed workspace-relatively, is refused
    # too — the check resolves against the project root, never
    # the process cwd.
    assert not workspaces.is_read_authorized(
        "projects/abc123/secret.txt")


def test_ordinary_workspace_file_is_authorized(ws):
    assert workspaces.is_read_authorized(str(ws / "notes.txt"))
    assert workspaces.read_authorization_error(
        str(ws / "notes.txt")) is None
    # An absolute path outside the workspace is authorized when
    # no file_roots are configured (legacy access, unchanged).
    assert workspaces.is_read_authorized("/tmp/elsewhere.txt")


def test_dispatch_gate_refuses_reserved_process_file(ws):
    secret = str(ws / "projects" / "abc123" / "secret.txt")
    out, _ok = dispatch.execute_tool(
        "process_file", {"path": secret})
    assert out.startswith("status=failed"), out
    assert "reserved projects/" in out
    assert "private notes" not in out


def test_dispatch_gate_refuses_reserved_vision_look(ws):
    secret = str(ws / "projects" / "abc123" / "secret.txt")
    out, _ok = dispatch.execute_tool(
        "vision_look", {"image_path": secret})
    assert out.startswith("status=failed"), out
    assert "reserved projects/" in out
