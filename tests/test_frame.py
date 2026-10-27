"""Phase 4 frame tests: aria.frame's data mapping + input routing.

The render itself needs cv2/numpy/PIL (not installed on the CI runner),
so — same convention as the other suites — the heavy modules are stubbed
and the tests exercise the pure surface: strip_data() mapping from the
existing sources, the activity/chat sources, the rehoused VOICE gate,
drop-zone receipt, typed-submit routing, and click dispatch. The actual
pixels are verified by headless renders (aria-audit/phase4-renders).
"""
import importlib
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_pkg = types.ModuleType("aria")
_pkg.__path__ = [os.path.join(HERE, "..", "aria")]
sys.modules["aria"] = _pkg
_agent = types.ModuleType("aria.agent")
_agent.__path__ = [os.path.join(HERE, "..", "aria", "agent")]
sys.modules["aria.agent"] = _agent

# Heavy render deps — frame uses them only inside draw calls.
_mod("cv2")
_mod("numpy", ndarray=object, uint8=int, zeros=lambda *a, **k: None)
_pil = _mod("PIL")
_pil.Image = _mod("PIL.Image")
_pil.ImageDraw = _mod("PIL.ImageDraw")
_pil.ImageFont = _mod("PIL.ImageFont")
_mod("aria.pixel_avatar", theme_colors=lambda: ((247, 129, 47), (180, 80, 255)))

import types as _t
_mod("psutil",
     net_io_counters=lambda: _t.SimpleNamespace(bytes_sent=2048, bytes_recv=4096),
     boot_time=lambda: time.time() - 3600)

# --- hud stub: the surface frame consumes ---------------------------------
_hud_logs = []
_hud = _mod(
    "aria.hud",
    get_cached_telemetry=lambda: (33.0, 55.0, "87%",
                                  [("Voice Pathway", "ARMED", True),
                                   ("Vision", "ONLINE", True)]),
    get_hud_volume=lambda: 0.4,
    DISPLAY_CHAT_LOG=[("10:00", "A.R.I.A.", "Online and listening."),
                      ("10:01", "ALEK", "Show me the frame.")],
    LOG_STREAM=[],
    CURRENT_STATE="idle", SUBTITLE_TEXT="",
    TYPING_ACTIVE=False, TYPING_BUFFER="",
    WHISPER_MODE=False, SHOW_COMMANDS=False,
    MIC_LEVEL=0.0, MIC_LEVEL_AT=0.0,
    hud_ascii=lambda s: s,
    add_hud_log=lambda m: _hud_logs.append(m),
    _draw_hud_avatar=lambda canvas, a, b, mood: None,
    _get_spotify_telemetry=lambda: {"running": False, "playing": False,
                                    "title": "", "artist": "", "track": ""},
    _dispatch_spotify_action=lambda a: _spot_actions.append(a),
    get_clipboard_text=lambda: "",
    _MOOD_CALLBACK=None,
)
_hud._VOL_MGR = _t.SimpleNamespace(target="master")
_spot_actions = []
_vol_sets = []
_hud.set_hud_volume = lambda v: _vol_sets.append(v)

_speech = _mod("aria.speech", VOICE_ENABLED=True, voice_ready=lambda: True)
_mod("aria.scheduler", sched_list=lambda: [{"name": "a"}, {"name": "b"}])


class _P:
    def __init__(self, name, avail):
        self.name, self.model, self.api_key = name, "m", "k"
        self._avail = avail

    def is_available(self):
        return self._avail


_mod("aria.agent.providers",
     _build_chain=lambda: [_P("Ollama", True), _P("Groq", False)],
     get_active_provider=lambda: "Ollama")
_mod("aria.agent.workers",
     list_background_jobs=lambda limit=25: [{"status": "running"},
                                           {"status": "done"}])


class _Q:
    def pending_count(self):
        return 2

    def get_all_statuses(self):
        return [{"status": "running"}, {"status": "done"}]


_mod("aria.agent.taskqueue", get_queue=lambda: _Q())

_config = importlib.import_module("aria.config")
frame = importlib.import_module("aria.frame")


# ------------------------------------------------------------------ tests
def test_strip_data_mapping():
    d = frame.strip_data()
    assert d["cpu"] == 33.0 and d["mem"] == 55.0 and d["battery"] == "87%"
    assert d["active_provider"] == "Ollama"
    assert [r["state"] for r in d["providers"]] == ["live", "standby"]
    assert d["jobs_running"] == 1 and d["jobs_total"] == 2
    assert d["scheduled"] == 2
    assert d["task_pending"] == 2 and d["task_active"] == 1
    assert d["voice_ready"] is True and d["voice_enabled"] is True
    assert d["subsystems"][0][0] == "Voice Pathway"
    assert 3500 < d["uptime_s"] < 3700


def test_voice_pathway_badges():
    assert frame.voice_pathway({"voice_enabled": True, "voice_ready": True})[0] == "VOICE: EDGE"
    assert frame.voice_pathway({"voice_enabled": True, "voice_ready": False})[0] == "VOICE: SAPI"
    assert frame.voice_pathway({"voice_enabled": False, "voice_ready": True})[0] == "VOICE: MUTED"


def test_mic_state_from_subsystems():
    label, _ = frame.mic_state({"subsystems": [("Voice Pathway", "ARMED", True)]})
    assert label == "MIC: ARMED"
    label, _ = frame.mic_state({"subsystems": []})
    assert label == "MIC: ?"


def test_activity_source_is_event_ring():
    _config.add_log("frame probe event 12345")
    msgs = [m for (_ts, _lvl, m, _meta) in frame.activity_events(50)]
    assert "frame probe event 12345" in msgs


def test_chat_entries_from_hud_log():
    entries = frame.chat_entries()
    assert entries[-1][2] == "Show me the frame."
    assert entries[-2][1] == "A.R.I.A."


def test_chat_accent_bgr_follows_the_set_accent():
    # Alek, 2026-10-27: ARIA's chat text takes the system colour
    # (whatever accent is set); the frame card paints in BGR, so
    # '#bd93f9' must arrive as (0xf9, 0x93, 0xbd). With nothing
    # set it falls back to the palette ACCENT.
    import aria.ui_prefs as _up
    orig = frame._config.get_setting
    orig_up = _up.get_accent
    try:
        _up.get_accent = lambda: None
        frame._config.get_setting = lambda k, d=None: '#bd93f9'
        assert frame._chat_accent_bgr() == (0xf9, 0x93, 0xbd)
        frame._config.get_setting = lambda k, d=None: None
        assert frame._chat_accent_bgr() == frame.ACCENT
        frame._config.get_setting = lambda k, d=None: 'banana'
        assert frame._chat_accent_bgr() == frame.ACCENT
        _up.get_accent = lambda: '#58a6ff'   # ui_prefs wins over settings
        assert frame._chat_accent_bgr() == (0xff, 0xa6, 0x58)
    finally:
        frame._config.get_setting = orig
        _up.get_accent = orig_up


def test_toggle_voice_flips_gate_and_logs():
    before = _speech.VOICE_ENABLED
    out = frame.toggle_voice()
    assert out is (not before) and _speech.VOICE_ENABLED is out
    msgs = [m for (_ts, _lvl, m, _meta) in _config.recent_events(10)]
    assert any("Voice" in m for m in msgs)
    frame.toggle_voice()  # restore
    assert _speech.VOICE_ENABLED is before


def test_receive_file_logs_and_stages(tmp_path):
    p = tmp_path / "quarterly report.pdf"
    p.write_bytes(b"%PDF")
    name = frame.receive_file(str(p))
    assert name == "quarterly report.pdf"
    assert frame.staged_file() == str(p)
    msgs = [m for (_ts, _lvl, m, _meta) in _config.recent_events(10)]
    assert "File received: quarterly report.pdf" in msgs


def test_submit_typed_routes_and_clears():
    got = []
    frame.set_submit_callback(got.append)
    _hud.TYPING_ACTIVE = True
    _hud.TYPING_BUFFER = "  status report please "
    assert frame.submit_typed() is True
    assert got == ["status report please"]
    assert _hud.TYPING_BUFFER == "" and _hud.TYPING_ACTIVE is False
    assert frame.submit_typed() is False  # empty buffer: no route


def test_spotify_data_honest_standby():
    spot = frame.spotify_data()
    assert spot["running"] is False and spot["playing"] is False


def test_handle_click_dispatch():
    # Spotify transport (geometry is rebuilt per draw; seed one box)
    frame._SPOT_BTNS = [(0, 0, 50, 30, "next")]
    assert frame.handle_click(10, 10) is True
    assert _spot_actions == ["next"]
    # VOICE button toggles the gate
    vx, vy, vw, vh = frame.VOICE_BTN
    before = _speech.VOICE_ENABLED
    assert frame.handle_click(vx + vw // 2, vy + vh // 2) is True
    assert _speech.VOICE_ENABLED is (not before)
    frame.toggle_voice()
    # CMDS button toggles the commands overlay flag
    cx, cy, cw, ch = frame.CMDS_BTN
    assert frame.handle_click(cx + 2, cy + 2) is True
    assert _hud.SHOW_COMMANDS is True
    _hud.SHOW_COMMANDS = False
    # Input box engages typing
    ix, iy, iw, ih = frame.INPUT_BOX
    assert frame.handle_click(ix + 5, iy + 5) is True
    assert _hud.TYPING_ACTIVE is True
    _hud.TYPING_ACTIVE = False
    # Empty space is not consumed
    assert frame.handle_click(640, 300) is False


def test_scroll_activity_clamps():
    frame.ACTIVITY_SCROLL = 0
    frame.scroll_activity(-3)
    assert frame.ACTIVITY_SCROLL == 0
    frame.scroll_activity(2)
    assert frame.ACTIVITY_SCROLL == 2
    frame.ACTIVITY_SCROLL = 0
