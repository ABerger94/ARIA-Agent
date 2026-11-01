"""Real dispatcher and SQLite receipts, with fake external effects only."""
import threading

import pytest
from test_missions import env, missions, dispatch, approval, _create, _approve_plan, _row, _calls
from aria import runtime, action_journal


def test_receipt_survives_crash_before_mission_checkpoint(env, monkeypatch):
    mid, _ = _create('send once', [('send_email', {'to':'fake@example.invalid','subject':'fake','body':'test only'})],
                     pre_approved_irreversible='send_email')
    _approve_plan(mid)
    execute = missions._execute_step
    class Crash(BaseException): pass
    def crash(*args, **kwargs):
        execute(*args, **kwargs)
        raise Crash()
    monkeypatch.setattr(missions, '_execute_step', crash)
    with pytest.raises(Crash): missions._run_mission(mid)
    monkeypatch.setattr(missions, '_execute_step', execute)
    missions._run_mission(mid)
    assert len(_calls('send_email')) == 1
    assert _row(mid)['status'] == 'done'


def test_uncertain_external_effect_is_never_replayed(env, monkeypatch):
    mid, _ = _create('send uncertain', [('send_email', {'to':'fake@example.invalid','subject':'fake','body':'test only'})],
                     pre_approved_irreversible='send_email')
    _approve_plan(mid)
    original = action_journal.finish
    class Crash(BaseException): pass
    monkeypatch.setattr(action_journal, 'finish', lambda *a: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash): missions._run_mission(mid)
    monkeypatch.setattr(action_journal, 'finish', original)
    missions._run_mission(mid)
    assert len(_calls('send_email')) == 1
    assert _row(mid)['status'] == 'blocked_input'
    assert missions._step_rows(mid)[0]['status'] == 'uncertain'


@pytest.mark.parametrize('status,expected', [('failed','failed'),('unverified','blocked_input'),
                                          ('started','blocked_input'),('ask','blocked_input')])
def test_typed_result_is_not_false_success(env, status, expected):
    dispatch.register_tool('probe_result', lambda a: f'status={status} reason=test')
    mid, _ = _create('check outcome', [('probe_result', {})]); _approve_plan(mid)
    missions._run_mission(mid)
    assert _row(mid)['status'] == expected


def test_verifier_does_not_repeat_email(env):
    mid, _ = _create('one send', [('send_email', {'to':'fake@example.invalid','subject':'fake','body':'test only'})],
                     pre_approved_irreversible='send_email')
    _approve_plan(mid); missions.set_verifier(lambda *a: False)
    missions._run_mission(mid)
    assert _row(mid)['status'] == 'done_unverified'
    assert len(_calls('send_email')) == 1


def test_background_reset_does_not_clear_foreground_guard(env):
    with runtime.execution('foreground'):
        dispatch.execute_tool('fake_note', {'text':'A'})
        def worker():
            with runtime.execution('task'):
                dispatch.reset_turn_state(); dispatch.execute_tool('fake_note', {'text':'B'})
        thread = threading.Thread(target=worker); thread.start(); thread.join()
        result, _ = dispatch.execute_tool('fake_note', {'text':'A'})
        assert result.startswith('[Duplicate call blocked')


def test_cancelled_execution_cannot_start_tool(env):
    with runtime.execution('foreground') as ctx:
        ctx.cancel.set()
        with pytest.raises(runtime.Cancelled): dispatch.execute_tool('fake_note', {'text':'no'})
    assert _calls('fake_note') == []


def test_approval_replay_keeps_action_id_and_project(env):
    with runtime.execution('mission'):
        with runtime.operation('stable-approval-action'):
            text = approval.request_approval('send_email', {'to':'fake@example.invalid','subject':'fake','body':'test only'})
    token = text.split('token=', 1)[1].split(']', 1)[0]
    approval.approve_token(token)
    assert action_journal.get('stable-approval-action')['state'] == 'done'


def test_reconciliation_resumes_without_repeating(env, monkeypatch):
    mid, _ = _create('uncertain', [('send_email', {'to':'fake@example.invalid','subject':'fake','body':'test only'})],
                     pre_approved_irreversible='send_email')
    _approve_plan(mid)
    step = missions._step_rows(mid)[0]
    action_journal.begin(f'mission:{mid}:step:{step["id"]}', 'send_email', step['args'])
    missions._run_mission(mid)
    missions.record_receipt(mid, step['id'], 'User checked: sent message id 123')
    missions._run_mission(mid)
    assert _row(mid)['status'] == 'done'
    assert _calls('send_email') == []


def test_pause_resume_cannot_skip_uncertain_step(env):
    dispatch.register_tool('uncertain_probe', lambda a: 'status=unverified reason=lost-receipt')
    mid, _ = _create('preserve order', [('uncertain_probe', {}), ('fake_note', {'text':'later'})])
    _approve_plan(mid); missions._run_mission(mid)
    missions.pause_mission(mid); missions.pause_mission(mid)
    missions._run_mission(mid)
    assert _row(mid)['status'] == 'blocked_input'
    assert _calls('fake_note') == []
