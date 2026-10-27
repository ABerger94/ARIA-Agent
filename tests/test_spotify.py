"""tool_spotify's explicit play/pause (Alek, 2026-10-27):
closed-loop through SMTC when the backend exists, honest
fallback when it doesn't."""
import sys
import types
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

# aria.spotify imports aria.memory + aria.tools.builtins at
# module level — stub both before the import.
memory = types.ModuleType('aria.memory')
import threading
memory.DB_LOCK = threading.Lock()
memory.DB_PATH = ':memory:'
sys.modules['aria.memory'] = memory
tools_pkg = types.ModuleType('aria.tools'); tools_pkg.__path__ = []
builtins = types.ModuleType('aria.tools.builtins')
_focus_calls = []
builtins.tool_focus_window = lambda title: _focus_calls.append(title) or 'Focused'
builtins.tool_media_key = lambda action: 'ok'
tools_pkg.builtins = builtins
sys.modules['aria.tools'] = tools_pkg
sys.modules['aria.tools.builtins'] = builtins

from aria import spotify as spotify_mod


def _smtc_stub(monkeypatch, state, final):
    smtc = types.ModuleType('aria.smtc')
    calls = []
    smtc.refresh_now = lambda: {'spotify': state, 'active': state}
    def fake_set_playing(want, *, spotify_only=False):
        calls.append(want)
        return final
    smtc.set_playing = fake_set_playing
    smtc.control = lambda action: True
    monkeypatch.setitem(sys.modules, 'aria.smtc', smtc)
    monkeypatch.setattr(pkg, 'smtc', smtc, raising=False)
    return calls


def test_pause_via_closed_loop(monkeypatch):
    state = {'playing': True, 'running': True, 'status': 'playing'}
    calls = _smtc_stub(monkeypatch, state, False)
    taps = []
    monkeypatch.setattr(spotify_mod, '_tap_media_vk', lambda vk: taps.append(vk) or True)
    assert spotify_mod.tool_spotify('pause') == 'Spotify: pause.'
    assert calls == [False] and taps == []      # no blind toggle needed


def test_pause_that_never_takes_is_an_honest_failure(monkeypatch):
    state = {'playing': True, 'running': True, 'status': 'playing'}
    _smtc_stub(monkeypatch, state, True)        # still playing at the end
    monkeypatch.setattr(spotify_mod, '_tap_media_vk', lambda vk: True)
    out = spotify_mod.tool_spotify('pause')
    assert out.startswith('[Spotify did not pause'), out


def test_already_paused_is_a_noop(monkeypatch):
    state = {'playing': False, 'running': True, 'status': 'paused'}
    calls = _smtc_stub(monkeypatch, state, False)
    assert spotify_mod.tool_spotify('pause') == 'Spotify: already paused.'
    assert calls == []                          # set_playing never invoked


def test_no_backend_falls_back_to_focus_and_toggle(monkeypatch):
    smtc = types.ModuleType('aria.smtc')
    def boom():
        raise ImportError('no winsdk')
    smtc.refresh_now = boom
    monkeypatch.setitem(sys.modules, 'aria.smtc', smtc)
    monkeypatch.setattr(pkg, 'smtc', smtc, raising=False)
    hud = types.ModuleType('aria.hud')
    hud._get_spotify_telemetry = lambda: {'running': True, 'playing': True,
                                          'source': 'title'}
    monkeypatch.setitem(sys.modules, 'aria.hud', hud)
    monkeypatch.setattr(pkg, 'hud', hud, raising=False)
    taps = []
    monkeypatch.setattr(spotify_mod, '_tap_media_vk', lambda vk: taps.append(vk) or True)
    _focus_calls.clear()
    out = spotify_mod.tool_spotify('play')
    assert out == 'Spotify: sent play command.'
    assert taps == [0xB3] and _focus_calls == ['Spotify']
