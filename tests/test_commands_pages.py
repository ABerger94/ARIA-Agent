"""Commands overlay pages test — the regression that Phase 4's render
verification missed because the overlay was never opened against the
REAL guide.

Bug (user screenshot, 2026-10-21): opening the Commands overlay
painted "HUD RENDER FAULT: 'list' object has no attribute 'items'"
(main.py's render guard). aria/tools/schemas.py defines COMMAND_GUIDE
as a LIST of (category, action, phrase) triples, but hud's
_build_commands_pages() iterated it as a dict. Both shapes coexisted
since the original f80957e — a pre-existing bug, not merger-caused.

This test deliberately does NOT stub COMMAND_GUIDE: it imports the
real guide and the real hud._build_commands_pages and asserts the
pages build cleanly and lose no entries. Heavy render deps (cv2,
psutil, pixel_avatar, vision) are stubbed per suite convention; the
guide and the page builder are the real code.
"""
import importlib
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_pkg = types.ModuleType("aria")
_pkg.__path__ = [os.path.join(HERE, "..", "aria")]
sys.modules["aria"] = _pkg
_tools = types.ModuleType("aria.tools")
_tools.__path__ = [os.path.join(HERE, "..", "aria", "tools")]
sys.modules["aria.tools"] = _tools

import types as _t
_mod("cv2")
_mod("psutil",
     net_io_counters=lambda: _t.SimpleNamespace(bytes_sent=0, bytes_recv=0),
     boot_time=lambda: time.time() - 3600)
_mod("aria.pixel_avatar",
     draw_pixel_aria=lambda *a, **k: None,
     load_theme=lambda: {})
_mod("aria.vision", publish_face_frame=lambda *a, **k: None)

config = importlib.import_module("aria.config")
schemas = importlib.import_module("aria.tools.schemas")
hud = importlib.import_module("aria.hud")


def test_build_commands_pages_real_guide():
    guide = schemas.COMMAND_GUIDE
    assert isinstance(guide, list) and guide, "guide must be the real list"
    pages = hud._build_commands_pages()
    assert pages, "overlay pages must be non-empty"
    flat = [(act, phr)
            for page in pages
            for (_cat, items) in page
            for (act, phr) in items]
    # No entry lost or duplicated by pagination/columns.
    assert len(flat) == len(guide), (len(flat), len(guide))
    # The known Memory row survives the trip through the page builder.
    assert ("save_memory",
            "remember my Doja playlist is spotify:playlist:xxx") in flat
    # First column is headed by the first entry's category.
    assert pages[0][0][0] == guide[0][0] == "Memory"
