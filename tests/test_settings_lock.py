"""Pass 26 B3#5: concurrent set_setting calls must not lose keys.

set_setting used to be an unlocked read-modify-write (load the
whole settings dict, mutate one key, save the whole dict), so two
threads setting different keys at once could each load the same
snapshot and the second save would silently drop the first key.
Pins: N interleaved writers per side, every key survives.
"""
import importlib
import os
import sys
import tempfile
import threading
import types

_TMP = tempfile.mkdtemp(prefix='aria_settings_lock_')
os.environ['HOME'] = _TMP

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
_pkg = types.ModuleType('aria')
_pkg.__path__ = [PKG]
sys.modules.setdefault('aria', _pkg)

config = importlib.import_module('aria.config')

# Isolate the settings file: never touch the real workspace settings.
config.SETTINGS_FILE = os.path.join(_TMP, 'settings.json')

N = 50


def _writer(prefix, barrier):
    barrier.wait()
    for i in range(N):
        config.set_setting(f'{prefix}_{i}', i)


def test_concurrent_set_setting_keeps_both_keys():
    barrier = threading.Barrier(2)
    threads = [
        threading.Thread(target=_writer, args=('key_a', barrier)),
        threading.Thread(target=_writer, args=('key_b', barrier)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), 'writer thread hung'
    saved = config.settings_load()
    missing = [f'key_{s}_{i}' for s in ('a', 'b') for i in range(N)
               if f'key_{s}_{i}' not in saved]
    assert not missing, f'lost {len(missing)} keys under concurrency: {missing[:10]}'
