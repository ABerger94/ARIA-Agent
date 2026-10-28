"""App lock x Gemini Live (2026-10-27, ChatGPT x ARIA loop
runner-up #3): Live bypassed the app lock entirely — its tool
runner called the dispatcher directly (never through
handle_action's locked guard), the mic pump checked presence
only, and the play sink wrote model audio straight to
sounddevice, past speech.py's lock gates.

Pinned here with the same standalone-load harness as test_live.py
and a stub aria.applock: while locked, ZERO tool executions, ZERO
mic chunks leave, ZERO audio samples are written, and the lock
watcher stops the manager and cancels the session task. Unlocked
controls prove each path still works.

Run per-file (own process), like every suite in this repo.
"""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'live_lock_test', Path(__file__).parents[1] / 'aria' / 'live.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


# -- lock stub ------------------------------------------------------
_STATE = {'locked': False}


def _install_lock():
    aria_mod = types.ModuleType('aria')
    applock_mod = types.ModuleType('aria.applock')
    applock_mod.is_locked = lambda: _STATE['locked']
    aria_mod.applock = applock_mod
    sys.modules['aria'] = aria_mod
    sys.modules['aria.applock'] = applock_mod


def _remove_lock():
    sys.modules.pop('aria', None)
    sys.modules.pop('aria.applock', None)
    _STATE['locked'] = False


class _Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# -- tool gate ------------------------------------------------------

def test_tool_call_refused_while_locked():
    _install_lock()
    try:
        _STATE['locked'] = True
        calls = []

        def runner(name, args):
            calls.append((name, args))
            return ('ran', True)

        fc = types.SimpleNamespace(id='c1', name='send_email', args={})
        out = asyncio.run(live._execute_tool_call(runner, fc))
        assert calls == [], f'locked Live executed a tool: {calls}'
        assert out['response']['result'] == 'ARIA is locked.', out
        # Control: unlocked, the same call goes through.
        _STATE['locked'] = False
        out2 = asyncio.run(live._execute_tool_call(runner, fc))
        assert calls == [('send_email', {})]
        assert out2['response']['result'] == 'ran'
    finally:
        _remove_lock()


def test_run_session_tool_call_refused_while_locked():
    _install_lock()
    try:
        _STATE['locked'] = True

        class _Session:
            def __init__(self):
                self.tool_responses = []
                self._served = False

            async def receive(self):
                if self._served:
                    return
                    yield  # unreachable — async generator marker
                self._served = True
                fc = types.SimpleNamespace(
                    id='c9', name='write_file', args={'path': 'x'})
                yield types.SimpleNamespace(
                    server_content=None,
                    tool_call=types.SimpleNamespace(function_calls=[fc]))

            async def send_tool_response(self, function_responses):
                self.tool_responses.append(function_responses)

        calls = []
        session = _Session()
        asyncio.run(live.run_session(
            session,
            tool_runner=lambda n, a: (calls.append((n, a)), ('ran', True))[1]))
        assert calls == [], f'locked session executed a tool: {calls}'
        result = session.tool_responses[0][0]['response']['result']
        assert result == 'ARIA is locked.', result
    finally:
        _remove_lock()


# -- mic + audio gates ----------------------------------------------

class _FakeInStream:
    cb = None

    def __init__(self, **kw):
        _FakeInStream.cb = kw.get('callback')

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class _FakeOutStream:
    writes = []

    def __init__(self, **kw):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass

    def write(self, data):
        _FakeOutStream.writes.append(data)


def _install_sd():
    sd = types.ModuleType('sounddevice')
    sd.RawInputStream = _FakeInStream
    sd.RawOutputStream = _FakeOutStream
    sys.modules['sounddevice'] = sd
    live._genai_types = types.SimpleNamespace(Blob=_Cfg)


def _remove_sd():
    sys.modules.pop('sounddevice', None)


def _pump_once(mgr, session):
    async def _drive():
        task = asyncio.create_task(
            mgr._mic_pump(session, asyncio.get_running_loop()))
        await asyncio.sleep(0.05)
        _FakeInStream.cb(b'\x00\x01', 512, None, None)
        await asyncio.sleep(0.4)
        mgr._stop.set()
        await asyncio.wait_for(task, 2)
    asyncio.run(_drive())


def test_mic_pump_drops_everything_while_locked():
    _install_lock()
    _install_sd()
    try:
        sent = []

        class _Session:
            async def send_realtime_input(self, audio):
                sent.append(audio)

        mgr = live.LiveManager(
            api_key='k', tool_runner=lambda n, a: ('', True))
        _STATE['locked'] = True
        _pump_once(mgr, _Session())
        assert sent == [], f'locked mic forwarded audio: {sent}'
        # Control: unlocked, a chunk is forwarded.
        mgr2 = live.LiveManager(
            api_key='k', tool_runner=lambda n, a: ('', True))
        _STATE['locked'] = False
        _pump_once(mgr2, _Session())
        assert len(sent) == 1 and sent[0].data == b'\x00\x01', sent
    finally:
        _remove_lock()
        _remove_sd()


def test_play_sink_drops_audio_while_locked():
    _install_lock()
    _install_sd()
    try:
        _FakeOutStream.writes = []
        mgr = live.LiveManager(
            api_key='k', tool_runner=lambda n, a: ('', True))
        sink, _stream = asyncio.run(mgr._play_sink())
        _STATE['locked'] = True
        sink(b'\x01\x02')
        assert _FakeOutStream.writes == [], _FakeOutStream.writes
        _STATE['locked'] = False
        sink(b'\x03\x04')
        assert _FakeOutStream.writes == [b'\x03\x04']
    finally:
        _remove_lock()
        _remove_sd()


# -- lock watcher shuts the session down -----------------------------

def test_lock_watch_stops_manager_and_cancels_session():
    _install_lock()
    try:
        _STATE['locked'] = True

        async def _drive():
            mgr = live.LiveManager(
                api_key='k', tool_runner=lambda n, a: ('', True))

            async def _never():
                await asyncio.sleep(60)

            task = asyncio.create_task(_never())
            await mgr._lock_watch(task)
            return mgr, task

        mgr, task = asyncio.run(_drive())
        assert mgr._stop.is_set()
        assert task.cancelled()
    finally:
        _remove_lock()
