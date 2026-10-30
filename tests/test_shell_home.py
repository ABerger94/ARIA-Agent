"""ARIA OS Shell Home model + command routing (2026-10-30).

Pure-module suite: routing must be conservative — an exact app
name (or an explicit open-verb plus an unambiguous name) launches;
everything else is chat for ARIA's normal, approval-gated path."""
import os
import sys
import types
from datetime import datetime

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)

import importlib
shell_home = importlib.import_module("aria.shell_home")

APPS = [{"name": "Google Chrome", "path": "/x/chrome.lnk"},
        {"name": "Chromium", "path": "/x/chromium.lnk"},
        {"name": "Steam", "path": "/x/steam.lnk"},
        {"name": "Calculator", "path": "/x/calc.lnk"}]


def test_greetings_follow_the_clock():
    assert shell_home.greeting_for(9) == "Good morning, Alek"
    assert shell_home.greeting_for(13) == "Good afternoon, Alek"
    assert shell_home.greeting_for(20) == "Good evening, Alek"
    assert shell_home.greeting_for(2) == "Still up, Alek"


def test_home_model_shape():
    now = datetime(2026, 10, 30, 15, 47)
    model = shell_home.home_model(APPS, now=now)
    assert model["clock"] == "15:47"
    assert model["greeting"] == "Good afternoon, Alek"
    assert model["date_line"] == "Friday, October 30"
    assert model["app_count"] == 4
    assert model["count_line"] == "4 apps"
    assert model["exit_label"] == "Exit to Windows"
    assert shell_home.home_model(APPS[:1], now=now)["count_line"] \
        == "1 app"


def test_route_exact_name_launches():
    intent = shell_home.route_command("Steam", APPS)
    assert intent["kind"] == "launch"
    assert intent["app"]["name"] == "Steam"
    intent = shell_home.route_command("google chrome", APPS)
    assert intent["kind"] == "launch"
    assert intent["app"]["name"] == "Google Chrome"


def test_route_verb_plus_name_launches():
    intent = shell_home.route_command("open Steam", APPS)
    assert intent["kind"] == "launch"
    intent = shell_home.route_command("Launch calculator", APPS)
    assert intent["kind"] == "launch"
    assert intent["app"]["name"] == "Calculator"


def test_route_verb_plus_unique_prefix_launches():
    intent = shell_home.route_command("open steam", APPS)
    assert intent["kind"] == "launch"
    intent = shell_home.route_command("open chromi", APPS)
    assert intent["kind"] == "launch"
    assert intent["app"]["name"] == "Chromium"


def test_route_ambiguous_prefix_is_chat():
    apps = [{"name": "Chrome", "path": "/x/c.lnk"},
            {"name": "Chrome Beta", "path": "/x/cb.lnk"}]
    # "chrom" prefixes both: no guessing.
    intent = shell_home.route_command("open chrom", apps)
    assert intent["kind"] == "chat"
    # And a verb-less bare prefix is chat even when unique.
    intent = shell_home.route_command("chromi", APPS)
    assert intent["kind"] == "chat"


def test_route_sentences_are_chat():
    for text in ("what's the weather like",
                 "remind me to buy milk at 6",
                 "open the pod bay doors",
                 "steam is down again",
                 "tell me about steam engines"):
        intent = shell_home.route_command(text, APPS)
        assert intent["kind"] == "chat", text
        assert intent["text"] == text


def test_route_empty_is_empty():
    assert shell_home.route_command("   ", APPS)["kind"] == "empty"
    assert shell_home.route_command("", APPS)["kind"] == "empty"


# -- V2: tile + rail-card presentation models ---------------------

def test_tile_model_letter_and_stable_color():
    tile = shell_home.tile_model({"name": "Steam", "path": "/x/s.lnk"})
    assert tile["name"] == "Steam"
    assert tile["letter"] == "S"
    assert tile["color"] in shell_home.TILE_COLORS
    # Same name -> same letter + colour, every run (stable paint).
    a = shell_home.tile_model({"name": "Steam"})
    b = shell_home.tile_model({"name": "Steam", "path": "/other"})
    assert (a["letter"], a["color"]) == (b["letter"], b["color"])
    assert shell_home.tile_model({})["letter"] == "?"
    assert shell_home.tile_model({"name": "chrome"})["letter"] == "C"


def test_briefing_card_empty_without_report():
    card = shell_home.briefing_card(None)
    assert card["title"] == "Briefing"
    assert card["empty"] is True
    assert "No briefing yet" in card["lines"][0]
    assert shell_home.briefing_card({})["empty"] is True


def test_briefing_card_uses_report_sections_schedule_first():
    report = {"generated_at": "2026-10-30T08:00:00",
              "text": "full text",
              "sections": [
                  {"label": "Local weather", "text": "Weather: cold.",
                   "status": "ok"},
                  {"label": "Today's schedule",
                   "text": "Schedule: one thing.", "status": "ok"},
                  {"label": "Tech news", "text": "", "status": "empty"},
              ]}
    card = shell_home.briefing_card(report)
    assert card["empty"] is False
    assert card["lines"][0] == "Schedule: one thing."
    assert card["lines"][1] == "Weather: cold."
    # Blank sections never reach the card.
    assert len(card["lines"]) == 2


def test_now_playing_card_states():
    card = shell_home.now_playing_card(None)
    assert card["empty"] is True
    assert card["lines"] == ["Nothing playing"]
    info = {"title": "Artist - Track", "track": "Track",
            "artist": "Artist", "app": "Spotify", "playing": True}
    card = shell_home.now_playing_card(info)
    assert card["empty"] is False
    assert card["lines"][0] == "Artist - Track"
    assert card["lines"][1] == "Playing · Spotify"
    info["playing"] = False
    assert shell_home.now_playing_card(info)["lines"][1] == \
        "Paused · Spotify"
    # A session with no title at all is still "nothing playing".
    assert shell_home.now_playing_card({"app": "VLC"})["empty"] is True


def test_status_card_only_states_what_it_was_given():
    card = shell_home.status_card()
    assert card["empty"] is True
    card = shell_home.status_card(safe_mode=False,
                                  approval_mode="confirm-risky",
                                  voice_enabled=True, shell_mode=True)
    assert card["empty"] is False
    assert card["lines"] == ["Approvals: confirm-risky",
                             "Safe mode: off", "Voice: on",
                             "Shell Mode boot: on"]
    # Unknown values are omitted, never guessed.
    card = shell_home.status_card(safe_mode=True)
    assert card["lines"] == ["Safe mode: on"]
