"""Audit regressions: real dispatch/storage with external effects replaced."""
import ast
import importlib.util
from pathlib import Path
import sys
import threading
import types

import pytest

from test_missions import env, config, memory, approval, dispatch, CALLS
from aria import action_journal, fleet, runtime, workspaces
from aria.outcome import normalize, FAILED, UNVERIFIED
from aria.tools import builtins, sandbox


def test_safe_mode_blocks_an_already_pending_approval(env, monkeypatch):
    approval.set_mode('confirm-risky')
    result, _ = dispatch.execute_tool('send_email', {
        'to': 'test@example.invalid', 'subject': 'test', 'body': 'test'})
    token = result.split('token=', 1)[1].split(']', 1)[0]
    monkeypatch.setattr(config, 'is_safe_mode', lambda: True)
    assert approval.approve_token(token).startswith('[Safe mode')
    assert CALLS == []


@pytest.mark.parametrize('value', [0, 0.0, False, [], {}])
def test_required_arguments_allow_present_falsy_values(value):
    declarations = {'test': {'parameters': {'required': ['value']}}}
    assert sandbox.missing_required_args('test', {'value': value}, declarations) == []


@pytest.mark.parametrize('args', [{}, {'value': None}, {'value': ''}, {'value': '  '}])
def test_required_arguments_still_reject_missing_or_blank_values(args):
    declarations = {'test': {'parameters': {'required': ['value']}}}
    assert sandbox.missing_required_args('test', args, declarations) == ['value']


def test_failed_python_exit_is_not_recorded_as_done(tmp_path):
    result = sandbox.tool_run_python('raise SystemExit(7)', workspace_dir=str(tmp_path))
    assert normalize(result).status == FAILED
    assert '7' in str(result)


def test_python_timeout_is_not_recorded_as_done(tmp_path):
    result = sandbox.tool_run_python('while True: pass', workspace_dir=str(tmp_path), timeout=.05)
    assert normalize(result).status == UNVERIFIED


def test_tool_owned_error_messages_keep_failure_status(env, monkeypatch):
    from aria.tools import fileproc, selfedit, winctl
    monkeypatch.setattr(builtins, 'GUI_AVAILABLE', False)
    monkeypatch.setattr(winctl, '_is_windows', lambda: False)
    results = [builtins.tool_gui_click(1, 1), builtins.tool_gui_type('test'),
               winctl.tool_window_snap('test', 'left'),
               winctl.tool_launch_app('test'),
               selfedit.tool_edit_own_code(str(env.tmp / 'outside.py'), 'pass'),
               selfedit.tool_patch_own_code(str(env.tmp / 'outside.py'), 'pass', 'pass'),
               fileproc.tool_process_file(str(env.tmp / 'missing.txt'))]
    assert [normalize(result).status for result in results] == [FAILED] * len(results)


def test_read_content_that_looks_like_a_tool_error_is_still_data(files):
    (files / 'example.txt').write_text('[Self-edit failed: example in a document]')
    assert normalize(builtins.tool_read_file('example.txt')).status != FAILED


@pytest.fixture
def files(env, monkeypatch):
    root = env.tmp / 'files'
    root.mkdir()
    monkeypatch.setattr(config, 'WORKSPACE_DIR', str(root))
    monkeypatch.setattr(builtins, 'WORKSPACE_DIR', str(root))
    return root


def test_workspace_file_tools_cannot_follow_a_link_outside_the_workspace(files, env):
    outside = env.tmp / 'outside'
    outside.mkdir()
    (outside / 'secret.txt').write_text('private')
    (files / 'linked').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        builtins.tool_write_file('linked/secret.txt', 'overwritten')
    with pytest.raises(ValueError):
        builtins.tool_read_file('linked/secret.txt')
    assert (outside / 'secret.txt').read_text() == 'private'


@pytest.mark.parametrize('name', ['C:/outside.txt', 'C:outside.txt', '//server/share/file.txt', '../outside.txt'])
def test_workspace_file_tools_reject_cross_platform_escapes(files, name):
    with pytest.raises(ValueError):
        builtins.tool_write_file(name, 'test')


def test_workspace_listing_uses_the_bound_project(files):
    (files / 'personal.txt').write_text('personal')
    project = workspaces.create('Project files')
    with workspaces.use(project):
        builtins.tool_write_file('project.txt', 'project')
        listing = builtins.tool_list_files()
    assert 'project.txt' in listing and 'personal.txt' not in listing


def test_read_fallback_cannot_bypass_project_file_roots(files):
    allowed = files / 'allowed'
    allowed.mkdir()
    (files / 'secret.txt').write_text('private')
    workspaces.update_settings({'file_roots': [str(allowed)]})
    result, _ = dispatch.execute_tool('read_file', {'filename': 'allowed/secret.txt'})
    assert 'private' not in result
    assert normalize(result).status == FAILED


def test_project_chat_display_is_bounded(files, monkeypatch):
    project = workspaces.create('Chat')
    monkeypatch.setattr(memory, 'spine_append', lambda *a: None)
    with workspaces.use(project):
        memory.init_databases()
        for i in range(90):
            memory.log_conversation('Alek', str(i))
        rows = memory.get_display_chat_log()
        assert len(rows) == 60
        assert rows[-1][-1] == '89'
        memory._PROJECT_DISPLAY_LOGS.clear()
        reloaded = memory.get_display_chat_log()
        assert len(reloaded) == 60 and reloaded[-1][-1] == '89'
        assert reloaded[0][-1] == '30'


def _real_scheduler():
    spec = importlib.util.spec_from_file_location(
        'aria.audit_scheduler', Path(__file__).parents[1] / 'aria/scheduler.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scheduled_thread_retains_its_project_after_selection_changes(files, monkeypatch):
    import sqlite3
    from test_missions import missions
    scheduler = _real_scheduler()
    a, b = workspaces.create('Schedule A'), workspaces.create('Selection B')
    with workspaces.use(a):
        memory.init_databases()
        with sqlite3.connect(memory.db_path()) as conn:
            conn.execute("INSERT INTO scheduled_tasks(kind,prompt,interval_s,next_run) "
                         "VALUES('interval','project work',3600,'2000-01-01 00:00:00')")
    spawned = []
    class Thread:
        def __init__(self, target, args=(), **kwargs):
            self.target, self.args = target, args
        def start(self):
            spawned.append(self)
    class StopTick(BaseException):
        pass
    def sleep(seconds):
        if seconds == 10:
            raise StopTick()
    monkeypatch.setattr(scheduler, 'threading', types.SimpleNamespace(Thread=Thread))
    monkeypatch.setattr(scheduler, 'time', types.SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(scheduler, '_reminder_gate_tick', lambda *a: None)
    monkeypatch.setattr(missions, 'executor_tick', lambda: None)
    observed = []
    with pytest.raises(StopTick):
        scheduler.scheduler_loop(run_agent_fn=lambda prompt: observed.append(workspaces.current()))
    workspaces.select(b)
    assert len(spawned) == 1
    spawned[0].target(*spawned[0].args)
    assert observed == [a]


def test_timer_is_daemonized_and_keeps_its_origin_project(files, monkeypatch):
    scheduler = _real_scheduler()
    a, b = workspaces.create('Timer A'), workspaces.create('Selection B')
    timers, observed = [], []
    class Timer:
        daemon = False
        def __init__(self, seconds, function):
            self.function = function
        def start(self):
            timers.append(self)
    monkeypatch.setattr(scheduler, 'threading', types.SimpleNamespace(Timer=Timer))
    monkeypatch.setattr(scheduler, 'add_log', lambda message: observed.append(workspaces.current()))
    with workspaces.use(a):
        scheduler.tool_set_timer('1h', 'test')
    workspaces.select(b)
    assert timers[0].daemon
    timers[0].function()
    assert observed == [a]


def test_trigger_definitions_do_not_cross_projects(files):
    from aria import triggers
    a, b = workspaces.create('Triggers A'), workspaces.create('Triggers B')
    with workspaces.use(a):
        assert triggers.create_trigger('A only', 'folder', 'test')['ok']
    with workspaces.use(b):
        assert triggers.load_triggers() == []
    with workspaces.use(a):
        assert [row['name'] for row in triggers.load_triggers()] == ['A only']


def test_background_jobs_with_equal_ids_keep_project_identity(files, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'aria.audit_workers', Path(__file__).parents[1] / 'aria/agent/workers.py')
    workers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workers)
    processes, launches = [], []
    class Process:
        def __init__(self):
            self.pid = 9000 + len(processes)
            self.code = None
            processes.append(self)
        def poll(self):
            return self.code
        def terminate(self):
            self.code = -15
        def kill(self):
            self.code = -9
    def popen(command, **kwargs):
        launches.append(kwargs['cwd'])
        return Process()
    monkeypatch.setattr(workers.subprocess, 'Popen', popen)
    monkeypatch.setattr(workers, '_ACTIVE_JOBS', {})
    monkeypatch.setattr(workers.time, 'sleep', lambda *a: None)
    a, b = workspaces.create('Jobs A'), workspaces.create('Jobs B')
    with workspaces.use(a):
        memory.init_databases()
        first = workers.start_background_job('fake command A')
        root_a = workspaces.path('', str(files))
    with workspaces.use(b):
        memory.init_databases()
        second = workers.start_background_job('fake command B')
        root_b = workspaces.path('', str(files))
    assert first['job_id'] == second['job_id'] == 1
    with workspaces.use(a):
        assert workers.cancel_background_job(1)
    assert processes[0].code is not None and processes[1].code is None
    assert launches == [root_a, root_b]
    processes[1].code = 0
    workers._poll_background_jobs()
    with workspaces.use(b):
        assert memory.job_db_list()[0]['status'] == 'completed'
    assert workers._ACTIVE_JOBS == {}


def test_saved_pid_alone_cannot_authorize_terminating_a_process(files, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'aria.audit_workers', Path(__file__).parents[1] / 'aria/agent/workers.py')
    workers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workers)
    jid = memory.job_db_create('old job', 'old command', 123456, '')
    monkeypatch.setattr(workers.psutil, 'Process', lambda pid: pytest.fail('Used a stale persisted PID'))
    assert not workers.cancel_background_job(jid)
    assert memory.job_db_list()[0]['status'] == 'running'


def test_gui_click_and_type_serialize_across_execution_contexts(env):
    entered, release, typed = threading.Event(), threading.Event(), threading.Event()
    def click(args):
        entered.set()
        assert release.wait(3)
        return 'clicked'
    dispatch.register_tool('gui_click', click)
    dispatch.register_tool('gui_type', lambda args: typed.set() or 'typed')
    errors = []
    def run(tool, args):
        try:
            with runtime.execution('task'):
                dispatch.execute_tool(tool, args)
        except Exception as exc:
            errors.append(exc)
    first = threading.Thread(target=run, args=('gui_click', {'x': 1, 'y': 1}))
    second = threading.Thread(target=run, args=('gui_type', {'text': 'test'}))
    first.start()
    assert entered.wait(3)
    try:
        second.start()
        assert not typed.wait(.15), 'Typing interleaved with a desktop click'
    finally:
        release.set()
        first.join(3)
        second.join(3)
    assert typed.is_set() and errors == []


def test_tool_audit_masks_a_new_credential_before_it_is_configured(env, monkeypatch):
    events = []
    monkeypatch.setattr(dispatch, '_SPINE_HOOK', lambda kind, payload: events.append(payload))
    monkeypatch.setitem(dispatch._REGISTRY, 'gmail_setup', lambda args: 'saved')
    dispatch.execute_tool('gmail_setup', {'gmail_user': 'test@example.invalid',
                                         'app_password': 'NEW-TEST-SECRET-ONLY'})
    assert 'NEW-TEST-SECRET-ONLY' not in str(events)


def test_provider_keys_and_nested_credentials_are_redacted(env, monkeypatch):
    monkeypatch.setitem(config._KEYS, 'GEMINI_API_KEYS', ['TEST-POOL-KEY-SECRET'])
    monkeypatch.setitem(config._KEYS, 'MISTRAL_API_KEY', 'TEST-MISTRAL-KEY-SECRET')
    assert 'TEST-POOL-KEY-SECRET' not in config.redact('failed TEST-POOL-KEY-SECRET')
    assert 'TEST-MISTRAL-KEY-SECRET' not in config.redact('failed TEST-MISTRAL-KEY-SECRET')
    destination = env.tmp / 'spine.jsonl'
    monkeypatch.setattr(memory, 'SPINE_PATH', str(destination))
    memory.spine_append('test', {'nested': {'authorization': 'NEW-TEST-AUTH-SECRET'}})
    assert 'NEW-TEST-AUTH-SECRET' not in destination.read_text()


def test_primary_saves_queue_new_backup_snapshots(files, monkeypatch):
    from aria import backup_mirror
    calls = []
    monkeypatch.setattr(backup_mirror, 'queue_snapshot', lambda: calls.append(workspaces.current()))
    monkeypatch.setattr(memory, '_embed', lambda *a: None)
    monkeypatch.setattr(config, 'SETTINGS_FILE', str(files / 'settings.json'))
    monkeypatch.setattr(memory, 'CHAT_LOG_FILE', str(files / 'chat_history.md'))
    assert config.settings_save({'test': True})
    memory.memory_save('test', 'test', 'new value')
    memory.log_conversation('Alek', 'new conversation')
    assert calls == ['default', 'default', 'default']


def test_background_mirror_refreshes_and_keeps_projects_separate(files, monkeypatch):
    import sqlite3
    import time
    from contextlib import closing
    from aria import backup_mirror as bm
    mirror = files / 'mirror'
    monkeypatch.setattr(bm, '_get_setting', lambda key, default=None: str(mirror))
    monkeypatch.setattr(memory, '_embed', lambda *a: None)
    projects = [workspaces.create('Backup A'), workspaces.create('Backup B')]
    def wait_for_copy():
        deadline = time.monotonic() + 3
        while bm._WORKER is not None and time.monotonic() < deadline:
            time.sleep(.01)
        assert bm._WORKER is None
    for index, project in enumerate(projects):
        with workspaces.use(project):
            memory.init_databases()
            memory.memory_save('test', 'key', f'value {index}')
        wait_for_copy()
    for index, project in enumerate(projects):
        with closing(sqlite3.connect(mirror / 'projects' / project / 'aria_memory.db')) as conn:
            assert conn.execute('SELECT value FROM memory WHERE key=?', ('key',)).fetchone() == (f'value {index}',)
    with workspaces.use(projects[0]):
        memory.memory_save('test', 'key', 'updated value')
    wait_for_copy()
    with closing(sqlite3.connect(mirror / 'projects' / projects[0] / 'aria_memory.db')) as conn:
        assert conn.execute('SELECT value FROM memory WHERE key=?', ('key',)).fetchone() == ('updated value',)


def test_cached_receipt_does_not_leave_a_phantom_fleet_call(env):
    fleet.reset_for_tests()
    with runtime.execution('task'):
        with runtime.operation('cached-fleet-probe'):
            dispatch.execute_tool('fake_note', {'text': 'test'})
        dispatch.reset_turn_state()
        with runtime.operation('cached-fleet-probe'):
            dispatch.execute_tool('fake_note', {'text': 'test'})
    assert fleet.current() == []


def test_cancelled_handler_does_not_leave_a_phantom_fleet_call(env):
    fleet.reset_for_tests()
    def cancel(args):
        raise runtime.Cancelled('cancelled in the handler')
    dispatch.register_tool('audit_cancel', cancel)
    with pytest.raises(runtime.Cancelled):
        dispatch.execute_tool('audit_cancel', {})
    assert fleet.current() == []


def test_staged_file_still_obeys_project_file_roots(files, env, monkeypatch):
    from aria.tools import fileproc
    allowed = files / 'allowed'
    allowed.mkdir()
    secret = env.tmp / 'private.txt'
    secret.write_text('private')
    workspaces.update_settings({'file_roots': [str(allowed)]})
    fake_frame = types.ModuleType('aria.frame')
    fake_frame.staged_file = lambda: str(secret)
    monkeypatch.setitem(sys.modules, 'aria.frame', fake_frame)
    monkeypatch.setattr(sys.modules['aria'], 'frame', fake_frame, raising=False)
    reads = []
    monkeypatch.setattr(fileproc, 'summarize_file', lambda *a: reads.append(a) or 'private')
    result = fileproc.tool_process_file()
    assert normalize(result).status == FAILED and reads == []


def test_live_transcripts_reach_the_real_logging_callback(env, monkeypatch):
    # Execute the production startup function without importing audio devices.
    source = ast.parse((Path(__file__).parents[1] / 'aria/main.py').read_text())
    function = next(n for n in source.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_start_live_if_configured')
    captured, logs = {}, []
    live = types.ModuleType('aria.live')
    live.DEFAULT_VOICE = 'test'
    def manager(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(start=lambda: True)
    live.LiveManager = manager
    monkeypatch.setitem(sys.modules, 'aria.live', live)
    monkeypatch.setattr(sys.modules['aria'], 'live', live, raising=False)
    settings = types.SimpleNamespace(get_setting=lambda key, default=None: 'live' if key == 'voice_mode' else default,
                                     GEMINI_API_KEY='test', ARIA_SOUL='test')
    schemas = types.ModuleType('aria.tools.schemas')
    schemas.ALL_FUNCTION_DECLARATIONS = []
    monkeypatch.setitem(sys.modules, 'aria.tools.schemas', schemas)
    namespace = {'_config_silent': settings, '_mic_device': lambda: None,
                 'presence_allows_speech': lambda: True, 'add_log': logs.append,
                 'memory': types.SimpleNamespace(log_conversation=lambda *a: logs.append(a))}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<live startup>', 'exec'), namespace)
    assert namespace['_start_live_if_configured']()
    captured['on_user_text']('hello')
    captured['on_assistant_text']('hi')
    assert ('Alek', 'hello') in logs and ('A.R.I.A.', 'hi') in logs
