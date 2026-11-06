"""Background-job cancellation must kill the whole process tree.

Pass 25 (P2#4): cancel_background_job terminated only the shell,
so a job's descendants kept running orphaned. The job below
spawns a sleeper carrying a unique marker; after the cancel
neither the job nor the sleeper may survive.
"""
import importlib.util
import subprocess
import sys
import time
from pathlib import Path

from test_missions import env, memory  # noqa: F401  (env is a pytest fixture)
from aria import workspaces


def _load_workers():
    spec = importlib.util.spec_from_file_location(
        'aria.audit_workers_killtree',
        Path(__file__).parents[1] / 'aria/agent/workers.py')
    workers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workers)
    return workers


def _marker_alive(marker):
    found = subprocess.run(['pgrep', '-f', marker],
                           capture_output=True, text=True)
    return found.returncode == 0


def test_cancel_background_job_kills_descendants(env, monkeypatch):
    workers = _load_workers()
    marker = 'p2_4_worker_marker'
    command = (
        f'"{sys.executable}" -c "import subprocess, sys, time; '
        f"subprocess.Popen([sys.executable, '-c', "
        f"'import time; time.sleep(25)  # {marker}']); "
        'time.sleep(25)"')
    project = workspaces.create('Kill tree')
    try:
        with workspaces.use(project):
            memory.init_databases()
            job = workers.start_background_job(command, 'tree job')
        assert 'job_id' in job, job
        # Wait for the sleeper to exist before cancelling.
        for _ in range(60):
            if _marker_alive(marker):
                break
            time.sleep(0.05)
        assert _marker_alive(marker), 'sleeper never started'
        with workspaces.use(project):
            assert workers.cancel_background_job(job['job_id'])
        # The group kill is synchronous; the sleeper must be gone.
        assert not _marker_alive(marker)
    finally:
        subprocess.run(['pkill', '-f', marker], capture_output=True)
