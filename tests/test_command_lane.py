"""Local command lane (pass 6): exact-phrase media commands take a
direct path in handle_action; everything else reaches the brain.

Pure-module suite: the lane imports nothing at module level, and
the media seams are injected fakes, so no package __init__, no Qt,
no audio stack is touched."""
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)

import importlib
lane = importlib.import_module("aria.command_lane")


class Recorder:
    def __init__(self, result="ok"):
        self.calls = []
        self.result = result

    def __call__(self, action, *a, **k):
        self.calls.append(action)
        return self.result


# ------------------------------------------------------------- matching

def test_all_phrases_match():
    assert lane.match("pause music") == ("media", "pause")
    assert lane.match("resume music") == ("media", "play")
    assert lane.match("play music") == ("media", "play")
    assert lane.match("next track") == ("media", "next")
    assert lane.match("previous track") == ("media", "prev")
    assert lane.match("volume up") == ("volume", "up")
    assert lane.match("volume down") == ("volume", "down")


def test_normalization_case_whitespace_punctuation():
    assert lane.match("Pause Music!") == ("media", "pause")
    assert lane.match("  NEXT    TRACK  ") == ("media", "next")
    assert lane.match("volume up.") == ("volume", "up")
    assert lane.match("Volume Down?") == ("volume", "down")


def test_leading_please_is_normalized():
    assert lane.match("please pause music") == ("media", "pause")
    assert lane.match("Please, next track") is None  # comma form is not exact
    assert lane.match("please volume down") == ("volume", "down")


def test_sentences_never_match():
    for text in (
        "pause",
        "next",
        "pause the music",
        "what does pause music mean",
        "turn the volume up",
        "volume up please",
        "pause music and check my email",
        "the next track is my favourite",
        "",
        "   ",
    ):
        assert lane.match(text) is None, text


# ------------------------------------------------------------- execution

def test_media_phrases_route_to_control():
    ctl, vol = Recorder("media: done"), Recorder("vol: done")
    assert lane.try_handle("pause music", control_fn=ctl, volume_fn=vol) == "media: done"
    assert ctl.calls == ["pause"] and vol.calls == []
    assert lane.try_handle("next track", control_fn=ctl, volume_fn=vol) == "media: done"
    assert ctl.calls == ["pause", "next"]


def test_volume_phrases_route_to_volume_tool():
    ctl, vol = Recorder("media: done"), Recorder("Volume is at 60%.")
    assert lane.try_handle("volume up", control_fn=ctl, volume_fn=vol) == "Volume is at 60%."
    assert vol.calls == ["up"] and ctl.calls == []
    assert lane.try_handle("volume down", control_fn=ctl, volume_fn=vol) == "Volume is at 60%."
    assert vol.calls == ["up", "down"]


def test_non_command_calls_nothing():
    ctl, vol = Recorder(), Recorder()
    assert lane.try_handle("book me a flight", control_fn=ctl, volume_fn=vol) is None
    assert ctl.calls == [] and vol.calls == []


def test_control_result_surfaces_verbatim():
    # An honest refusal from the media layer is the acknowledgement,
    # not something the lane hides or rewrites.
    refusal = "[Media control: no built-in player is available.]"
    ctl = Recorder(refusal)
    assert lane.try_handle("pause music", control_fn=ctl) == refusal
