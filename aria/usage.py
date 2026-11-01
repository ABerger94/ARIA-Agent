"""Persistent model attempts and atomic budget reservations.

Prices are user-configured USD per million tokens, never guessed. Bounded work
refuses an unpriced model. Missing usage keeps the conservative reservation
and is labelled estimated; it is never reported as provider-billed spend.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager

from aria import runtime, workspaces


class BudgetExceeded(Exception):
    pass


def canonical_provider(name):
    return 'gemini' if name == 'gemini' or name.startswith('gemini_') else name


def settings():
    return workspaces.get().get('settings', {})


def _connect():
    from aria import memory
    conn = sqlite3.connect(memory.db_path(), timeout=10)
    conn.execute('''CREATE TABLE IF NOT EXISTS model_usage (
        id TEXT PRIMARY KEY, scope TEXT, provider TEXT, model TEXT, day TEXT,
        created REAL, latency_ms INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER,
        reserved REAL, cost REAL, estimated INTEGER, status TEXT, reason TEXT, input_rate REAL, output_rate REAL)''')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(model_usage)')}
    for name in ('input_rate','output_rate'):
        if name not in columns: conn.execute(f'ALTER TABLE model_usage ADD COLUMN {name} REAL')
    conn.commit()
    return conn


def max_output_tokens():
    return max(64, min(16384, int(settings().get('max_output_tokens', 2048))))


def reserve(provider, model, payload, reason='configured provider order'):
    ctx, policy = runtime.current(), settings()
    daily = policy.get('daily_budget_usd')
    task_cap = ctx.spend_cap if ctx.spend_cap is not None else policy.get('task_budget_usd')
    provider_key = canonical_provider(provider)
    rates = policy.get('model_rates', {}).get(f'{provider_key}:{model}')
    bounded = daily is not None or task_cap is not None
    if rates is None and bounded:
        raise BudgetExceeded(f'No configured price for {provider_key}:{model}; budgeted call refused')
    amount = None
    input_rate = output_rate = None
    if rates is not None:
        input_rate, output_rate = float(rates['input']), float(rates['output'])
        if min(input_rate, output_rate) < 0 or not all(map(math.isfinite, (input_rate, output_rate))):
            raise ValueError('Model prices must be finite nonnegative numbers')
        prompt_bound = len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) + 512
        completion_bound = int(payload.get('max_tokens', (payload.get('options') or {}).get('num_predict', max_output_tokens())))
        amount = (prompt_bound * input_rate + completion_bound * output_rate) / 1_000_000
    day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    uid = uuid.uuid4().hex
    with _connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for cap, clause, value in ((daily, 'day', day),
                                   (task_cap, 'scope', ctx.budget_scope or ctx.id)):
            if cap is None:
                continue
            cap = float(cap)
            if not math.isfinite(cap) or cap < 0:
                raise ValueError('Budgets must be finite nonnegative numbers')
            spent, unknown = conn.execute(
                f'SELECT COALESCE(SUM(COALESCE(cost,reserved)),0), '
                f'SUM(CASE WHEN cost IS NULL AND reserved IS NULL THEN 1 ELSE 0 END) '
                f'FROM model_usage WHERE {clause}=?', (value,)).fetchone()
            if unknown or spent + (amount or 0) > cap + 1e-12:
                raise BudgetExceeded(f'{clause} budget would be exceeded; call refused')
        conn.execute('INSERT INTO model_usage '
                     '(id,scope,provider,model,day,created,reserved,estimated,status,reason,input_rate,output_rate) '
                     'VALUES(?,?,?,?,?,?,?,1,\'reserved\',?,?,?)',
                     (uid, ctx.budget_scope or ctx.id, provider, model, day, time.time(), amount, reason, input_rate, output_rate))
    return uid


def finish(uid, *, usage=None, latency_ms=0, status='ok'):
    usage = usage or {}
    with _connect() as conn:
        row = conn.execute('SELECT provider,model,reserved,input_rate,output_rate FROM model_usage WHERE id=?', (uid,)).fetchone()
        if not row:
            return
        provider, model, reserved, input_rate, output_rate = row
        rates = {"input":input_rate,"output":output_rate} if input_rate is not None else None
        prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
        measured = rates is not None and isinstance(prompt, int) and isinstance(completion, int) and min(prompt, completion) >= 0
        cost = ((prompt * float(rates['input']) + completion * float(rates['output'])) / 1_000_000
                if measured else reserved)
        conn.execute('UPDATE model_usage SET cost=?,estimated=?,prompt_tokens=?,completion_tokens=?, '
                     'latency_ms=?,status=? WHERE id=?',
                     (cost, not measured, prompt, completion, latency_ms, status, uid))


def recent(limit=100):
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute('SELECT * FROM model_usage ORDER BY created DESC LIMIT ?', (limit,))]


@contextmanager
def attempt(provider, model, payload, reason):
    runtime.check_cancelled()
    ticket = reserve(provider, model, payload, reason)
    start = time.monotonic()
    result = {'usage': None}
    ok = False
    try:
        yield result
        ok = True
    finally:
        finish(ticket, usage=result['usage'], latency_ms=int((time.monotonic()-start)*1000),
               status='ok' if ok else 'error')


def order(chain):
    """An explicit project preference changes ordering, never availability/approval."""
    preferred = settings().get('provider_order') or []
    def rank(provider):
        name = canonical_provider(provider.name)
        return preferred.index(name) if name in preferred else len(preferred)
    return sorted(chain, key=rank)
