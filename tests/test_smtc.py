"""SMTC backend (aria/smtc.py): the real now-playing source and
explicit per-session transport. Fakes stand in for the WinRT
objects — winsdk is Windows-only, so the parsing, session
choice, and call routing are what get pinned here; the WinRT
round-trip itself is Alek's witness on the laptop."""
import sys
import types
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import smtc


class _Op:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class _Props:
    def __init__(self, title, artist, album):
        self.title = title
        self.artist = artist
        self.album_title = album


class _Info:
    def __init__(self, status):
        self.playback_status = status


class _Session:
    def __init__(self, app_id, props=None, status=4):
        self.source_app_user_model_id = app_id
        self._props = props or _Props('The Song', 'The Artist', 'The Album')
        self._status = status
        self.calls = []

    def try_get_media_properties_async(self):
        return _Op(self._props)

    def get_playback_info(self):
        return _Info(self._status)

    def _hit(self, name):
        self.calls.append(name)
        return _Op(True)

    def try_play_async(self):
        return self._hit('play')

    def try_pause_async(self):
        return self._hit('pause')

    def try_toggle_play_pause_async(self):
        return self._hit('toggle')

    def try_skip_next_async(self):
        return self._hit('next')

    def try_skip_previous_async(self):
        return self._hit('previous')

    def try_stop_async(self):
        return self._hit('stop')


class _Manager:
    def __init__(self, sessions, current=None):
        self._sessions = sessions
        self._current = current

    def get_sessions(self):
        return list(self._sessions)

    def get_current_session(self):
        return self._current


def _install(monkeypatch, manager):
    monkeypatch.setattr(smtc, '_MANAGER', None)
    monkeypatch.setattr(smtc, '_MANAGER_FAILED', False)
    monkeypatch.setattr(smtc, '_load_manager', lambda: manager)
    monkeypatch.setattr(smtc, '_CACHE', {'time': 0.0, 'data': None})
    monkeypatch.setattr(smtc, '_REFRESHING', False)


def test_reads_the_spotify_session_not_the_current_one(monkeypatch):
    browser = _Session('Chrome', _Props('A Video', 'Some Channel', ''))
    spotify = _Session('SpotifyAB.SpotifyMusic_beta', status=5)
    _install(monkeypatch, _Manager([browser, spotify]))
    data = smtc._read_now_playing()
    assert data['source'] == 'smtc'
    assert data['track'] == 'The Song' and data['artist'] == 'The Artist'
    assert data['title'] == 'The Artist - The Song'
    assert data['album'] == 'The Album'
    assert data['status'] == 'paused' and data['playing'] is False
    assert data['running'] is True


def test_playing_status_maps_to_playing(monkeypatch):
    _install(monkeypatch, _Manager([_Session('Spotify', status=4)]))
    data = smtc._read_now_playing()
    assert data['status'] == 'playing' and data['playing'] is True


def test_no_spotify_session_is_none(monkeypatch):
    _install(monkeypatch, _Manager([_Session('Chrome')]))
    assert smtc._read_now_playing() is None
    assert smtc.control('play') is False


def test_missing_backend_degrades(monkeypatch):
    def boom():
        raise ImportError('no winsdk here')
    _install(monkeypatch, None)
    monkeypatch.setattr(smtc, '_load_manager', boom)
    assert smtc.available() is False
    assert smtc._read_now_playing() is None
    assert smtc.control('pause') is False


def test_control_routes_explicit_calls_to_the_session(monkeypatch):
    spotify = _Session('Spotify')
    _install(monkeypatch, _Manager([spotify]))
    assert smtc.control('pause') is True
    assert smtc.control('play') is True
    assert smtc.control('next') is True
    assert smtc.control('previous') is True
    assert smtc.control('play_pause') is True
    assert spotify.calls == ['pause', 'play', 'next', 'previous', 'toggle']
    assert smtc.control('bogus') is False


def test_now_playing_cache_refreshes_in_background(monkeypatch):
    import time as _time
    _install(monkeypatch, _Manager([_Session('Spotify', status=4)]))
    deadline = _time.time() + 5
    data = None
    while _time.time() < deadline:
        data = smtc.now_playing()
        if data:
            break
        _time.sleep(0.02)
    assert data is not None and data['track'] == 'The Song'


def test_active_session_is_whatever_is_playing(monkeypatch):
    # Alek, 2026-10-27: NOW PLAYING covers movies and browsers,
    # not just Spotify — the active read follows the session
    # Windows calls current, and labels the app.
    edge = _Session('MSEdge', _Props('Big Buck Bunny (2008)', '', ''), status=4)
    spotify = _Session('Spotify', status=5)
    _install(monkeypatch, _Manager([spotify, edge], current=edge))
    data = smtc._read_all()['active']
    assert data['app'] == 'Edge' and data['is_spotify'] is False
    assert data['track'] == 'Big Buck Bunny (2008)'
    assert data['playing'] is True
    # The Spotify-specific read still targets Spotify.
    assert smtc._read_all()['spotify']['status'] == 'paused'
    # And transport aims at the active session, not Spotify.
    assert smtc.control_active('pause') is True
    assert edge.calls == ['pause'] and spotify.calls == []


def test_set_playing_closed_loop_escalates_when_session_shrugs(monkeypatch):
    # Alek's witness: prev/next worked, play/pause did nothing —
    # a session can accept the SMTC call without acting. The
    # closed loop must notice the state didn't flip and escalate
    # to the media-key toggle.
    session = _Session('Spotify', status=4)   # playing
    session.obey = False                      # ignores try_pause
    orig_pause = session.try_pause_async
    def stubborn_pause():
        session.calls.append('pause')
        return _Op(True)                      # claims success, does nothing
    session.try_pause_async = stubborn_pause
    _install(monkeypatch, _Manager([session]))
    monkeypatch.setattr(smtc, '_SETTLE_S', 0)
    taps = []
    tools_pkg = types.ModuleType('aria.tools'); tools_pkg.__path__ = []
    builtins = types.ModuleType('aria.tools.builtins')
    def fake_key(action):
        taps.append(action)
        session._status = 5                   # the toggle works
        return 'ok'
    builtins.tool_media_key = fake_key
    tools_pkg.builtins = builtins
    monkeypatch.setitem(sys.modules, 'aria.tools', tools_pkg)
    monkeypatch.setitem(sys.modules, 'aria.tools.builtins', builtins)
    final = smtc.set_playing(False, spotify_only=True)
    assert final is False                     # paused, as wanted
    assert session.calls == ['pause']
    assert taps == ['play_pause']             # escalated exactly once


def test_set_playing_cooperative_and_already(monkeypatch):
    session = _Session('Spotify', status=4)
    orig_pause = session.try_pause_async
    def good_pause():
        session.calls.append('pause')
        session._status = 5
        return _Op(True)
    session.try_pause_async = good_pause
    _install(monkeypatch, _Manager([session]))
    monkeypatch.setattr(smtc, '_SETTLE_S', 0)
    assert smtc.set_playing(False, spotify_only=True) is False
    assert session.calls == ['pause']
    # Already paused: no further calls at all.
    assert smtc.set_playing(False, spotify_only=True) is False
    assert session.calls == ['pause']
    # No session anywhere: None, and callers decide the message.
    _install(monkeypatch, _Manager([]))
    assert smtc.set_playing(True) is None


def test_app_labels():
    assert smtc.app_label('SpotifyAB.SpotifyMusic_1.0') == 'Spotify'
    assert smtc.app_label('Microsoft.ZuneVideo_8wekyb3d8bbwe') == 'Movies & TV'
    assert smtc.app_label('org.videolan.vlc') == 'VLC'
    assert smtc.app_label('Some.Random.Player_x') == 'Player'
