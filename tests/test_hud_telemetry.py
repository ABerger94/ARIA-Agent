"""Spotify window-title parsing (hud._parse_spotify_title).

The regression pinned here (Alek, 2026-10-27): the old fallback
searched window TITLES for the word 'spotify' — but a playing
Spotify's title is the track name, so the scrape only ever saw
the idle window: the panel said standby / 'nothing playing'
while music played, and every telemetry-gated play/pause path
refused as 'not running'. The window is now found by process;
this pins the title interpretation."""
import sys
import types
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

# hud pulls aria.speech at module level; the sandbox venv lacks
# speech_recognition — stub speech the way test_frame.py does.
speech = types.ModuleType('aria.speech')
speech.VOICE_ENABLED = True
sys.modules['aria.speech'] = speech
pkg.speech = speech

from aria.hud import _parse_spotify_title, media_window_telemetry


def test_no_window_means_not_running():
    assert _parse_spotify_title(None) is None
    assert _parse_spotify_title('') is None
    assert _parse_spotify_title('   ') is None


def test_track_title_is_the_playing_case():
    out = _parse_spotify_title('The Artist - The Song')
    assert out['running'] is True and out['playing'] is True
    assert out['artist'] == 'The Artist' and out['track'] == 'The Song'
    assert out['status'] == 'playing'


def test_bare_app_title_is_open_not_playing():
    for title in ('Spotify', 'Spotify Free', 'Spotify Premium'):
        out = _parse_spotify_title(title)
        assert out['running'] is True and out['playing'] is False
        assert out['status'] == 'open' and out['track'] == ''


def test_unstructured_title_is_a_loaded_something():
    out = _parse_spotify_title('Some Podcast Episode')
    assert out['running'] is True and out['title'] == 'Some Podcast Episode'


def test_media_player_movie_shows_from_its_window():
    # Alek, 2026-10-27: SMTC dark, Media Player playing a movie —
    # the panel said nothing. The window title is the fallback.
    out = media_window_telemetry(
        windows=[('mediaplayer.exe', 'Dune Part Two.mp4'),
                 ('explorer.exe', 'Dune Part Two.mp4 - File Explorer')])
    assert out['app'] == 'Media Player'
    assert out['track'] == 'Dune Part Two.mp4'
    assert out['playing'] is True and out['source'] == 'title'


def test_vlc_suffix_is_stripped_and_idle_player_is_open():
    out = media_window_telemetry(
        windows=[('vlc.exe', 'clip.mkv - VLC media player')])
    assert out['app'] == 'VLC' and out['track'] == 'clip.mkv'
    idle = media_window_telemetry(windows=[('vlc.exe', 'VLC media player')])
    assert idle['status'] == 'open' and idle['track'] == ''


def test_content_beats_idle_and_unknown_processes_are_ignored():
    out = media_window_telemetry(
        windows=[('vlc.exe', 'VLC media player'),
                 ('mediaplayer.exe', 'Movie.mkv'),
                 ('notepad.exe', 'Movie.mkv - Notepad')])
    assert out['app'] == 'Media Player' and out['track'] == 'Movie.mkv'
    assert media_window_telemetry(windows=[('notepad.exe', 'x')]) is None
