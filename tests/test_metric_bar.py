"""MetricBar + StatusPlaque port: thresholds, clamping, value cap.

Adapted from Mark-LVI-ARIA's ui.py (see aria/metric_bar.py). The pinned
behaviours are the source's: bar colour is a threshold warning (orange
over 65, red over 85), unchanged values skip the repaint, and plaque
values cap at 16 characters.
"""
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


def test_metric_bar_clamps_and_thresholds():
    app = _app()
    from aria.metric_bar import MetricBar, C
    bar = MetricBar('CPU'); bar.resize(220, 38); bar.show()
    bar.set_value(140, '140%')
    assert bar._value == 100.0
    assert bar._bar_color() == C.RED
    bar.set_value(70, '70%')
    assert bar._bar_color() == C.ACC
    bar.set_value(25, '25%')
    assert bar._bar_color() == C.PRI
    bar.set_value(-5, '--')
    assert bar._value == 0.0
    assert not bar.grab().isNull()
    bar.close(); app.processEvents()


def test_metric_bar_unchanged_skips_repaint(monkeypatch):
    app = _app()
    from aria.metric_bar import MetricBar
    bar = MetricBar('MEM'); bar.show()
    bar.set_value(40, '40%')
    updates = []
    monkeypatch.setattr(bar, 'update', lambda: updates.append(1))
    bar.set_value(40, '40%')
    assert updates == []
    bar.set_value(41, '41%')
    assert updates == [1]
    bar.close(); app.processEvents()


def test_status_plaque_caps_value_at_16():
    app = _app()
    from aria.metric_bar import StatusPlaque
    plaque = StatusPlaque('ENGINE'); plaque.show()
    plaque.set_value('A very long provider name indeed')
    assert plaque._value == 'A very long prov'
    assert 'ENGINE' in plaque._label.text()
    assert plaque._value in plaque._label.text()
    plaque.set_value('Ollama')
    assert plaque._value == 'Ollama'
    assert not plaque.grab().isNull()
    plaque.close(); app.processEvents()
