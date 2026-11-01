import json
import sqlite3
import threading

import pytest
from test_missions import env, memory, config
from aria import workspaces, runtime, usage, deliverables, action_journal


def test_projects_isolate_memories_histories_and_bound_threads(env, monkeypatch):
    monkeypatch.setattr(config, 'WORKSPACE_DIR', str(env.tmp / 'workspace'))
    monkeypatch.setattr(memory, '_embed', lambda text: None)
    a, b = workspaces.create('Alpha'), workspaces.create('Beta')
    with workspaces.use(a):
        memory.init_databases(); memory.memory_save('project', 'name', 'Alpha')
        workspaces.save_history([{'text':'Alpha'}])
        callback = runtime.copied_call(lambda: workspaces.current())
    workspaces.select(b)
    with workspaces.use(b):
        memory.init_databases()
        assert memory.memory_get('project','name') == ''
        assert workspaces.load_history() == []
    assert callback() == a
    with workspaces.use(a): assert workspaces.load_history() == [{'text':'Alpha'}]


def test_semantic_vectors_are_read_only_safe_and_fallback_is_recent(env, monkeypatch):
    with sqlite3.connect(memory.db_path()) as conn:
        conn.execute('INSERT INTO memory(category,key,value,embedding,updated_at) VALUES(?,?,?,?,?)',
                     ('test','old','old',memory._pack_embedding([1.,0.,0.]),'2020-01-01'))
        for i in range(12):
            conn.execute('INSERT INTO memory(category,key,value,updated_at) VALUES(?,?,?,?)',
                         ('test',f'new{i}',str(i),f'2026-10-{i+1:02}'))
    monkeypatch.setattr(memory, '_embed', lambda q: [1.,0.,0.])
    assert 'old' in memory.memory_search_semantic('test')
    monkeypatch.setattr(memory, '_embed', lambda q: None)
    text = memory.build_prompt_memories('test', top_k=1)
    assert 'new11' in text and 'old' not in text


def test_spine_tail_reads_unicode_from_end(env, monkeypatch):
    path = env.tmp / 'spine.jsonl'
    path.write_text(''.join(json.dumps({'n':i,'text':'café'})+'\n' for i in range(5000)))
    monkeypatch.setattr(memory, 'SPINE_PATH', str(path))
    assert [e['n'] for e in memory.spine_tail(2)] == [4998,4999]
    assert memory.spine_tail(0) == []


def test_budget_reservations_are_atomic_and_unpriced_calls_refused(env):
    workspaces.update_settings({'task_budget_usd': .006,
        'model_rates': {'test:m': {'input':0, 'output':2}}, 'max_output_tokens':2048})
    with runtime.execution('task', budget_scope='shared'):
        ticket = usage.reserve('test', 'm', {})
        with pytest.raises(usage.BudgetExceeded): usage.reserve('test','m',{})
        usage.finish(ticket, usage={'prompt_tokens':100,'completion_tokens':10})
        assert usage.recent()[0]['cost'] == pytest.approx(.00002)
        with pytest.raises(usage.BudgetExceeded): usage.reserve('unknown','m',{})


def test_receipts_index_and_verify_file_versions(env):
    path = env.tmp / 'report.md'; path.write_text('version 1')
    evidence = action_journal.check_artifact(path)
    from aria.outcome import ToolResult
    deliverables.capture('one','write_file',ToolResult('done','saved',artifacts=(evidence,)))
    item = deliverables.list_items()[0]
    assert deliverables.verify(item)['verified']
    path.write_text('version 2')
    assert not deliverables.verify(item)['verified']
    assert deliverables.preview(item) == 'version 2'


def test_budget_does_not_leak_between_projects(env, monkeypatch):
    monkeypatch.setattr(config, 'WORKSPACE_DIR', str(env.tmp / 'workspace'))
    a,b = workspaces.create('A'), workspaces.create('B')
    for pid in [a,b]:
        with workspaces.use(pid):
            memory.init_databases()
            workspaces.update_settings({'task_budget_usd':0,'model_rates':{'free:m':{'input':0,'output':0}}})
    with runtime.execution('task', project=a):
        usage.reserve('free','m',{})
        assert len(usage.recent()) == 1
    with runtime.execution('task', project=b): assert usage.recent() == []
