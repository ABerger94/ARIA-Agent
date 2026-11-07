"""Pass 27 #1: play_uri / DJ must launch only Spotify targets.

play_uri used to pass arbitrary model text straight to
os.startfile, and DJ did the same with stored-memory playlist
values (model-writable) — a .bat path launched a program with
zero approvals. Only spotify: URIs and open.spotify.com links
may reach startfile now.
"""
import sys
import types
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

memory = types.ModuleType('aria.memory')
import threading
memory.DB_LOCK = threading.Lock()
memory.DB_PATH = ':memory:'
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


def _launches(monkeypatch):
    calls = []
    monkeypatch.setattr(spotify_mod.os, 'startfile',
                        lambda target: calls.append(target), raising=False)
    monkeypatch.setattr(spotify_mod.time, 'sleep', lambda s: None)
    monkeypatch.setattr(spotify_mod, '_tap_media_vk', lambda vk: True)
    return calls


def test_play_uri_refuses_bat_path(monkeypatch):
    calls = _launches(monkeypatch)
    out = spotify_mod.tool_spotify('play_uri', r'C:\evil.bat')
    assert 'Refused' in out and 'nothing was launched' in out, out
    assert calls == []


def test_play_uri_refuses_file_and_steam_schemes(monkeypatch):
    calls = _launches(monkeypatch)
    for bad in ('file:///etc/passwd', 'steam://run/123',
                'https://evil.com/spotify:track:abc',
                'spotify:track:abc; rm -rf /', 'spotify:track:',
                'spotify:track:abc/def', 'open.spotify.com/track/abc'):
        out = spotify_mod.tool_spotify('play_uri', bad)
        assert 'Refused' in out, (bad, out)
    assert calls == []


def test_play_uri_accepts_spotify_uris_and_links(monkeypatch):
    calls = _launches(monkeypatch)
    for good in ('spotify:track:4uLU6hMCjMI75M1A2tKUQC',
                 'spotify:playlist:37i9dQZF1DWWQRwui0ExPn',
                 'spotify:collection:tracks',
                 'https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC',
                 'https://open.spotify.com/playlist/37i9dQZF1DWWQRwui0ExPn?si=abc'):
        out = spotify_mod.tool_spotify('play_uri', good)
        assert 'Spotify: playing' in out or 'Spotify: opened' in out, out
    assert calls == ['spotify:track:4uLU6hMCjMI75M1A2tKUQC',
                     'spotify:playlist:37i9dQZF1DWWQRwui0ExPn',
                     'spotify:collection:tracks',
                     'https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC',
                     'https://open.spotify.com/playlist/37i9dQZF1DWWQRwui0ExPn?si=abc']


def test_dj_refuses_poisoned_stored_uri(monkeypatch):
    calls = _launches(monkeypatch)
    monkeypatch.setattr(spotify_mod, '_find_playlist',
                        lambda name: (r'C:\evil.bat', 'Evil'))
    out = spotify_mod.tool_dj('play evil')
    assert 'Refused' in out and 'nothing was launched' in out, out
    assert calls == []


def test_dj_accepts_valid_stored_uri(monkeypatch):
    calls = _launches(monkeypatch)
    monkeypatch.setattr(spotify_mod, '_find_playlist',
                        lambda name: ('spotify:playlist:37i9dQZF1DWWQRwui0ExPn', 'Lofi'))
    monkeypatch.setattr(spotify_mod, '_tap_ctrl_s', lambda: True)
    out = spotify_mod.tool_dj('play lofi')
    assert 'Spotify DJ: playing Lofi.' in out, out
    assert calls == ['spotify:playlist:37i9dQZF1DWWQRwui0ExPn']


def test_open_still_launches_constant(monkeypatch):
    calls = _launches(monkeypatch)
    out = spotify_mod.tool_spotify('open')
    assert 'opening the app' in out, out
    assert calls == ['spotify:']
