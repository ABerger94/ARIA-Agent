"""Port 5 Customize: hue-shift math, wheel, prefs, stylesheet, dialog.

Pinned behaviours (from the Mark-LVI source): one accent re-derives
the whole family by hue shift with S/V ratios preserved; a near-grey
accent desaturates instead; an invalid accent changes nothing; the
dashboard stylesheet keeps its unlinked text colour under any accent.
"""
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


def _hsv(hex_str):
    r = int(hex_str[1:3], 16) / 255
    g = int(hex_str[3:5], 16) / 255
    b = int(hex_str[5:7], 16) / 255
    return colorsys.rgb_to_hsv(r, g, b)


def test_shift_palette_rotates_hue_preserving_sv():
    from aria.hue_wheel import shift_palette
    out = shift_palette({'PRI': '#00d4ff'}, '#00d4ff', '#ff0000')
    assert out == {'PRI': '#ff0000'}          # S=V=1 family -> pure red
    out2 = shift_palette({'A': '#007a99', 'B': '#d8f8ff'},
                         '#00d4ff', '#00d4ff')
    assert out2 == {'A': '#007a99', 'B': '#d8f8ff'}   # dh=0 is identity


def test_shift_palette_grey_desaturates_and_invalid_is_none():
    from aria.hue_wheel import shift_palette
    out = shift_palette({'PRI': '#00d4ff'}, '#00d4ff', '#808080')
    _h, s, _v = _hsv(out['PRI'])
    assert s == pytest.approx(0.15, abs=0.02)
    for bad in ('red', '#12345', '', None, '#gggggg'):
        assert shift_palette({'PRI': '#00d4ff'}, '#00d4ff', bad) is None


def test_hue_wheel_roundtrip_and_pick():
    app = _app()
    from aria.hue_wheel import HueWheel
    wheel = HueWheel('#00d4ff'); wheel.show()
    assert wheel.color() == '#00d4ff'
    wheel.set_color('#ff0000')
    assert wheel.color() == '#ff0000'
    picked = []
    wheel.hue_picked.connect(picked.append)
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent, Qt
    ev = QMouseEvent(QEvent.Type.MouseButtonPress,
                     QPointF(wheel.width() - 12, wheel.height() / 2),
                     Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    wheel.mousePressEvent(ev)
    assert picked and picked[0].startswith('#')
    wheel.close(); app.processEvents()


def test_ui_prefs_roundtrip_and_soft_fail(monkeypatch, tmp_path):
    import importlib
    prefs = importlib.import_module('aria.ui_prefs')
    target = tmp_path / 'ui.json'
    monkeypatch.setattr(prefs, '_ui_file', lambda: str(target))
    assert prefs.get_accent() is None
    prefs.set_accent('#FF3366')
    assert prefs.get_accent() == '#ff3366'
    prefs.set_accent(None)
    assert prefs.get_accent() is None
    target.write_text('{broken')
    assert prefs.get_accent() is None


def test_style_for_accent():
    from aria.dashboard import STYLE, style_for_accent
    assert style_for_accent(None) == STYLE
    assert style_for_accent('bogus') == STYLE
    shifted = style_for_accent('#ff0000')
    assert '#54d9e8' not in shifted
    assert '#dce6ef' in shifted               # text colour never linked
    assert len(shifted) == len(STYLE)         # hex-for-hex replacement


def test_customize_dialog_preview_commit_cancel(monkeypatch):
    app = _app()
    from PyQt6.QtWidgets import QPushButton, QWidget
    from aria.customize_panel import build_customize_dialog
    events = []
    parent = QWidget(); parent.show()
    dlg = build_customize_dialog(
        parent, '#00d4ff',
        lambda h: events.append(('preview', h)),
        lambda h: events.append(('commit', h)))
    # Click the Green preset -> preview fires with the preset hex.
    for btn in dlg.findChildren(QPushButton):
        if btn.text() == 'Green':
            btn.click()
    assert ('preview', '#00ff88') in events
    assert dlg._hex_label.text() == '#00ff88'
    for btn in dlg.findChildren(QPushButton):
        if btn.text() == 'Apply':
            btn.click()
    assert events[-1] == ('commit', '#00ff88')

    events.clear()
    dlg2 = build_customize_dialog(
        parent, '#00d4ff',
        lambda h: events.append(('preview', h)),
        lambda h: events.append(('commit', h)))
    for btn in dlg2.findChildren(QPushButton):
        if btn.text() == 'Cancel':
            btn.click()
    assert events == [('preview', '#00d4ff')]  # cancel restores the start
    parent.close(); app.processEvents()
