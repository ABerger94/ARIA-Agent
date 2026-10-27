"""Gap fill 6 — unified media control routing (Qt-free core).

Pins the router in aria/media_control.py:
  * loaded HUD player owns transport + seek under target=auto;
  * seek with nothing loaded is an honest refusal (nothing is
    emitted, no system key is pressed);
  * transport with nothing loaded falls back to system keys;
  * next/prev are always system keys (the HUD has no playlist);
  * volume routes to the HUD player while loaded, to master
    volume otherwise; value parsing (seconds, M:SS, signed,
    percent) is exact;
  * status reports the snapshot; unknown actions are refused.
The Qt side (VideoPanel.command -> apply_command) is pinned in
tests/test_video_panel.py.
"""
import importlib
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg

mc = importlib.import_module("aria.media_control")


class FakePanel:
    def __init__(self, loaded=True):
        self._snap = {
            "loaded": loaded, "source": "clip.mp4" if loaded else "",
            "title": "Movie Night" if loaded else "",
            "playing": True, "position_ms": 60_000,
            "duration_ms": 600_000, "muted": False,
        }
        self.commands = []

    def snapshot(self):
        return dict(self._snap)

    def apply_command(self, action, payload=None):
        self.commands.append((action, payload))
        return self.snapshot()


class FakeSignal:
    def __init__(self, panel):
        self.panel = panel
        self.emitted = []

    def emit(self, action, payload):
        self.emitted.append((action, payload))
        self.panel.apply_command(action, payload)


def _sys():
    calls = {"key": [], "vol": []}

    def key(a):
        calls["key"].append(a)
        return f"key:{a}"

    def vol(a, level=50):
        calls["vol"].append((a, level))
        return f"vol:{a}:{level}"

    return calls, key, vol


def test_hud_owns_transport_and_seek_when_loaded():
    p, (calls, key, vol) = FakePanel(), _sys()
    out = mc.control("seek_percent", value="50", panel=p,
                     system_key=key, system_volume=vol)
    assert p.commands == [("seek_percent", 50.0)], p.commands
    assert "50%" in out and calls["key"] == []
    out = mc.control("pause", panel=p, system_key=key, system_volume=vol)
    assert p.commands[-1] == ("pause", None) and calls["key"] == []
    out = mc.control("seek_by", value="-15", panel=p,
                     system_key=key, system_volume=vol)
    assert p.commands[-1] == ("seek_by", -15000)
    out = mc.control("seek_to", value="1:30", panel=p,
                     system_key=key, system_volume=vol)
    assert p.commands[-1] == ("seek_ms", 90000)
    print("ok hud_owns_transport_and_seek_when_loaded")


def test_seek_refused_honestly_when_nothing_loaded():
    p, (calls, key, vol) = FakePanel(loaded=False), _sys()
    out = mc.control("seek_percent", value="50", panel=p,
                     system_key=key, system_volume=vol)
    assert "nothing is loaded" in out
    assert p.commands == [] and calls["key"] == []
    print("ok seek_refused_honestly_when_nothing_loaded")


def test_fallbacks_to_system_when_unloaded():
    p, (calls, key, vol) = FakePanel(loaded=False), _sys()
    assert mc.control("play", panel=p, system_key=key,
                      system_volume=vol) == "key:play_pause"
    assert mc.control("volume", value="40", panel=p, system_key=key,
                      system_volume=vol) == "vol:set:40"
    assert mc.control("mute", panel=p, system_key=key,
                      system_volume=vol) == "vol:mute:50"
    print("ok fallbacks_to_system_when_unloaded")


def test_next_prev_always_system_and_volume_prefers_hud():
    p, (calls, key, vol) = FakePanel(), _sys()
    assert mc.control("next", panel=p, system_key=key,
                      system_volume=vol) == "key:next"
    out = mc.control("volume", value="25", panel=p, system_key=key,
                     system_volume=vol)
    assert p.commands == [("set_volume", 25)] and calls["vol"] == []
    print("ok next_prev_always_system_and_volume_prefers_hud")


def test_status_signal_path_and_unknown_action():
    p = FakePanel()
    p.command = FakeSignal(p)
    out = mc.control("status", panel=p, system_key=lambda a: "",
                     system_volume=lambda a, level=50: "")
    assert "Movie Night" in out and "1:00 / 10:00" in out, out
    out = mc.control("pause", panel=p, system_key=lambda a: "",
                     system_volume=lambda a, level=50: "")
    assert p.command.emitted == [("pause", None)]  # signal path used
    out = mc.control("explode", panel=p)
    assert "unknown action" in out
    out = mc.control("status", target="hud", panel=None)
    assert "nothing loaded" in out or "No built-in" in out
    print("ok status_signal_path_and_unknown_action")


if __name__ == "__main__":
    test_hud_owns_transport_and_seek_when_loaded()
    test_seek_refused_honestly_when_nothing_loaded()
    test_fallbacks_to_system_when_unloaded()
    test_next_prev_always_system_and_volume_prefers_hud()
    test_status_signal_path_and_unknown_action()
    print("ALL MEDIA CONTROL CHECKS PASSED")
