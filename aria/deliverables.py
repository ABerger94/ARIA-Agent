"""Project deliverables with immutable file hashes and producing action receipts."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


def _connect():
    from aria import memory
    conn = sqlite3.connect(memory.db_path(), timeout=10)
    conn.execute('''CREATE TABLE IF NOT EXISTS deliverables (
        id INTEGER PRIMARY KEY, action_id TEXT NOT NULL, tool TEXT NOT NULL,
        path TEXT NOT NULL, sha256 TEXT, evidence TEXT NOT NULL, created REAL NOT NULL,
        UNIQUE(action_id,path,sha256))''')
    conn.commit()
    return conn


def capture(action_id, tool, result):
    if not result.artifacts:
        return
    with _connect() as conn:
        for artifact in result.artifacts:
            if not artifact.get('path'):
                continue
            conn.execute('INSERT OR IGNORE INTO deliverables '
                         '(action_id,tool,path,sha256,evidence,created) VALUES(?,?,?,?,?,?)',
                         (action_id, tool, str(Path(artifact['path']).resolve()),
                          artifact.get('sha256'), json.dumps(artifact), time.time()))


def list_items(limit=100):
    with _connect() as conn:
        rows = conn.execute('SELECT id,action_id,tool,path,sha256,evidence,created '
                            'FROM deliverables ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [dict(zip(('id','action_id','tool','path','sha256','evidence','created'),
                     (*row[:5], json.loads(row[5]), row[6]))) for row in rows]


def verify(item):
    from aria.action_journal import check_artifact
    return check_artifact(item['path'], item.get('sha256'))


def preview(item):
    path = Path(item['path'])
    if path.suffix.lower() not in {'.txt','.md','.json','.csv','.tsv','.py','.html','.css','.js','.yaml','.yml','.log'}:
        return 'Open this file with its associated application.'
    with path.open(encoding='utf-8', errors='replace') as stream:
        return stream.read(65536)
