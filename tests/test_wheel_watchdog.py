"""Wheel watchdog: wheels stop by themselves when commands go silent
(pass 26 B2#7).

The desktop process is the only thing that normally stops the wheels
(hardware.py tool_drive arms a threading.Timer, max 30 s) — if it dies
mid-drive, no stop is ever sent. The firmware (arduino/aria_body/
aria_body.ino) now records lastWheelCmd in the W handler and stops the
wheels 35 s after the last W command; sim/robot_sim.py mirrors that
watchdog so the behaviour is testable without hardware.

Run per-file:  python3 -m pytest tests/test_wheel_watchdog.py -v
"""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "robot_sim", Path(__file__).parents[1] / "sim" / "robot_sim.py")
robot_sim = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(robot_sim)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _body():
    clock = FakeClock()
    return robot_sim.VirtualBody(clock=clock), clock


def _wheels(body):
    return body.left, body.right


def test_wheels_stop_after_watchdog_interval_with_no_further_commands():
    body, clock = _body()
    body.handle_line("W50,50")
    assert _wheels(body) == (50, 50)
    clock.advance(36)
    body.watchdog_check()
    assert _wheels(body) == (0, 0)


def test_wheels_keep_driving_before_watchdog_interval():
    body, clock = _body()
    body.handle_line("W50,-50")
    clock.advance(34)
    body.watchdog_check()
    assert _wheels(body) == (50, -50)


def test_fresh_command_resets_watchdog_timer():
    body, clock = _body()
    body.handle_line("W50,50")
    clock.advance(30)
    body.handle_line("W50,50")  # fresh command restarts the 35 s window
    clock.advance(30)
    body.watchdog_check()
    assert _wheels(body) == (50, 50)
    clock.advance(6)
    body.watchdog_check()
    assert _wheels(body) == (0, 0)


def test_stopped_wheels_never_trigger_watchdog():
    body, clock = _body()
    clock.advance(100)
    body.watchdog_check()
    assert _wheels(body) == (0, 0)


def test_stop_command_halts_immediately_and_clears_watchdog():
    body, clock = _body()
    body.handle_line("W50,50")
    body.handle_line("S")
    assert _wheels(body) == (0, 0)
    clock.advance(100)
    body.watchdog_check()
    assert _wheels(body) == (0, 0)
