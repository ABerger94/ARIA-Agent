"""Real companion effects in a temporary shared root and an in-process node hub."""
import sqlite3

import pytest
from aria.desktop_node import DesktopAgent
from aria.node_hub import NodeHub


def test_companion_scopes_files_and_capabilities(tmp_path):
    agent = DesktopAgent(tmp_path)
    with pytest.raises(ValueError, match='not enabled'):
        agent.execute('write_file', {'path':'x.txt','content':'x'}, 'one')
    with pytest.raises(ValueError, match='outside'):
        agent.execute('read_file', {'path':'../escape.txt'}, 'two')
    assert 'system_info' in agent.capabilities and 'click' not in agent.capabilities


def test_companion_replay_uses_persisted_receipt(tmp_path):
    first = DesktopAgent(tmp_path, writes=True)
    result = first.execute('write_file', {'path':'file.txt','content':'one'}, 'write-1')
    (tmp_path / 'file.txt').write_text('changed after first write')
    second = DesktopAgent(tmp_path, writes=True)
    assert second.execute('write_file', {'path':'file.txt','content':'one'}, 'write-1') == result
    assert (tmp_path / 'file.txt').read_text() == 'changed after first write'
    with pytest.raises(ValueError, match='different arguments'):
        second.execute('write_file', {'path':'other.txt','content':'two'}, 'write-1')


def test_companion_uncertain_effect_is_not_retried(tmp_path):
    agent = DesktopAgent(tmp_path, writes=True)
    import hashlib, json
    args = {'path':'file.txt','content':'one'}
    fingerprint = hashlib.sha256(json.dumps(['write_file',args], sort_keys=True).encode()).hexdigest()
    with sqlite3.connect(agent.db) as conn:
        conn.execute('INSERT INTO commands VALUES(?,?,\'executing\',NULL)', ('lost',fingerprint))
    with pytest.raises(RuntimeError, match='uncertain'):
        agent.execute('write_file', args, 'lost')
    assert not (tmp_path / 'file.txt').exists()


def test_named_desktop_protocol_round_trip(tmp_path):
    agent = DesktopAgent(tmp_path, writes=True)
    hub = NodeHub()
    def send(message):
        data = agent.execute(message['action'], message['args'], message['id'])
        hub.handle_result({'type':'result','id':message['id'],'ok':True,'data':data})
    hub.register('second-laptop','desktop',agent.capabilities,send)
    receipt = hub.send_command('second-laptop','write_file',{'path':'report.txt','content':'hello'})
    assert receipt['sha256'] and (tmp_path / 'report.txt').read_text() == 'hello'
    # Phone tools must never silently pick a desktop when no phone is connected.
    assert hub._pick(None) is None
