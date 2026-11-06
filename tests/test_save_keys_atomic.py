"""save_keys must not destroy the previous key file on failure
(pass 25 P2#11).

The old code truncated aria_keys.json with open("w") and then
dumped, so a failure mid-dump left a truncated/partial file. The
save now goes through the canonical atomic helper (temp file +
os.replace): a failed save leaves the previous file byte-identical.
"""
import json
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_save_keys_')
os.environ['HOME'] = _TMPHOME
os.environ['ARIA_DATA_ROOT'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg

import importlib
config = importlib.import_module('aria.config')


def test_save_keys_roundtrip():
    config._KEYS.clear()
    config._KEYS.update({"GROQ_API_KEY": "new-key"})
    assert config.save_keys() is True
    with open(config.KEYS_FILE, encoding="utf-8") as f:
        assert json.load(f)["GROQ_API_KEY"] == "new-key"


def test_failed_save_keeps_previous_file():
    good = {"GROQ_API_KEY": "keep-me"}
    with open(config.KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(good, f, indent=2)
    before = open(config.KEYS_FILE, "rb").read()
    config._KEYS.clear()
    config._KEYS.update({"GROQ_API_KEY": object()})  # not JSON-serializable
    assert config.save_keys() is False
    assert open(config.KEYS_FILE, "rb").read() == before
    assert json.loads(before) == good
