"""hud_ascii: the subtitle under IDLE (and the HUD generally)
must never show '?' for typographic characters (Alek's
screenshot, 2026-10-27: a vision description rendered as
'low?resolution', 'grey?blue', 'button?down', 'chest?up',
'off?white', 'blown?out' — the vision model's U+2011
non-breaking hyphens weren't in the substitution table and
the ASCII encode replaced everything unknown with '?')."""
import sys
import types
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

# hud pulls aria.speech at module level; the sandbox venv lacks
# speech_recognition — stub speech the way test_hud_telemetry does.
speech = types.ModuleType('aria.speech')
speech.VOICE_ENABLED = True
sys.modules['aria.speech'] = speech
pkg.speech = speech

from aria.hud import hud_ascii


def test_nonbreaking_hyphens_become_real_hyphens():
    txt = ('low\u2011resolution indoor portrait, grey\u2011blue '
           'button\u2011down shirt, chest\u2011up, off\u2011white '
           'walls, blown\u2011out glare')
    out = hud_ascii(txt)
    assert out == ('low-resolution indoor portrait, grey-blue '
                   'button-down shirt, chest-up, off-white '
                   'walls, blown-out glare')
    assert '?' not in out


def test_the_whole_typographic_family_maps():
    out = hud_ascii('a\u2010b\u2012c\u2013d\u2014e\u2212f '
                    '\u201csmart\u201d \u2018quotes\u2019 wait\u2026 '
                    '90\u00b0 x\u00d73 a\u2192b')
    assert '?' not in out
    assert out.startswith('a-b-c-d-e-f')
    assert '"smart"' in out and "'quotes'" in out
    assert 'wait...' in out and '90deg' in out and 'x3' in out
    assert 'a->b' in out


def test_accents_fold_and_nbsp_becomes_space():
    assert hud_ascii('café\u00a0au lait') == 'cafe au lait'


def test_plain_ascii_is_untouched():
    assert hud_ascii("He's centered - it's fine.") == \
        "He's centered - it's fine."
