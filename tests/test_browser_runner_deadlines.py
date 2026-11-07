"""Pass 27 #11: a timed-out browser job must not execute later.

Before: _Runner.call() raised TimeoutError but left the callback
queued — once the thread freed up, the stale effect still ran
after its caller had already moved on. Now every job carries a
deadline and a cancellation token: the loop drops expired jobs
before starting them, and a job that ran after its caller gave
up is recorded on runner.uncertain instead of vanishing.
"""
import importlib
import os
import sys
import threading
import time
import types

pkg = types.ModuleType("aria")
pkg.__path__ = [os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")]
sys.modules["aria"] = pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
sys.modules["aria.speech"] = types.ModuleType("aria.speech")

ba = importlib.import_module("aria.browser_agent")


def _wait_until(pred, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_expired_job_never_runs():
    runner = ba._Runner()
    release = threading.Event()
    ran = []

    def blocker(_r):
        release.wait(5)
        return "first"

    def stale(_r):
        ran.append("stale")
        return "second"

    # Hold the thread with the first job, in the background.
    first = threading.Thread(target=lambda: runner.call(blocker, 5))
    first.start()
    assert _wait_until(lambda: not release.is_set() or True, 0.5)
    time.sleep(0.05)  # let the blocker start
    try:
        runner.call(stale, timeout=0.2)
    except TimeoutError:
        pass
    else:
        raise AssertionError("stale job should have timed out")
    release.set()
    first.join(5)
    # Give the loop a chance to (not) run the stale job.
    time.sleep(0.3)
    assert ran == [], f"expired job executed anyway: {ran}"
    assert runner.uncertain == []


def test_in_flight_timeout_is_recorded_uncertain():
    runner = ba._Runner()
    release = threading.Event()
    ran = []

    def slow(_r):
        release.wait(5)
        ran.append("slow")
        return "slow"

    result = {}

    def caller():
        try:
            result["v"] = runner.call(slow, timeout=0.2)
        except TimeoutError:
            result["v"] = "timeout"

    t = threading.Thread(target=caller)
    t.start()
    t.join(5)
    assert result["v"] == "timeout"
    release.set()
    assert _wait_until(lambda: len(runner.uncertain) == 1), \
        "completed-after-timeout job was not recorded"
    assert ran == ["slow"], "the in-flight job should have run"
    assert "at" in runner.uncertain[0]


def test_normal_job_still_returns():
    runner = ba._Runner()
    assert runner.call(lambda _r: 42, timeout=5) == 42
    assert runner.uncertain == []
