"""get_background_job_log must read only the tail (pass 28 #33).

The old code did f.readlines() on the whole log file and then
kept the last 40 lines — a job that logs verbosely for hours
materialises a multi-GB file to show 40 lines. The fix seeks to
a bounded tail window first.

Run per-file (own process), like every suite in this repo.
"""
import importlib.util
from pathlib import Path

import pytest

from test_missions import env, memory  # noqa: F401  (env is a pytest fixture)
from aria import workspaces
from aria.memory import job_db_create


def _load_workers():
    spec = importlib.util.spec_from_file_location(
        'aria.audit_workers_logtail',
        Path(__file__).parents[1] / 'aria/agent/workers.py')
    workers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workers)
    return workers


@pytest.fixture()
def project(env):
    pid = workspaces.create('LogTail')
    with workspaces.use(pid):
        memory.init_databases()
        yield pid


def _job_with_log(project, log_path):
    with workspaces.use(project):
        return job_db_create("big", "sleep 1", 999999, str(log_path))


def test_job_log_returns_only_tail(project, tmp_path):
    # Preservation: the tail selection (last 40 lines) was already
    # right — the fix is the bounded READ, proven by the huge-line
    # test below. This pins the selection through the fix.
    workers = _load_workers()
    log = tmp_path / "job.log"
    log.write_text("".join(f"line {i}\n" for i in range(10000)))
    assert log.stat().st_size > 65536  # bigger than the tail window
    jid = _job_with_log(project, log)
    out = workers.get_background_job_log(jid)
    assert "line 9999" in out, out[-200:]
    assert "line 0\n" not in out
    assert "line 9960" in out  # the last 40 lines
    assert len(out) < 70000  # bounded read, not the whole ~120KB log
    print("ok job_log_returns_only_tail")


def test_job_log_small_file_returns_everything(project, tmp_path):
    workers = _load_workers()
    log = tmp_path / "small.log"
    log.write_text("".join(f"line {i}\n" for i in range(5)))
    jid = _job_with_log(project, log)
    out = workers.get_background_job_log(jid)
    for i in range(5):
        assert f"line {i}" in out
    print("ok job_log_small_file_returns_everything")


def test_job_log_single_huge_line_still_returns_something(project, tmp_path):
    workers = _load_workers()
    log = tmp_path / "huge.log"
    log.write_text("x" * 200000)
    jid = _job_with_log(project, log)
    out = workers.get_background_job_log(jid)
    assert "x" * 100 in out  # the tail fragment, not "(no output...)"
    assert len(out) < 70000
    print("ok job_log_single_huge_line_still_returns_something")


def test_job_log_missing_job_and_missing_file(project, tmp_path):
    workers = _load_workers()
    assert "not found" in workers.get_background_job_log(424242)
    jid = _job_with_log(project, tmp_path / "gone.log")
    assert "does not exist" in workers.get_background_job_log(jid)
    print("ok job_log_missing_job_and_missing_file")
