"""Presence monitor (ported from Mark-LVI core/presence.py).

Pins the v1 contract: idle or lock folds to AWAY only when no veto
is active; a return needs a real signal (unlock transition or fresh
input); the locked() False LEVEL never ends an absence by itself
(the 2026-10-24 flap); both signals unknown fails open to PRESENT;
the return greeting is threshold- and quiet-hours-gated.
"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'presence_test', Path(__file__).parents[1] / 'aria' / 'presence.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _monitor(clock, events, **kw):
    return p.PresenceMonitor(
        monotonic=clock,
        on_away=lambda reason: events.append(('away', reason)),
        on_return=lambda away_s, greet: events.append(('return', away_s, greet)),
        **kw)


def test_idle_past_threshold_goes_away():
    clock, events = Clock(), []
    m = _monitor(clock, events, idle_signal=lambda: 601.0,
                 locked_signal=lambda: False, idle_after_s=600)
    m.tick()
    assert m.present is False and m.away_reason == 'idle'
    assert events == [('away', 'idle')]


def test_media_playing_vetoes_away():
    clock, events = Clock(), []
    m = _monitor(clock, events, idle_signal=lambda: 9999.0,
                 locked_signal=lambda: False, media_signal=lambda: True,
                 idle_after_s=600)
    m.tick()
    assert m.present is True and events == []


def test_lock_goes_away_and_unlock_transition_returns_with_greeting():
    clock, events = Clock(), []
    state = {'locked': True}
    m = _monitor(clock, events, idle_signal=lambda: 0.0,
                 locked_signal=lambda: state['locked'],
                 idle_after_s=600, greet_after_s=1800, hour_fn=lambda: 14)
    m.tick()
    assert m.present is False and m.away_reason == 'lock'
    clock.t = 1900.0
    state['locked'] = False
    m.tick()
    assert m.present is True
    assert events[-1] == ('return', 1900.0, True)


def test_locked_false_level_does_not_end_absence():
    # The flap specimen: away via idle, machine unlocked the whole
    # time — locked() reading False every tick must NOT flap back.
    clock, events = Clock(), []
    m = _monitor(clock, events, idle_signal=lambda: 9999.0,
                 locked_signal=lambda: False, idle_after_s=600)
    m.tick()
    assert m.present is False
    for _ in range(5):
        m.tick()
    assert m.present is False
    assert [e[0] for e in events] == ['away']


def test_short_absence_returns_without_greeting():
    clock, events = Clock(), []
    idle = {'s': 9999.0}
    m = _monitor(clock, events, idle_signal=lambda: idle['s'],
                 locked_signal=lambda: False, idle_after_s=600,
                 greet_after_s=1800)
    m.tick()
    assert m.present is False
    clock.t = 300.0
    idle['s'] = 0.0                       # fresh input
    m.tick()
    assert m.present is True
    assert events[-1] == ('return', 300.0, False)


def test_both_signals_unknown_fails_open():
    clock, events = Clock(), []
    sig = {'idle': 9999.0, 'locked': False}
    m = _monitor(clock, events,
                 idle_signal=lambda: sig['idle'],
                 locked_signal=lambda: sig['locked'], idle_after_s=600)
    m.tick()
    assert m.present is False
    sig['idle'] = None
    sig['locked'] = None
    m.tick()
    assert m.present is True             # cannot confirm absence


def test_quiet_hours_suppress_greeting():
    clock, events = Clock(), []
    state = {'locked': True}
    m = _monitor(clock, events, idle_signal=lambda: 0.0,
                 locked_signal=lambda: state['locked'],
                 greet_after_s=1800, hour_fn=lambda: 2,
                 quiet_hours=(23, 7))
    m.tick()
    clock.t = 4000.0
    state['locked'] = False
    m.tick()
    assert events[-1] == ('return', 4000.0, False)


def test_session_counts_as_activity_boundary():
    assert p.session_counts_as_activity(True, 599.0, 600) is True
    assert p.session_counts_as_activity(True, 600.0, 600) is False
    assert p.session_counts_as_activity(False, 0.0, 600) is False
    assert p.session_counts_as_activity(True, 'junk', 600) is False


def test_greeting_for_rotates_and_long_absence():
    assert p.greeting_for(9, 60.0, 0) in p._MORNING
    assert p.greeting_for(9, 60.0, 0) != p.greeting_for(9, 60.0, 1)
    assert p.greeting_for(9, 4 * 3600.0, 0) in p._LONG_ABSENCE


def test_registry_defaults_present():
    p.set_monitor(None)
    assert p.is_present() is True
