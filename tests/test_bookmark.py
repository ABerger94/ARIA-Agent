"""Task bookmarks (pass 6): "save this for later" snapshots the
current task from history; "where were we" builds a resume prompt.

Pure-module suite: fabricated histories in the brain's real entry
shape; storage goes to a tmp path."""
import importlib
import os
import sys
import types
from datetime import datetime

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)

bookmark = importlib.import_module("aria.bookmark")


def history():
    return [
        {"role": "user", "parts": [{"text": "Find me a cheap flight to Denver"}]},
        {"role": "model", "parts": [
            {"functionCall": {"name": "search_flights", "args": {}}},
            {"functionCall": {"name": "search_flights", "args": {}}},
        ]},
        {"role": "user", "parts": [
            {"functionResponse": {"name": "search_flights",
                                  "response": {"output": "3 options"}}},
        ]},
        {"role": "model", "parts": [{"text": "Found three options under $200."}]},
        {"role": "user", "parts": [{"text": "Check the Thursday one"}]},
        {"role": "model", "parts": [
            {"functionCall": {"name": "get_flight_details", "args": {}}},
            {"text": "Thursday flight leaves at 9 AM."},
        ]},
    ]


# ------------------------------------------------------------- phrases

def test_phrase_matching():
    assert bookmark.is_save("save this for later")
    assert bookmark.is_save("Bookmark this!")
    assert bookmark.is_save("please save this for later")
    assert bookmark.is_resume("where were we")
    assert bookmark.is_resume("Where were we?")
    assert bookmark.is_resume("pick up where we left off")


def test_sentences_are_not_phrases():
    assert not bookmark.is_save("save this file to the desktop")
    assert not bookmark.is_save("save this song to my playlist")
    assert not bookmark.is_resume("where were we in the movie")
    assert not bookmark.is_resume("where were we going again")


# ------------------------------------------------------------- building

def test_build_bookmark_contents():
    bm = bookmark.build_bookmark(history(),
                                 now=datetime(2026, 10, 29, 15, 30))
    assert bm["request"] == "Check the Thursday one"
    # Function calls deduped, in order; responses are not actions.
    assert bm["actions"] == ["search_flights", "get_flight_details"]
    assert bm["saved_at"].startswith("2026-10-29T15:30")
    joined = "\n".join(bm["excerpt"])
    assert "User: Find me a cheap flight to Denver" in joined
    assert "ARIA: Found three options under $200." in joined
    # Function-response entries never render as excerpt lines.
    assert "3 options" not in joined


def test_build_bookmark_bounds():
    long_text = "word " * 300
    hist = [{"role": "user", "parts": [{"text": long_text}]}]
    hist += [{"role": "model", "parts": [{"text": f"reply {i}"}]}
             for i in range(20)]
    bm = bookmark.build_bookmark(hist)
    assert len(bm["request"]) <= bookmark.REQUEST_MAX + 1
    assert len(bm["excerpt"]) == bookmark.EXCERPT_MAX
    assert all(len(line) <= bookmark.LINE_MAX + len("ARIA: ") + 1
               for line in bm["excerpt"])


def test_build_bookmark_empty_history():
    bm = bookmark.build_bookmark([])
    assert bm["request"] == "" and bm["excerpt"] == [] and bm["actions"] == []


# ------------------------------------------------------------- storage

def test_save_load_roundtrip(tmp_path):
    path = str(tmp_path / "bookmark.json")
    bm = bookmark.build_bookmark(history(),
                                 now=datetime(2026, 10, 29, 15, 30))
    bookmark.save(bm, path)
    loaded = bookmark.load(path)
    assert loaded["request"] == bm["request"]
    assert loaded["actions"] == bm["actions"]


def test_load_missing_or_corrupt(tmp_path):
    assert bookmark.load(str(tmp_path / "nope.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert bookmark.load(str(bad)) is None


def test_save_replaces_previous(tmp_path):
    path = str(tmp_path / "bookmark.json")
    bookmark.save(bookmark.build_bookmark(history()), path)
    other = [{"role": "user", "parts": [{"text": "A different task"}]}]
    bookmark.save(bookmark.build_bookmark(other), path)
    assert bookmark.load(path)["request"] == "A different task"


# ------------------------------------------------------------- lanes

def test_try_save_ack_and_persist(tmp_path):
    path = str(tmp_path / "bookmark.json")
    ack = bookmark.try_save("save this for later", history(), path)
    assert ack is not None and ack.startswith("Saved for later:")
    assert "Check the Thursday one" in ack
    assert bookmark.load(path) is not None
    # A non-phrase does nothing.
    assert bookmark.try_save("book a table", history(), path) is None


def test_try_save_empty_history():
    ack = bookmark.try_save("bookmark this", [], path=None)
    assert ack is not None and "nothing to save" in ack.lower()


def test_resume_prompt_carries_context():
    bm = bookmark.build_bookmark(history(),
                                 now=datetime(2026, 10, 29, 15, 30))
    prompt = bookmark.resume_prompt(bm)
    assert "Check the Thursday one" in prompt
    assert "search_flights" in prompt and "get_flight_details" in prompt
    assert "3:30 PM" in prompt
    assert "Do not re-run" in prompt
    assert "Found three options under $200." in prompt
