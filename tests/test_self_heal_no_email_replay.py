"""Regression tests (2026-11-01, ChatGPT x ARIA loop pass 13, fixing
pass 12's runner-up): self-healing duplicated a successfully sent
email. send_email's success message echoes the subject verbatim, so
a subject containing both "FileNotFoundError" and "PermissionError"
fired dispatch's output-based healing trigger; diagnose_error mapped
it to PERMISSION_LOCKED; Heuristic 6 then re-called the handler for
ANY tool name, bypassing the dispatcher duplicate guard (which only
records the call AFTER healing runs). One approval, two sends.

Fix contract pinned here:
- through the REAL dispatcher, a sent email whose subject contains
  both error words is delivered exactly once, with no healing suffix;
- self-healing refuses to replay send_email directly, on both the
  output-shaped input and a real exception (e.g. a post-send logging
  failure raising PermissionError);
- the mailer itself never resends on an ambiguous outcome: a
  send_message raise reports "outcome unknown" after ONE attempt,
  and a post-send audit-log failure still reports success after ONE
  attempt; only pre-send (connect/login) failures retry, and then
  give up honestly after two attempts;
- control: healing still replays a genuinely safe tool (fetch_url
  after a transient timeout).

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import types
import tempfile

_TMPHOME = tempfile.mkdtemp(prefix='aria_no_email_replay_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir_v20
_data_dir_v20._ROOT = os.path.join(_TMPHOME, 'ARIA')  # V20: state stores resolve via data_dir (install root), not HOME

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
for _name in ('aria.scheduler', 'aria.vision', 'aria.hardware',
              'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)

approval = importlib.import_module('aria.approval')
dispatch = importlib.import_module('aria.tools.dispatch')
self_healing = importlib.import_module('aria.agent.self_healing')
builtins = importlib.import_module('aria.tools.builtins')

_ORIG_SEND_EMAIL = dispatch._REGISTRY.get('send_email')
_CALLS = []


def _stub_sent_with_error_words(args):
    _CALLS.append(dict(args or {}))
    return ("Email sent to nobody@example.com: "
            "'FileNotFoundError PermissionError weekly report'.")


def _fresh():
    dispatch._TURN_CALLS.clear()
    _CALLS.clear()
    approval._PENDING.clear()
    approval.tool_set_approval_mode('auto')
    os.environ.pop('ARIA_SAFE_MODE', None)


def teardown_module(module=None):
    if _ORIG_SEND_EMAIL is not None:
        dispatch._REGISTRY['send_email'] = _ORIG_SEND_EMAIL
    else:
        dispatch._REGISTRY.pop('send_email', None)


ARGS = {'to': 'nobody@example.com',
        'subject': 'FileNotFoundError PermissionError weekly report',
        'body': 'b'}

# ------------------------------------------------------------ the duplication

def test_error_like_subject_sends_exactly_once_via_dispatch():
    _fresh()
    dispatch._REGISTRY['send_email'] = _stub_sent_with_error_words
    try:
        res, _ok = dispatch.execute_tool('send_email', dict(ARGS))
    finally:
        dispatch._REGISTRY['send_email'] = _ORIG_SEND_EMAIL
    assert len(_CALLS) == 1, f'email sent {_CALLS.__len__()} times: {_CALLS}'
    assert res.startswith('Email sent to '), res
    assert 'Autonomous Self-Healing' not in res, res


def test_self_healing_refuses_email_replay_directly():
    calls = []

    def _handler(args):
        calls.append(dict(args or {}))
        return 'second send'

    healed, out, _msg = self_healing.attempt_auto_heal(
        'send_email', dict(ARGS),
        "Email sent to nobody@example.com: "
        "'FileNotFoundError PermissionError weekly report'.",
        _handler)
    assert healed is False
    assert calls == [], 'healing re-executed the email handler'
    assert out.startswith('Email sent to '), out


def test_self_healing_refuses_email_replay_on_exception():
    calls = []

    def _handler(args):
        calls.append(dict(args or {}))
        return 'second send'

    healed, _out, _msg = self_healing.attempt_auto_heal(
        'send_email', dict(ARGS),
        PermissionError('post-send log write failed'),
        _handler)
    assert healed is False
    assert calls == [], 'healing re-executed the email handler on exception'


def test_post_send_exception_does_not_resend_via_dispatch():
    _fresh()
    calls = []

    def _raising_handler(args):
        calls.append(dict(args or {}))
        raise PermissionError('post-send log write failed')

    dispatch._REGISTRY['send_email'] = _raising_handler
    try:
        res, _ok = dispatch.execute_tool('send_email', dict(ARGS))
    finally:
        dispatch._REGISTRY['send_email'] = _ORIG_SEND_EMAIL
    assert len(calls) == 1, f'handler ran {len(calls)} times'
    assert 'Autonomous Self-Healing' not in res, res
    assert '[Tool Error' in res, res


# ------------------------------------------------------------ the mailer's own retry

class _FakeSMTP:
    instances = 0
    behaviour = 'ok'  # 'ok' | 'send_raises' | 'connect_raises'

    def __init__(self, *a, **k):
        type(self).instances += 1
        if type(self).behaviour == 'connect_raises':
            raise ConnectionError('unreachable')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if type(self).behaviour == 'quit_raises':
            raise RuntimeError('QUIT failed after the server accepted the message')
        return False

    def starttls(self):
        return None

    def login(self, user, pw):
        return None

    def send_message(self, msg):
        if type(self).behaviour == 'send_raises':
            raise RuntimeError('connection reset during send')
        if type(self).behaviour == 'partial':
            return {'refused@example.invalid': (550, b'recipient rejected')}
        return None


def _patch_mailer(monkeypatch, behaviour, log_raises=False):
    _FakeSMTP.instances = 0
    _FakeSMTP.behaviour = behaviour
    monkeypatch.setattr(builtins, '_gmail_creds', lambda: ('u@x.y', 'pw'))
    monkeypatch.setattr(builtins.smtplib, 'SMTP', _FakeSMTP)
    monkeypatch.setattr(builtins.time, 'sleep', lambda s: None)
    if log_raises:
        def _boom(msg):
            raise RuntimeError('log write failed')
        monkeypatch.setattr(builtins, 'add_log', _boom)


def test_send_raise_is_unknown_without_resend(monkeypatch):
    _patch_mailer(monkeypatch, 'send_raises')
    res = builtins.tool_send_email('nobody@example.com', 't', 'b')
    assert 'outcome unknown' in res, res
    assert 'NOT resent' in res, res
    assert _FakeSMTP.instances == 1, \
        f'retried an ambiguous send {_FakeSMTP.instances} times'


def test_post_send_log_failure_still_success_no_resend(monkeypatch):
    _patch_mailer(monkeypatch, 'ok', log_raises=True)
    res = builtins.tool_send_email('nobody@example.com', 't', 'b')
    assert res == "Email sent to nobody@example.com: 't'.", res
    assert _FakeSMTP.instances == 1


def test_connect_failure_retries_then_gives_up(monkeypatch):
    _patch_mailer(monkeypatch, 'connect_raises')
    res = builtins.tool_send_email('nobody@example.com', 't', 'b')
    assert res == 'Could not send email to nobody@example.com.', res
    assert _FakeSMTP.instances == 2, \
        f'pre-send failures must retry once: {_FakeSMTP.instances}'


def test_smtp_cleanup_failure_after_acceptance_never_resends(monkeypatch):
    _patch_mailer(monkeypatch, 'quit_raises')
    res = builtins.tool_send_email('nobody@example.com', 't', 'b')
    assert _FakeSMTP.instances == 1
    assert res == "Email sent to nobody@example.com: 't'."


def test_ambiguous_send_remains_unverified_in_the_receipt_contract(monkeypatch):
    from aria.outcome import normalize, UNVERIFIED
    _patch_mailer(monkeypatch, 'send_raises')
    result = builtins.tool_send_email('nobody@example.com', 't', 'b')
    assert normalize(result).status == UNVERIFIED


def test_connection_failure_remains_failed_in_the_receipt_contract(monkeypatch):
    from aria.outcome import normalize, FAILED
    _patch_mailer(monkeypatch, 'connect_raises')
    assert normalize(builtins.tool_send_email('nobody@example.com', 't', 'b')).status == FAILED


def test_partial_recipient_refusal_is_not_reported_as_full_success(monkeypatch):
    from aria.outcome import normalize, UNVERIFIED
    _patch_mailer(monkeypatch, 'partial')
    result = builtins.tool_send_email('ok@example.invalid, refused@example.invalid', 't', 'b')
    assert normalize(result).status == UNVERIFIED
    assert 'refused@example.invalid' in result
    assert _FakeSMTP.instances == 1


# ------------------------------------------------------------ control: healing still heals

def test_transient_fetch_still_heals(monkeypatch):
    monkeypatch.setattr(self_healing.time, 'sleep', lambda s: None)
    healed, out, _msg = self_healing.attempt_auto_heal(
        'fetch_url', {}, 'timed out', lambda args: 'fetched')
    assert healed is True
    assert out == 'fetched'
