"""Notify: three sinks, dedupe, stores, rate discipline (2026-10-27).

The stores are file-backed on purpose — dedupe and the tool's
rate-limit ledger must survive a restart — so every test runs
against tmp_path stores, an in-memory settings dict patched in at
notify's _get_setting/_set_setting seam, and the module clock seam
instead of sleeping."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import notify, outcome


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Tmp stores, fake settings, clean in-process dedupe/broadcast."""
    settings = {}
    monkeypatch.setattr(notify, 'SUBS_PATH', tmp_path / 'push_subscriptions.json')
    monkeypatch.setattr(notify, 'QUEUE_PATH', tmp_path / 'notify_queue.json')
    monkeypatch.setattr(notify, '_get_setting',
                        lambda k, default=None: settings.get(k, default))
    monkeypatch.setattr(notify, '_set_setting',
                        lambda k, v: settings.__setitem__(k, v) or True)
    notify._recent.clear()
    notify.bind_broadcaster(None)
    yield settings
    notify._recent.clear()
    notify.bind_broadcaster(None)


def _sub(endpoint='https://push.example/abc', p256dh='pk', auth='ak'):
    return {'endpoint': endpoint, 'keys': {'p256dh': p256dh, 'auth': auth}}


# ── notify(): the one call ──────────────────────────────────────

def test_notify_queues_and_reports_the_result_shape():
    r = notify.notify('Title', 'Body text', now=1000.0)
    assert r['ok'] is True and r['enabled'] is True
    assert r['deduped'] is False and r['queued'] is True
    assert r['toast'] is False and r['pushed'] == 0 and r['pruned'] == 0
    assert 'webpush_available' in r
    rows = notify.queued()
    assert len(rows) == 1
    assert rows[0]['title'] == 'Title' and rows[0]['body'] == 'Body text'
    assert rows[0]['source'] == 'system' and rows[0]['ts'] == 1000.0


def test_notify_empty_is_refused_and_title_falls_back_to_body():
    r = notify.notify('', '', now=1000.0)
    assert r['ok'] is False and r['reason'] == 'empty'
    assert notify.queued() == []
    r2 = notify.notify('', 'a body that becomes the title', now=1000.0)
    assert r2['ok'] is True
    assert notify.queued()[0]['title'] == 'a body that becomes the title'


def test_notify_broadcasts_to_the_bound_hook(isolated):
    seen = []
    notify.bind_broadcaster(seen.append)
    r = notify.notify('T', 'B', source='test', now=1000.0)
    assert r['toast'] is True
    assert seen[0]['type'] == 'notification'
    assert seen[0]['title'] == 'T' and seen[0]['source'] == 'test'


def test_dedupe_identical_within_an_hour_then_allowed():
    assert notify.notify('T', 'B', now=1000.0)['deduped'] is False
    assert notify.notify('T', 'B', now=2000.0)['deduped'] is True
    assert len(notify.queued()) == 1          # delivered once
    r = notify.notify('T', 'B', now=1000.0 + 3601.0)
    assert r['deduped'] is False
    assert len(notify.queued()) == 2


def test_dedupe_same_body_any_title_within_two_minutes():
    assert notify.notify('One title', 'same body', now=1000.0)['deduped'] is False
    assert notify.notify('Other title', 'same body', now=1060.0)['deduped'] is True
    # Past the 2-minute same-body window (and a different title, so
    # the identical-pair window does not apply): delivered again.
    assert notify.notify('Other title', 'same body', now=1121.0)['deduped'] is False


def test_queue_is_capped_at_fifty_oldest_dropped():
    for i in range(55):
        r = notify.notify(f'T{i}', f'body {i}', now=1000.0 + i)
        assert r['ok'] is True
    rows = notify.queued()
    assert len(rows) == 50
    assert rows[0]['body'] == 'body 5'
    assert rows[-1]['body'] == 'body 54'


def test_disabled_flag_silences_everything(isolated):
    isolated['notify_enabled'] = False
    r = notify.notify('T', 'B', now=1000.0)
    assert r['ok'] is False and r['enabled'] is False
    assert r['queued'] is False
    assert notify.queued() == []
    assert notify.status()['notify_enabled'] is False


def test_dismiss_by_id_and_all():
    notify.notify('A', 'body a', now=1000.0)
    notify.notify('B', 'body b', now=2000.0)
    first = notify.queued()[0]['id']
    assert notify.dismiss(first) == 1
    assert [e['title'] for e in notify.queued()] == ['B']
    assert notify.dismiss() == 1
    assert notify.queued() == []
    assert notify.dismiss() == 0


# ── Subscriptions ───────────────────────────────────────────────

def test_subscription_add_dedupe_refresh_remove():
    assert notify.add_subscription(_sub()) is True
    assert notify.subscription_count() == 1
    # Same endpoint, same keys: already stored, no duplicate.
    assert notify.add_subscription(_sub()) is True
    assert notify.subscription_count() == 1
    # Same endpoint, fresh keys: refreshed in place.
    assert notify.add_subscription(_sub(p256dh='pk2', auth='ak2')) is True
    assert notify.subscription_count() == 1
    assert notify.list_subscriptions()[0]['keys']['p256dh'] == 'pk2'
    # A second phone is a second record.
    assert notify.add_subscription(_sub(endpoint='https://push.example/xyz')) is True
    assert notify.subscription_count() == 2
    assert notify.remove_subscription('https://push.example/abc') is True
    assert notify.subscription_count() == 1
    assert notify.remove_subscription('https://push.example/abc') is False
    assert notify.remove_subscription('') is False


def test_subscription_invalid_values_are_rejected():
    assert notify.add_subscription(None) is False
    assert notify.add_subscription('nope') is False
    assert notify.add_subscription({}) is False
    assert notify.add_subscription({'endpoint': 'https://x'}) is False
    assert notify.add_subscription(
        {'endpoint': 'https://x', 'keys': {'p256dh': 'p'}}) is False
    assert notify.add_subscription(
        {'endpoint': '', 'keys': {'p256dh': 'p', 'auth': 'a'}}) is False
    assert notify.subscription_count() == 0


# ── The model-call ledger (persistence is the point) ────────────

def test_model_call_ledger_prunes_and_persists_on_disk(isolated, tmp_path):
    notify.record_model_call(1000.0)
    notify.record_model_call(2000.0)
    assert notify.model_call_times() == [1000.0, 2000.0]
    # A call more than 7200s after the first prunes it.
    notify.record_model_call(1000.0 + 7201.0)
    assert notify.model_call_times() == [2000.0, 8201.0]
    # It is in the file, not just in memory...
    raw = json.loads((tmp_path / 'notify_queue.json').read_text())
    assert raw['model_calls'] == [2000.0, 8201.0]
    # ...and a fresh load of the same module file over the same
    # stores sees it (a restart cannot reset the rate limit).
    spec = importlib.util.spec_from_file_location(
        'aria_notify_fresh', Path(notify.__file__))
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    fresh.SUBS_PATH = tmp_path / 'push_subscriptions.json'
    fresh.QUEUE_PATH = tmp_path / 'notify_queue.json'
    assert fresh.model_call_times() == [2000.0, 8201.0]


def test_corrupt_store_recovers_from_backup(tmp_path):
    notify.notify('Good', 'body good', now=1000.0)
    notify.notify('Good2', 'body good2', now=5000.0)  # snapshots the first state
    (tmp_path / 'notify_queue.json').write_text('{corrupt', encoding='utf-8')
    rows = notify.queued()
    assert [e['title'] for e in rows] == ['Good']     # the newest good backup


# ── The push sink ───────────────────────────────────────────────

def test_push_gone_subscription_is_pruned(monkeypatch):
    assert notify.add_subscription(_sub()) is True
    monkeypatch.setattr(notify, 'webpush_available', lambda: True)
    monkeypatch.setattr(notify, 'ensure_vapid', lambda: ('pub', 'priv'))
    monkeypatch.setattr(notify, '_send_push', lambda sub, payload, priv: 'gone')
    r = notify.notify('T', 'B', now=1000.0)
    assert r['pruned'] == 1 and r['pushed'] == 0
    assert notify.subscription_count() == 0


def test_push_ok_counts_delivered_and_error_keeps_the_sub(monkeypatch):
    assert notify.add_subscription(_sub()) is True
    monkeypatch.setattr(notify, 'webpush_available', lambda: True)
    monkeypatch.setattr(notify, 'ensure_vapid', lambda: ('pub', 'priv'))
    monkeypatch.setattr(notify, '_send_push', lambda sub, payload, priv: 'ok')
    r = notify.notify('T', 'B', now=1000.0)
    assert r['pushed'] == 1 and r['pruned'] == 0
    monkeypatch.setattr(notify, '_send_push', lambda sub, payload, priv: 'error')
    r2 = notify.notify('T2', 'B2', now=5000.0)
    assert r2['pushed'] == 0 and r2['pruned'] == 0
    assert notify.subscription_count() == 1         # a blip does not unsubscribe


def test_vapid_keys_persist_through_the_settings_seam(isolated):
    if notify._Vapid is None:
        pytest.skip('py_vapid not installed in this environment')
    pub, priv = notify.ensure_vapid()
    assert pub and priv
    assert isolated['vapid_public_key'] == pub
    assert isolated['vapid_private_key'] == priv
    assert notify.ensure_vapid() == (pub, priv)      # reused, not regenerated
    assert notify.vapid_public_key() == pub
    assert notify.status()['vapid_configured'] is True


def test_status_shape():
    s = notify.status()
    assert s['notify_enabled'] is True
    assert s['subscriptions'] == 0 and s['queued'] == 0
    assert isinstance(s['webpush_available'], bool)


# ── tool_notify_phone ───────────────────────────────────────────

def _stub_delivery(monkeypatch, **over):
    result = {'ok': True, 'enabled': True, 'deduped': False,
              'toast': False, 'pushed': 2, 'pruned': 0, 'queued': True,
              'webpush_available': True}
    result.update(over)
    calls = []
    monkeypatch.setattr(
        notify, 'notify',
        lambda title, body, source='system', now=None: calls.append(
            (title, body, source, now)) or dict(result))
    return calls


def test_tool_requires_a_body():
    out = notify.tool_notify_phone({'title': 'Hi'})
    assert outcome.kind_of(out) == 'failed'
    assert 'no message text' in out
    assert outcome.kind_of(notify.tool_notify_phone({})) == 'failed'
    assert outcome.kind_of(notify.tool_notify_phone(None)) == 'failed'


def test_tool_delivers_records_the_call_and_reports_done(monkeypatch):
    monkeypatch.setattr(notify, 'clock', lambda: 10000.0)
    calls = _stub_delivery(monkeypatch)
    out = notify.tool_notify_phone({'body': 'the build finished'})
    assert outcome.kind_of(out) == 'done'
    assert 'pushed=2' in out and 'queued=true' in out
    assert 'title=ARIA' in out                       # default title
    assert calls and calls[0][1] == 'the build finished'
    assert notify.model_call_times() == [10000.0]    # budget spent on delivery


def test_tool_rate_limit_min_gap_refuses_without_sending(monkeypatch):
    monkeypatch.setattr(notify, 'clock', lambda: 10000.0)
    notify.record_model_call(10000.0 - 100.0)         # 100s ago: inside the gap
    calls = _stub_delivery(monkeypatch)
    out = notify.tool_notify_phone({'body': 'too soon'})
    assert outcome.kind_of(out) == 'failed'
    assert 'less than' in out and '5 minutes' in out
    assert calls == []                               # never reached the sinks
    assert notify.model_call_times() == [9900.0]     # a refusal spends nothing


def test_tool_rate_limit_hourly_cap(monkeypatch):
    monkeypatch.setattr(notify, 'clock', lambda: 100000.0)
    for t in (96500.0, 97000.0, 97500.0, 98000.0, 99000.0, 99600.0):
        notify.record_model_call(t)
    calls = _stub_delivery(monkeypatch)
    out = notify.tool_notify_phone({'body': 'one too many'})
    assert outcome.kind_of(out) == 'failed'
    assert 'hourly cap' in out
    assert calls == []


def test_tool_disabled_and_deduped_become_failures(monkeypatch):
    monkeypatch.setattr(notify, 'clock', lambda: 10000.0)
    _stub_delivery(monkeypatch, ok=True, enabled=False)
    out = notify.tool_notify_phone({'body': 'x'})
    assert outcome.kind_of(out) == 'failed' and 'switched off' in out
    _stub_delivery(monkeypatch, deduped=True)
    out2 = notify.tool_notify_phone({'body': 'x'})
    assert outcome.kind_of(out2) == 'failed' and 'already went out' in out2
    assert notify.model_call_times() == []           # neither spent the budget


def test_tool_refuses_in_preauthorized_scope(monkeypatch):
    monkeypatch.setattr(notify, 'clock', lambda: 10000.0)
    _stub_delivery(monkeypatch)
    fake = types.ModuleType('aria.tools.dispatch')
    fake.in_preauthorized_scope = lambda: True
    # No in_delegated_scope on purpose: the getattr fallback reads False.
    monkeypatch.setitem(sys.modules, 'aria.tools.dispatch', fake)
    out = notify.tool_notify_phone({'body': 'from a routine'})
    assert outcome.kind_of(out) == 'failed'
    assert 'delegated tasks' in out or 'routine replays' in out


def test_tool_refuses_in_delegated_scope(monkeypatch):
    monkeypatch.setattr(notify, 'clock', lambda: 10000.0)
    _stub_delivery(monkeypatch)
    fake = types.ModuleType('aria.tools.dispatch')
    fake.in_preauthorized_scope = lambda: False
    fake.in_delegated_scope = lambda: True
    monkeypatch.setitem(sys.modules, 'aria.tools.dispatch', fake)
    out = notify.tool_notify_phone({'body': 'from a delegated task'})
    assert outcome.kind_of(out) == 'failed'
