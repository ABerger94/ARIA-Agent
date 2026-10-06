"""Test suite for ARIA ULTIMATE Worker B: Modules 2 + 3 (fileops, winctl).

Standalone runnable: python3 tests/test_ultimate_b.py
Uses the test_headless.py stub pattern (bypasses aria/__init__.py eager imports).
Covers spec test plan item 3.
"""
import os
import sys
import types
import tempfile
import traceback
PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
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
for name in ('aria.scheduler', 'aria.vision', 'aria.hardware', 'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[name] = types.ModuleType(name)
sys.modules['aria.vision'].get_face_frame_jpeg = lambda: None
sys.modules['aria.hud'].tool_show_commands = lambda: 'ok'
sys.modules['aria.speech'].edge_tts_bytes = lambda text: b''
importlib.import_module('aria.config')
fileops = importlib.import_module('aria.tools.fileops')
winctl = importlib.import_module('aria.tools.winctl')

def _make_mixed_dir():
    tmp = tempfile.mkdtemp(prefix='aria_fc_')
    payloads = {'photo.jpg': b'\xff\xd8fakejpg', 'doc.pdf': b'%PDF-fake', 'vid.mp4': b'\x00\x00\x00 fakemp4', 'tune.mp3': b'ID3fakemp3', 'arch.zip': b'PK\x03\x04fakezip', 'script.py': b"print('hi')", 'README': b'no extension', '.hidden': b'dotfile stays'}
    for name, data in payloads.items():
        with open(os.path.join(tmp, name), 'wb') as f:
            f.write(data)
    os.makedirs(os.path.join(tmp, 'subdir'))
    with open(os.path.join(tmp, 'subdir', 'inner.txt'), 'wb') as f:
        f.write(b'inner')
    return tmp

def test_organize_dry_run_plan():
    tmp = _make_mixed_dir()
    out = fileops.tool_file_organize(tmp, dry_run=True)
    for frag in ('photo.jpg -> Images/photo.jpg', 'doc.pdf -> Documents/doc.pdf', 'vid.mp4 -> Videos/vid.mp4', 'tune.mp3 -> Audio/tune.mp3', 'arch.zip -> Archives/arch.zip', 'script.py -> Code/script.py', 'README -> Other/README', 'Dry run only'):
        assert frag in out, f'missing {frag!r} in:\n{out}'
    assert '.hidden' not in out, 'dotfile leaked into plan'

def test_organize_dry_run_touches_nothing():
    tmp = _make_mixed_dir()
    fileops.tool_file_organize(tmp, dry_run=True)
    for name in ('photo.jpg', 'doc.pdf', 'vid.mp4', 'tune.mp3', 'arch.zip', 'script.py', 'README', '.hidden'):
        assert os.path.isfile(os.path.join(tmp, name)), f'{name} was moved during dry run!'
    assert not os.path.exists(os.path.join(tmp, 'Images')), 'subfolder created during dry run!'

def test_organize_execute():
    tmp = _make_mixed_dir()
    out = fileops.tool_file_organize(tmp, dry_run=False)
    assert 'Organized' in out, out
    assert os.path.isfile(os.path.join(tmp, 'Images', 'photo.jpg'))
    assert os.path.isfile(os.path.join(tmp, 'Documents', 'doc.pdf'))
    assert os.path.isfile(os.path.join(tmp, 'Other', 'README'))
    assert os.path.isfile(os.path.join(tmp, '.hidden'))
    assert os.path.isfile(os.path.join(tmp, 'subdir', 'inner.txt'))

def test_find_advanced():
    tmp = tempfile.mkdtemp(prefix='aria_ff_')
    with open(os.path.join(tmp, 'big.txt'), 'wb') as f:
        f.write(b'x' * (2 * 1024 * 1024))
    with open(os.path.join(tmp, 'small.txt'), 'wb') as f:
        f.write(b'needle in haystack')
    with open(os.path.join(tmp, 'note.md'), 'wb') as f:
        f.write(b'other')
    out = fileops.tool_file_find_advanced(tmp, pattern='*.txt')
    assert 'big.txt' in out and 'small.txt' in out and ('note.md' not in out), out
    out = fileops.tool_file_find_advanced(tmp, pattern='*.txt', min_size_mb=1)
    assert 'big.txt' in out and 'small.txt' not in out, out
    out = fileops.tool_file_find_advanced(tmp, content_contains='needle')
    assert 'small.txt' in out and 'big.txt' not in out, out

def test_duplicates():
    tmp = tempfile.mkdtemp(prefix='aria_dup_')
    data = b'identical content' * 1000
    for name in ('a.txt', 'b.txt'):
        with open(os.path.join(tmp, name), 'wb') as f:
            f.write(data)
    with open(os.path.join(tmp, 'c.txt'), 'wb') as f:
        f.write(b'different content')
    out = fileops.tool_file_duplicates(tmp)
    assert '1 duplicate group' in out, out
    assert 'a.txt' in out and 'b.txt' in out, out
    assert 'c.txt' not in out.replace('REPORT ONLY', ''), f'c.txt wrongly grouped:\n{out}'

def test_disk_usage():
    tmp = tempfile.mkdtemp(prefix='aria_du_')
    with open(os.path.join(tmp, 'heavy.bin'), 'wb') as f:
        f.write(b'z' * (1024 * 1024))
    with open(os.path.join(tmp, 'light.txt'), 'wb') as f:
        f.write(b'tiny')
    out = fileops.tool_disk_usage(tmp)
    assert 'heavy.bin' in out and 'light.txt' in out and ('Top' in out), out

def test_refusal_etc():
    for tool in ('tool_file_organize', 'tool_file_find_advanced', 'tool_file_duplicates', 'tool_disk_usage'):
        out = getattr(fileops, tool)('/etc')
        assert 'Refused' in out, f'{tool}(/etc) did not refuse: {out}'
    out = fileops.tool_file_organize('/usr')
    assert 'Refused' in out, out
    out = fileops.tool_disk_usage('/nonexistent-dir-xyz')
    assert 'Not a directory' in out, out

def test_organize_refuses_routines_dir():
    rd = os.path.expanduser(os.path.join('~', 'ARIA', 'routines'))
    os.makedirs(rd, exist_ok=True)
    out = fileops.tool_file_organize(rd)
    assert 'Refused' in out, out

def test_window_snap_linux_unavailable():
    out = winctl.tool_window_snap('Notepad', 'left')
    assert out == '[window control unavailable on this platform]', out

def test_window_snap_invalid_position_no_crash():
    out = winctl.tool_window_snap('Notepad', 'sideways')
    assert 'Invalid position' in out, out

def test_launch_app_linux_no_crash():
    out = winctl.tool_launch_app('notepad')
    assert 'open_app_or_url' in out, out
    assert isinstance(out, str)

def test_launch_app_empty_name():
    out = winctl.tool_launch_app('')
    assert 'app name' in out, out
