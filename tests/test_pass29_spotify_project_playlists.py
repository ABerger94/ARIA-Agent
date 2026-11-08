"""Pass 29 #44: Spotify playlist lookup must read the
project-bound memory DB.

Before: _find_playlist did sqlite3.connect(DB_PATH) — the imported
global — while memory_save writes through memory.db_path()
(project-aware). A playlist saved in a named project was never
found there, and default-project playlists leaked into named
projects. The lookup now uses memory.db_path(), the same accessor
the save path uses.
"""
import sqlite3
import sys
import threading
import types
from pathlib import Path

import pytest

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

# aria.spotify imports aria.memory + aria.tools.builtins at module
# level — stub both before the import. The memory stub carries a
# db_path() accessor like the real module, switchable per test.
memory = types.ModuleType('aria.memory')
memory.DB_LOCK = threading.Lock()
memory.DB_PATH = ':memory:'
memory.db_path = lambda: memory.DB_PATH
sys.modules['aria.memory'] = memory
tools_pkg = types.ModuleType('aria.tools')
tools_pkg.__path__ = []
builtins = types.ModuleType('aria.tools.builtins')
builtins.tool_focus_window = lambda title: 'Focused'
builtins.tool_media_key = lambda action: 'ok'
tools_pkg.builtins = builtins
sys.modules['aria.tools'] = tools_pkg
sys.modules['aria.tools.builtins'] = builtins

from aria import spotify as spotify_mod


def _make_db(path, playlists):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE memory (category TEXT, key TEXT, "
                 "value TEXT)")
    for key, value in playlists:
        conn.execute("INSERT INTO memory (category, key, value) "
                     "VALUES ('playlist', ?, ?)", (key, value))
    conn.commit()
    conn.close()


@pytest.fixture
def dbs(tmp_path):
    default_db = tmp_path / "default.db"
    project_db = tmp_path / "project.db"
    _make_db(default_db, [("chill vibes", "spotify:playlist:default1")])
    _make_db(project_db, [("focus flow", "spotify:playlist:project1")])
    memory.DB_PATH = str(default_db)
    memory.db_path = lambda: str(project_db)
    return types.SimpleNamespace(default_db=default_db,
                                 project_db=project_db)


def test_playlist_saved_in_named_project_found_there(dbs):
    # db_path() points at the named project's DB: its playlist is
    # found, the default project's is not.
    uri, name = spotify_mod._find_playlist("focus flow")
    assert uri == "spotify:playlist:project1"
    assert name == "focus flow"
    assert spotify_mod._find_playlist("chill vibes") == (None, None)


def test_default_playlist_not_visible_from_named_project(dbs,
                                                        monkeypatch):
    # db_path() points at the default project's DB: its playlist is
    # found, the named project's is not.
    monkeypatch.setattr(memory, "db_path",
                        lambda: str(dbs.default_db))
    uri, name = spotify_mod._find_playlist("chill vibes")
    assert uri == "spotify:playlist:default1"
    assert spotify_mod._find_playlist("focus flow") == (None, None)


def test_missing_db_path_accessor_falls_back_to_global_db(dbs,
                                                          monkeypatch):
    # The stubbed-memory harness has no db_path(): the global
    # DB_PATH is used, as before.
    monkeypatch.delattr(memory, "db_path", raising=False)
    # spotify bound DB_PATH at import (':memory:'); point the module
    # attribute at the default DB, as the real module would have.
    monkeypatch.setattr(spotify_mod, "DB_PATH", str(dbs.default_db))
    uri, name = spotify_mod._find_playlist("chill vibes")
    assert uri == "spotify:playlist:default1"
    assert spotify_mod._find_playlist("focus flow") == (None, None)
