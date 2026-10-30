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
