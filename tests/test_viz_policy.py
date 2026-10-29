"""VizPolicy: the visualizer's show/hide rules (Alek, 2026-10-29).

Pure state machine: pops up when music plays, steps aside during
interaction, a manual close wins for the rest of the session, and
a new session re-arms it.
"""
import os
import sys
import types
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

from aria.viz_policy import VizPolicy


def test_shows_when_playing():
    p = VizPolicy()
    assert p.update(playing=True, session=True, interacting=False)


def test_hidden_when_nothing_plays():
    p = VizPolicy()
    assert not p.update(playing=False, session=True, interacting=False)
    assert not p.update(playing=False, session=False, interacting=False)


def test_paused_does_not_show():
    p = VizPolicy()
    assert p.update(playing=True, session=True, interacting=False)
    assert not p.update(playing=False, session=True, interacting=False)


def test_interaction_hides_and_return_restores():
    p = VizPolicy()
    assert p.update(playing=True, session=True, interacting=False)
    assert not p.update(playing=True, session=True, interacting=True)
    # Interaction over, music still playing: it comes back.
    assert p.update(playing=True, session=True, interacting=False)


def test_manual_close_wins_for_the_session():
    p = VizPolicy()
    assert p.update(playing=True, session=True, interacting=False)
    p.manual_close()
    assert not p.update(playing=True, session=True, interacting=False)
    # Interaction does not resurrect a dismissed visualizer.
    assert not p.update(playing=True, session=True, interacting=True)
    assert not p.update(playing=True, session=True, interacting=False)


def test_manual_close_survives_pause_resume():
    p = VizPolicy()
    p.update(playing=True, session=True, interacting=False)
    p.manual_close()
    assert not p.update(playing=False, session=True, interacting=False)
    assert not p.update(playing=True, session=True, interacting=False)


def test_new_session_rearms_after_close():
    p = VizPolicy()
    p.update(playing=True, session=True, interacting=False)
    p.manual_close()
    # Player fully stops/closes: the session ends.
    assert not p.update(playing=False, session=False, interacting=False)
    # Next session: the visualizer pops up again.
    assert p.update(playing=True, session=True, interacting=False)


def test_close_before_any_playback_still_rearms():
    p = VizPolicy()
    p.manual_close()
    assert p.update(playing=True, session=True, interacting=False)
