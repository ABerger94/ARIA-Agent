import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('bridge_sessions', Path(__file__).parents[1] / 'aria' / 'bridge_sessions.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def test_remember_reload_expire_and_revoke(tmp_path):
    now = [1000.0]
    path = tmp_path / 'sessions.json'
    sessions = m.BridgeSessions(path, clock=lambda: now[0])
    token = sessions.issue()
    assert sessions.valid(token)
    assert token not in path.read_text()
    other = sessions.issue()
    restored = m.BridgeSessions(path, clock=lambda: now[0])
    assert restored.valid(token) and restored.valid(other)
    restored.revoke(token)
    assert not restored.valid(token) and restored.valid(other)
    now[0] += m.SESSION_SECONDS + 1
    assert not restored.valid(other)
    assert restored.status()['paired_devices'] == 0


def test_bad_store_grants_nothing(tmp_path):
    path = tmp_path / 'sessions.json'; path.write_text('broken')
    sessions = m.BridgeSessions(path)
    assert not sessions.valid('forged')
    token = sessions.issue(); assert sessions.valid(token)
    sessions.revoke(); assert not sessions.valid(token)
    assert m.BridgeSessions(path).status()['paired_devices'] == 0


def test_failed_persistence_does_not_issue_live_token(tmp_path, monkeypatch):
    import pytest
    sessions = m.BridgeSessions(tmp_path / 'sessions.json')
    def fail(entries): raise OSError('disk full')
    monkeypatch.setattr(sessions, '_write', fail)
    with pytest.raises(OSError): sessions.issue()
    assert not sessions.entries
