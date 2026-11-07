"""Pass 28 #22: background jobs must not run forever after ARIA
loses their process handles.

Before: a job's process lived only in the in-memory _ACTIVE_JOBS
dict; after a restart the dict was empty, the DB row stayed
'running' forever, the supervisor never polled the orphan, and
cancel_background_job refused to kill by PID alone — so the
process ran unbounded and its status lied.

Fix contract pinned here:
* on supervisor start, jobs whose DB row says 'running' but
  which have no live handle are recovered: a still-running
  process whose command line matches the recorded command is
  RE-ADOPTED (a psutil-backed handle joins _ACTIVE_JOBS, so
  the normal poll + cancel paths work again);
* a job whose PID is gone — or whose PID now belongs to a
  different process (PID reuse) — is settled as 'failed' with
  no invented exit code, and is never touched;
* a re-adopted job that later exits is marked 'failed' with
  exit_code NULL: its exit code died with its original parent,
  so the supervisor never invents one (fail-closed).
"""
import importlib.util
import os
import time
from pathlib import Path

import psutil
import pytest

from test_missions import env, memory  # noqa: F401  (env is a pytest fixture)
from aria import workspaces
from aria.memory import job_db_create, job_db_list


def _load_workers():
    spec = importlib.util.spec_from_file_location(
        'aria.audit_workers_readopt',
        Path(__file__).parents[1] / 'aria/agent/workers.py')
    workers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workers)
    return workers


@pytest.fixture()
def project(env):
    pid = workspaces.create('Readopt')
    with workspaces.use(pid):
        memory.init_databases()
        yield pid


def _row(workers, pid, jid):
    with workspaces.use(pid):
        rows = job_db_list(50)
    return next((r for r in rows if r["id"] == jid), None)


def _wait_for(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def _proc_gone(pid):
    """True once the PID is reaped or a zombie: in-process the
    test process is the shell's parent, so a killed shell lingers
    as a zombie until reaped (in production the parent is dead
    and init reaps it, so the PID vanishes)."""
    try:
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            return True
    except ChildProcessError:
        return True
    except OSError:
        pass
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def test_live_job_is_readopted_after_handle_loss(env, project):
    workers = _load_workers()
    with workspaces.use(project):
        job = workers.start_background_job("sleep 25", "sleeper")
    assert "job_id" in job, job
    jid, pid = job["job_id"], job["pid"]
    assert _wait_for(lambda: psutil.pid_exists(pid))
    # Simulate a restart: the in-memory handles are gone, the DB
    # row still says 'running', the process still lives.
    workers._ACTIVE_JOBS.clear()
    workers._readopt_orphaned_jobs()
    assert (project, jid) in workers._ACTIVE_JOBS
    # Cancel works again through the adopted handle.
    with workspaces.use(project):
        assert workers.cancel_background_job(jid) is True
    assert _wait_for(lambda: _proc_gone(pid))
    row = _row(workers, project, jid)
    assert row["status"] == "killed", row


def test_dead_job_is_settled_failed_not_left_running(env, project):
    workers = _load_workers()
    with workspaces.use(project):
        job = workers.start_background_job("sleep 0.1", "brief")
    assert "job_id" in job, job
    jid, pid = job["job_id"], job["pid"]
    assert _wait_for(lambda: _proc_gone(pid))
    workers._ACTIVE_JOBS.clear()
    workers._readopt_orphaned_jobs()
    assert (project, jid) not in workers._ACTIVE_JOBS
    row = _row(workers, project, jid)
    assert row["status"] == "failed", row
    assert row["exit_code"] is None, row


def test_reused_pid_is_never_touched(env, project):
    workers = _load_workers()
    # A row whose PID belongs to a process running something else
    # (here: this very test process) must be settled without the
    # process being signalled.
    with workspaces.use(project):
        jid = job_db_create(
            "fake", "sleep 999 --never-this-command",
            os.getpid(), "/tmp/no-such-job.log")
        workers._readopt_orphaned_jobs()
    assert (project, jid) not in workers._ACTIVE_JOBS
    assert psutil.pid_exists(os.getpid())
    row = _row(workers, project, jid)
    assert row["status"] == "failed", row
    assert row["exit_code"] is None, row
