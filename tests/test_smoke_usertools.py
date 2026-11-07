"""Pass 27 #5: the restart smoke test must never execute an
unapproved user-tool module body.

_watched_source_files walks the whole aria/ package, which
includes aria/tools/user_tools/ — so a changed user-tool file
was smoke-imported after ordinary agent turns, bypassing the
deferred user-tool import gate. User-tool files are now pruned
from the watch list, and _smoke_import_changed skips them even
when passed directly.
"""
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_smoke_usertools_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
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
import importlib
config = importlib.import_module('aria.config')
schemas = importlib.import_module('aria.tools.schemas')
sandbox = importlib.import_module('aria.tools.sandbox')
memory = importlib.import_module('aria.memory')
for name in ('aria.scheduler', 'aria.vision', 'aria.hardware',
             'aria.hud'):
    sys.modules[name] = types.ModuleType(name)
_spotify = types.ModuleType('aria.spotify')
_spotify.tool_media_key = lambda *a, **k: None
_spotify.tool_spotify = lambda *a, **k: None
sys.modules['aria.spotify'] = _spotify
sys.modules['aria.vision'].get_face_frame_jpeg = lambda: None
sys.modules['aria.vision'].publish_phone_frame = lambda jpeg: True
sys.modules['aria.vision'].get_phone_frame_jpeg = lambda: None
sys.modules['aria.vision'].get_phone_frame_status = lambda: {'active': False, 'age_s': -1.0}
sys.modules['aria.vision'].describe_phone_view = lambda q='': '[stubbed]'
sys.modules['aria.hud'].tool_show_commands = lambda: 'ok'
sys.modules['aria.hud'].tool_hide_commands = lambda: 'ok'
builtins_mod = importlib.import_module('aria.tools.builtins')
dispatch = importlib.import_module('aria.tools.dispatch')
_speech = types.ModuleType('aria.speech')
_speech.edge_tts_bytes = lambda text: b''
_speech.tts_bytes_for_bridge = lambda text: (b'ID3fake', 'audio/mpeg')
_speech.transcribe_audio = lambda audio, mime: ''
sys.modules['aria.speech'] = _speech

from aria.agent.brain import (  # noqa: E402
    _smoke_import_changed, _watched_source_files)

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_watched_files_exclude_user_tools():
    files = _watched_source_files()
    assert files, 'watcher found no files'
    bad = [f for f in files if (os.sep + 'user_tools' + os.sep) in f]
    assert bad == [], f'user-tool files still watched: {bad}'
    assert os.path.join(REPO_DIR, 'aria', 'agent',
                        'brain.py') in files


def _probe_module(rel_dir, marker):
    probe = os.path.join(REPO_DIR, 'aria', rel_dir,
                         '_pass27_probe.py')
    with open(probe, 'w', encoding='utf-8') as fh:
        fh.write(f'open({marker!r}, "w").write("executed")\n')
    return probe


def test_smoke_import_skips_user_tool_body(tmp_path):
    marker = str(tmp_path / 'user_tool_marker.txt')
    probe = _probe_module(
        os.path.join('tools', 'user_tools'), marker)
    try:
        fails = _smoke_import_changed([probe])
        assert fails == [], fails
        assert not os.path.exists(marker), (
            'user-tool module body executed during smoke import')
    finally:
        if os.path.exists(probe):
            os.unlink(probe)


def test_smoke_import_still_runs_trusted_module_body(tmp_path):
    # Control: the skip is scoped to user_tools, not a blanket
    # refusal — a trusted package module still smoke-imports.
    marker = str(tmp_path / 'trusted_marker.txt')
    probe = _probe_module('tools', marker)
    try:
        fails = _smoke_import_changed([probe])
        assert fails == [], fails
        assert os.path.exists(marker), (
            'trusted module body was not smoke-imported')
    finally:
        if os.path.exists(probe):
            os.unlink(probe)
