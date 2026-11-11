"""Durable action intents and receipts, independent of mission checkpoints.

An external service cannot participate in a SQLite transaction. A process that
dies after sending a request but before recording its receipt leaves an
uncertain action. Such actions MUST be reconciled, never blindly replayed.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
import time

from aria.outcome import ToolResult, normalize, ASK, DONE, FAILED, UNVERIFIED
from aria.tool_policy import retry_safe


@contextmanager
def _connect():
    from aria import memory
    db = sqlite3.connect(memory.db_path(), timeout=10)
    db.execute('''CREATE TABLE IF NOT EXISTS action_receipts (
        id TEXT PRIMARY KEY, tool TEXT NOT NULL, fingerprint TEXT NOT NULL,
        state TEXT NOT NULL, result TEXT, receipt TEXT, artifacts TEXT,
        created REAL NOT NULL, updated REAL NOT NULL)''')
    db.commit()
    try:
        with db:
            yield db
    finally:
        db.close()


def signature(tool, args):
    raw = json.dumps([tool, args], sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def begin(operation_id, tool, args):
    """None grants execution. A result returns a cached or uncertain outcome."""
    fingerprint = signature(tool, args)
    with _connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT tool,fingerprint,state,result,receipt,artifacts '
                         'FROM action_receipts WHERE id=?', (operation_id,)).fetchone()
        if row:
            if row[2] == 'ask':
                # Pass 28 #30: a stored ASK is a no-effect outcome —
                # the tool asked for input and did nothing, so the
                # receipt must never be returned or block a
                # re-attempt (same args, or new ones now that the
                # question has been answered). Drop it; the insert
                # below records a fresh attempt. This also heals
                # rows written before finish() stopped storing ASK.
                db.execute('DELETE FROM action_receipts WHERE id=?',
                           (operation_id,))
            elif row[1] != fingerprint:
                return ToolResult(FAILED, 'status=failed reason="Action id reused with different arguments"')
            elif row[2] != 'executing' and not (row[2] == FAILED and retry_safe(tool)):
                return ToolResult(row[2], row[3] or '', json.loads(row[4] or '{}'),
                                  tuple(json.loads(row[5] or '[]')))
            elif row[2] == 'executing' and not retry_safe(tool):
                return ToolResult(UNVERIFIED,
                    'status=unverified reason="An earlier attempt may already have taken effect; '
                    'check its receipt or destination before creating another action" '
                    f'execution_id={operation_id}')
        now = time.time()
        db.execute('INSERT INTO action_receipts(id,tool,fingerprint,state,created,updated) '
                   'VALUES(?,?,?,\'executing\',?,?) ON CONFLICT(id) DO UPDATE '
                   'SET state=\'executing\',updated=excluded.updated',
                   (operation_id, tool, fingerprint, now, now))
    return None


def finish(operation_id, result):
    result = normalize(result)
    # Approval is an unexecuted request. The approved replay owns this same id.
    # Pass 28 #30: an ASK outcome is likewise no-effect — the tool asked for
    # input and did nothing, so persisting it as a receipt would return the
    # stale question forever and permanently block the resumed task from
    # re-attempting the step. Drop the row; the next attempt executes fresh.
    if result.text.startswith('[AWAITING_APPROVAL') or result.status == ASK:
        with _connect() as db:
            db.execute('DELETE FROM action_receipts WHERE id=?', (operation_id,))
        return
    with _connect() as db:
        db.execute('UPDATE action_receipts SET state=?,result=?,receipt=?,artifacts=?,updated=? '
                   'WHERE id=?', (result.status, str(result), json.dumps(result.receipt),
                                 json.dumps(result.artifacts), time.time(), operation_id))


def settle(operation_id, child_task_id, child_status, detail=""):
    """Pass 36 #28: record that a delegated child this operation
    submitted has settled, as a DISTINCT settlement field on the
    receipt — the original state (usually STARTED) is never
    rewritten, so idempotent replay still keys off the
    submission, and the grader renders the settlement alongside
    it instead of failing a completed step for looking started.
    No-op (False) when the row is missing. Never raises."""
    try:
        with _connect() as db:
            row = db.execute(
                'SELECT receipt FROM action_receipts WHERE id=?',
                (operation_id,)).fetchone()
            if row is None:
                return False
            try:
                receipt = json.loads(row[0] or '{}')
            except Exception:
                receipt = {}
            if not isinstance(receipt, dict):
                receipt = {}
            receipt["settlement"] = {
                "child_task_id": str(child_task_id or ""),
                "status": str(child_status or ""),
                "detail": str(detail or "")[:200],
                "at": time.time(),
            }
            db.execute(
                'UPDATE action_receipts SET receipt=?, updated=? '
                'WHERE id=?',
                (json.dumps(receipt), time.time(), operation_id))
        return True
    except Exception:
        return False


def get(operation_id):
    with _connect() as db:
        row = db.execute('SELECT id,tool,state,result,receipt,artifacts,updated '
                         'FROM action_receipts WHERE id=?', (operation_id,)).fetchone()
    if row is None:
        return None
    return dict(zip(('id','tool','state','result','receipt','artifacts','updated'),
                    (*row[:4], json.loads(row[4] or '{}'), json.loads(row[5] or '[]'), row[6])))


def check_artifact(path, expected_hash=None):
    from pathlib import Path
    file = Path(path).resolve()
    if not file.is_file():
        return {'path': str(file), 'verified': False, 'reason': 'File missing'}
    digest = hashlib.sha256()
    with file.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    sha = digest.hexdigest()
    return {'path': str(file), 'name': file.name, 'sha256': sha,
            'size': file.stat().st_size,
            'verified': expected_hash is None or sha == expected_hash}


def add_evidence(tool, args, result):
    result = normalize(result)
    if result.status != DONE:
        return result
    if tool in {'write_file', 'code_helper', 'dev_agent'}:
        path = args.get('output_path') if tool != 'write_file' else args.get('filename', args.get('path'))
        if path:
            if tool in {'write_file', 'code_helper', 'dev_agent'}:
                from aria import workspaces
                from aria.tools import builtins
                path = workspaces.resolve_workspace_file(path, builtins.WORKSPACE_DIR)
            evidence = check_artifact(path)
            return ToolResult(DONE if evidence['verified'] else UNVERIFIED,
                              result.text, result.receipt, (*result.artifacts, evidence))
    return result
