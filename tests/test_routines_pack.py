"""Routine packs: export/import round trip + the safety rules.

The pack tools already existed (registered in dispatch and
schemas) but had no tests. The rules that matter, from Mark-LVI's
design: a pack is a zip (manifest + one JSON per routine),
imports NEVER overwrite an existing routine, and imported
routines land UNTRUSTED however the pack's copy was flagged.
"""
import json
import os
import sys
import types

pkg = types.ModuleType('aria')
pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import routines


def _seed(tmp_path, monkeypatch, name='morning_setup', trusted=True):
    monkeypatch.setattr(routines, '_routines_dir', lambda: str(tmp_path))
    data = {'name': name, 'steps': [{'tool': 'noop', 'args': {}}],
            'created': '2026-10-27', 'trusted': trusted}
    (tmp_path / f'{name}.json').write_text(json.dumps(data))
    return data


def test_pack_round_trip_lands_untrusted(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    pack = str(tmp_path / 'pack.zip')
    assert 'Exported 1 routine(s)' in routines.export_pack(
        ['morning_setup'], pack)
    # Simulate the other machine: empty routines dir.
    other = tmp_path / 'other'
    other.mkdir()
    monkeypatch.setattr(routines, '_routines_dir', lambda: str(other))
    out = routines.import_pack(pack)
    assert 'imported: morning_setup' in out
    landed = json.loads((other / 'morning_setup.json').read_text())
    assert landed['trusted'] is False          # trust never travels
    assert landed['steps'] == [{'tool': 'noop', 'args': {}}]


def test_import_never_overwrites(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    pack = str(tmp_path / 'pack.zip')
    routines.export_pack(['morning_setup'], pack)
    out = routines.import_pack(pack)            # same dir: it exists
    assert 'skipped (already exist): morning_setup' in out


def test_import_rejects_non_pack(tmp_path, monkeypatch):
    monkeypatch.setattr(routines, '_routines_dir', lambda: str(tmp_path))
    bogus = tmp_path / 'bogus.zip'
    import zipfile
    with zipfile.ZipFile(bogus, 'w') as z:
        z.writestr('whatever.json', '{}')
    assert 'refusing to import' in routines.import_pack(str(bogus))
    assert 'not found' in routines.import_pack(str(tmp_path / 'nope.zip'))


def test_export_with_no_match_is_honest(tmp_path, monkeypatch):
    monkeypatch.setattr(routines, '_routines_dir', lambda: str(tmp_path))
    assert routines.export_pack(['ghost'], str(tmp_path / 'p.zip')) \
        == '[No matching routines to export.]'
