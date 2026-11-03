"""Named palettes (2026-11-03, Alek's picks from the palette
cards he sent): the registry must carry the cards' hexes
EXACTLY; Midnight Neon is the default; ui_prefs persists
palette id + hero together; the dashboard stylesheet tints
its panels from the palette's Primary; the Customize dialog
fires on_palette before on_commit on a palette Apply, and
Reset restores the default palette."""
import colorsys
import os
import sys
import types
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg


def _app():
    return QApplication.instance() or QApplication([])


def _hls(hex_str):
    r = int(hex_str[1:3], 16) / 255
    g = int(hex_str[3:5], 16) / 255
    b = int(hex_str[5:7], 16) / 255
    return colorsys.rgb_to_hls(r, g, b)


# The cards, transcribed: every role hex, exactly as printed.
EXPECTED = {
    'apple_modern': {
        'name': 'Apple Modern', 'primary': '#1d1d1f',
        'secondary': None, 'tertiary': '#aaaaaa',
        'accent': '#007aff', 'background': '#f5f5f7',
        'hero': '#007aff', 'tone2': '#aaaaaa'},
    'vibrant_sunset': {
        'name': 'Vibrant Sunset', 'primary': '#4d3a4d',
        'secondary': '#be5ca9', 'tertiary': '#d59cc5',
        'accent': None, 'background': '#eadada',
        'hero': '#be5ca9', 'tone2': '#d59cc5'},
    'ocean_breeze': {
        'name': 'Ocean Breeze', 'primary': '#0b3d91',
        'secondary': '#3ba7f2', 'tertiary': '#7fe7d6',
        'accent': None, 'background': '#e8f6ff',
        'hero': '#3ba7f2', 'tone2': '#7fe7d6'},
    'earthy_minimal': {
        'name': 'Earthy Minimal', 'primary': '#2e3a2f',
        'secondary': '#6b7f5b', 'tertiary': '#d9c9b2',
        'accent': '#c96f4f', 'background': '#f8f6ee',
        'hero': '#c96f4f', 'tone2': '#6b7f5b'},
    'midnight_neon': {
        'name': 'Midnight Neon', 'primary': '#0f172a',
        'secondary': '#7c3aed', 'tertiary': '#a78bfa',
        'accent': '#22d3ee', 'background': '#ede9fe',
        'hero': '#22d3ee', 'tone2': '#7c3aed'},
}


def test_registry_matches_the_cards_exactly():
    from aria import palettes
    assert palettes.palette_ids() == list(EXPECTED)
    for pid, want in EXPECTED.items():
        pal = palettes.get_palette(pid)
        for key, value in want.items():
            assert pal[key] == value, (pid, key)
        # hero/tone2 are always real colours from the card.
        assert pal['hero'] in palettes.swatch_hexes(pal)
        assert pal['tone2'] in palettes.swatch_hexes(pal)
    assert palettes.get_palette('nope') is None
    assert palettes.hero_of('nope') is None
    assert not palettes.is_valid_palette_id('nope')


def test_midnight_neon_is_the_default():
    from aria import palettes
    assert palettes.DEFAULT_PALETTE_ID == 'midnight_neon'
    assert palettes.DEFAULT_ACCENT == '#22d3ee'
    assert palettes.hero_of(palettes.DEFAULT_PALETTE_ID) \
        == palettes.DEFAULT_ACCENT


def test_panel_shade_keeps_primary_hue_at_given_lightness():
    from aria import palettes
    shade = palettes.panel_shade('#0b3d91', 0.05)
    h_want, _l, s_want = _hls('#0b3d91')
    h_got, l_got, s_got = _hls(shade)
    assert h_got == pytest.approx(h_want, abs=0.02)
    assert s_got == pytest.approx(s_want, abs=0.03)
    assert l_got == pytest.approx(0.05, abs=0.02)


def test_ui_prefs_palette_roundtrip(monkeypatch, tmp_path):
    import importlib
    prefs = importlib.import_module('aria.ui_prefs')
    target = tmp_path / 'ui.json'
    monkeypatch.setattr(prefs, '_ui_file', lambda: str(target))
    assert prefs.get_palette() is None
    # One write stores id AND hero-as-accent together.
    assert prefs.set_palette('ocean_breeze') \
        == 'Palette: ocean_breeze.'
    assert prefs.get_palette() == 'ocean_breeze'
    assert prefs.get_accent() == '#3ba7f2'
    # An unknown id changes nothing.
    assert prefs.set_palette('bogus') == 'Palette: unknown (bogus).'
    assert prefs.get_palette() == 'ocean_breeze'
    assert prefs.get_accent() == '#3ba7f2'
    # Clearing returns to the (Midnight Neon) default resolution.
    prefs.set_palette(None)
    assert prefs.get_palette() is None
    assert prefs.get_accent() is None
    target.write_text('{broken')
    assert prefs.get_palette() is None


def test_style_for_palette_tints_panels_from_primary():
    from aria.dashboard import STYLE, style_for_palette
    assert style_for_palette('bogus') == STYLE
    sheet = style_for_palette('ocean_breeze')
    assert sheet != STYLE
    assert '#dce6ef' in sheet            # body text never linked
    # The page background #080d14 (lightness ~0.055) re-derived
    # from Ocean Breeze's Primary #0b3d91 at that lightness.
    from aria import palettes
    from aria.dashboard import _PANEL_HEXES
    import colorsys as _cs
    r = int('#080d14'[1:3], 16) / 255
    g = int('#080d14'[3:5], 16) / 255
    b = int('#080d14'[5:7], 16) / 255
    _h, lightness, _s = _cs.rgb_to_hls(r, g, b)
    derived = palettes.panel_shade('#0b3d91', lightness)
    assert derived in sheet
    assert '#080d14' in _PANEL_HEXES


def test_chat_default_is_midnight_neon_when_nothing_set(monkeypatch):
    import aria.dashboard as dash
    monkeypatch.setattr(dash, '_current_accent', lambda: None)
    monkeypatch.setattr(dash, '_default_accent', lambda: '#22d3ee')
    colors = dash.chat_colors()
    assert colors['aria'] == ('#22d3ee', '#22d3ee')
    assert colors['user'] == ('#ffffff', '#f4f7fa')


def test_dialog_palette_apply_fires_palette_before_commit():
    app = _app()
    from PyQt6.QtWidgets import QPushButton, QWidget
    from aria.customize_panel import build_customize_dialog
    events = []
    parent = QWidget(); parent.show()
    dlg = build_customize_dialog(
        parent, '#54d9e8',
        lambda h: events.append(('preview', h)),
        lambda h: events.append(('commit', h)),
        lambda pid: events.append(('palette', pid)))
    for btn in dlg.findChildren(QPushButton):
        if btn.text() == 'Ocean Breeze':
            btn.click()
    assert ('preview', '#3ba7f2') in events
    for btn in dlg.findChildren(QPushButton):
        if btn.text() == 'Apply':
            btn.click()
    # Palette persists FIRST so the commit's apply path can
    # already see it (panel tint), then the hero commits.
    assert events[-2:] == [('palette', 'ocean_breeze'),
                           ('commit', '#3ba7f2')]
    parent.close(); app.processEvents()


def test_dialog_wheel_pick_clears_pending_palette():
    app = _app()
    from PyQt6.QtWidgets import QPushButton, QWidget
    from aria.customize_panel import build_customize_dialog
    events = []
    parent = QWidget(); parent.show()
    dlg = build_customize_dialog(
        parent, '#54d9e8',
        lambda h: events.append(('preview', h)),
        lambda h: events.append(('commit', h)),
        lambda pid: events.append(('palette', pid)))
    for btn in dlg.findChildren(QPushButton):
        if btn.text() == 'Earthy Minimal':
            btn.click()
    for btn in dlg.findChildren(QPushButton):
        if btn.text() == 'Green':      # single-colour preset wins
            btn.click()
    for btn in dlg.findChildren(QPushButton):
        if btn.text() == 'Apply':
            btn.click()
    assert not [e for e in events if e[0] == 'palette']
    assert events[-1] == ('commit', '#00ff88')
    parent.close(); app.processEvents()


def test_dialog_reset_restores_midnight_neon():
    app = _app()
    from PyQt6.QtWidgets import QPushButton, QWidget
    from aria.customize_panel import build_customize_dialog
    events = []
    parent = QWidget(); parent.show()
    dlg = build_customize_dialog(
        parent, '#ff3366',
        lambda h: events.append(('preview', h)),
        lambda h: events.append(('commit', h)),
        lambda pid: events.append(('palette', pid)))
    for btn in dlg.findChildren(QPushButton):
        if btn.text() == 'Reset to default':
            btn.click()
    assert events == [('palette', 'midnight_neon'),
                      ('commit', '#22d3ee')]
    parent.close(); app.processEvents()
