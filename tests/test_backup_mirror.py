"""Backup mirror (the Mark-LVI port, 2026-10-27).

The mirror's rules: unset is not a failure, a copy counts only
when its size verifies, a broken mirror reports once per
process and then degrades silently, and secrets never mirror.
The setting helpers are monkeypatched at the module seam so no
test touches the real settings file."""
import sys
import types
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import backup_mirror as bm


def setup_function():
    bm.reset_latch_for_tests()


def _stub_settings(monkeypatch, store=None):
    store = {} if store is None else store
    monkeypatch.setattr(
        bm, '_get_setting',
        lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(
        bm, '_set_setting',
        lambda key, value: store.__setitem__(key, value))
    return store


def _slot(tmp_path, name='slot1.bin', data=b'hello mirror'):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ── mirror_dir / mirror_backups ────────────────────────────────────

def test_mirror_unset_is_nothing_to_do(monkeypatch, tmp_path):
    _stub_settings(monkeypatch, {})
    assert bm.mirror_dir() == ''
    slot = _slot(tmp_path)
    assert bm.mirror_backups([slot]) is True
    # Nothing was created anywhere for the unset case.
    assert not (tmp_path / 'mirror').exists()


def test_mirror_dir_read_failure_reads_as_unset(monkeypatch):
    def boom(key, default=None):
        raise RuntimeError('settings gone')
    monkeypatch.setattr(bm, '_get_setting', boom)
    assert bm.mirror_dir() == ''


def test_mirror_copy_lands_and_verifies(monkeypatch, tmp_path):
    mirror = tmp_path / 'mirror'
    _stub_settings(monkeypatch, {'backup_mirror_dir': str(mirror)})
    a = _slot(tmp_path, 'a.db', b'12345')
    b = _slot(tmp_path, 'b.json', b'{}')
    missing = tmp_path / 'not-there-yet.db'
    assert bm.mirror_backups([a, b, missing]) is True
    assert (mirror / 'a.db').read_bytes() == b'12345'
    assert (mirror / 'b.json').read_bytes() == b'{}'


def test_short_copy_fails_and_latches_once(monkeypatch, tmp_path):
    mirror = tmp_path / 'mirror'
    _stub_settings(monkeypatch, {'backup_mirror_dir': str(mirror)})
    slot = _slot(tmp_path, 'big.db', b'x' * 100)

    def short_copy(src, dst):
        Path(dst).write_bytes(b'x')  # short copy: 1 of 100 bytes

    fake_shutil = types.SimpleNamespace(copyfile=short_copy)
    monkeypatch.setattr(bm, 'shutil', fake_shutil)
    logged = []
    monkeypatch.setattr(bm, '_log_failure', logged.append)

    assert bm.mirror_backups([slot]) is False
    assert bm.mirror_backups([slot]) is False
    assert len(logged) == 1  # once per process, then silent
    bm.reset_latch_for_tests()
    assert bm.mirror_backups([slot]) is False
    assert len(logged) == 2


def test_mirror_failure_never_raises(monkeypatch, tmp_path):
    # Mirror dir path is an existing FILE: mkdir must fail inside,
    # and the caller hears False, not an exception.
    blocker = _slot(tmp_path, 'blocker', b'')
    _stub_settings(monkeypatch, {'backup_mirror_dir': str(blocker)})
    monkeypatch.setattr(bm, '_log_failure', lambda msg: None)
    assert bm.mirror_backups([_slot(tmp_path)]) is False


# ── backup_slots / snapshot_and_mirror ─────────────────────────────

def test_backup_slots_are_the_stores_that_hurt_and_never_secrets():
    slots = bm.backup_slots()
    names = [p.name for p in slots]
    assert names == ['aria_memory.db', 'settings.json', 'chat_history.md']
    assert not any('aria_keys' in str(p) for p in slots)


def test_snapshot_and_mirror_uses_backup_slots(monkeypatch, tmp_path):
    mirror = tmp_path / 'mirror'
    _stub_settings(monkeypatch, {'backup_mirror_dir': str(mirror)})
    slot = _slot(tmp_path, 'aria_memory.db', b'data')
    monkeypatch.setattr(bm, 'backup_slots', lambda: [slot])
    assert bm.snapshot_and_mirror() is True
    assert (mirror / 'aria_memory.db').read_bytes() == b'data'


# ── tool_set_backup_mirror ─────────────────────────────────────────

def test_tool_clear_turns_mirroring_off(monkeypatch):
    store = _stub_settings(monkeypatch, {'backup_mirror_dir': '/somewhere'})
    out = bm.tool_set_backup_mirror({'clear': True})
    assert store['backup_mirror_dir'] == ''
    assert 'status=done' in out and 'mirror=off' in out
    assert 'mirroring is off' in out


def test_tool_clear_verify_failure_is_failed(monkeypatch):
    store = {'backup_mirror_dir': '/somewhere'}
    monkeypatch.setattr(
        bm, '_get_setting',
        lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(bm, '_set_setting', lambda key, value: None)
    out = bm.tool_set_backup_mirror({'clear': True})
    assert 'status=failed' in out and 'did not clear' in out


def test_tool_report_current(monkeypatch):
    _stub_settings(monkeypatch, {'backup_mirror_dir': '/drive/folder'})
    out = bm.tool_set_backup_mirror({})
    assert 'status=done' in out and '/drive/folder' in out


def test_tool_report_none_is_failed(monkeypatch):
    _stub_settings(monkeypatch, {})
    out = bm.tool_set_backup_mirror({})
    assert 'status=failed' in out and 'no mirror folder is set' in out


def test_tool_set_mirrors_current_slots_verified(monkeypatch, tmp_path):
    store = _stub_settings(monkeypatch, {})
    mirror = tmp_path / 'mirror'
    slot = _slot(tmp_path, 'settings.json', b'{"a": 1}')
    monkeypatch.setattr(bm, 'backup_slots', lambda: [slot])
    out = bm.tool_set_backup_mirror({'path': str(mirror)})
    assert store['backup_mirror_dir'] == str(mirror)
    assert 'status=done' in out and 'copied there and verified' in out
    assert (mirror / 'settings.json').read_bytes() == b'{"a": 1}'


def test_tool_set_with_no_backups_yet(monkeypatch, tmp_path):
    _stub_settings(monkeypatch, {})
    monkeypatch.setattr(bm, 'backup_slots',
                        lambda: [tmp_path / 'nope.db'])
    out = bm.tool_set_backup_mirror({'path': str(tmp_path / 'm')})
    assert 'status=done' in out and 'No backups exist yet' in out


def test_tool_set_copy_failure_is_unverified(monkeypatch, tmp_path):
    _stub_settings(monkeypatch, {})
    blocker = _slot(tmp_path, 'blocker', b'')
    slot = _slot(tmp_path, 'aria_memory.db', b'data')
    monkeypatch.setattr(bm, 'backup_slots', lambda: [slot])
    monkeypatch.setattr(bm, '_log_failure', lambda msg: None)
    out = bm.tool_set_backup_mirror({'path': str(blocker)})
    assert 'status=unverified' in out and 'first copy to it failed' in out


def test_tool_set_save_verify_failure_is_failed(monkeypatch):
    store = {}
    monkeypatch.setattr(
        bm, '_get_setting',
        lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(bm, '_set_setting', lambda key, value: None)
    out = bm.tool_set_backup_mirror({'path': '/drive/folder'})
    assert 'status=failed' in out and 'did not save on re-read' in out
