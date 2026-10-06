import os
"Role-based model routing tests (suite E): resolve_role_model,\n_attempt_provider_call override/fallback/restore, inline_data -> image_url\nconversion, and the vision hook default.\n\nFollows the test_headless.py stub pattern: bypasses aria/__init__.py's eager\nimports and stubs hardware-bound modules (cv2 is absent on this box).\n"
import sys
import types
PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
tools_pkg = types.ModuleType('aria.tools')
tools_pkg.__path__ = [PKG + '/tools']
sys.modules['aria.tools'] = tools_pkg
agent_pkg = types.ModuleType('aria.agent')
agent_pkg.__path__ = [PKG + '/agent']
sys.modules['aria.agent'] = agent_pkg
_ps = types.ModuleType('psutil')
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules['psutil'] = _ps
sys.modules['cv2'] = types.ModuleType('cv2')
_hw = types.ModuleType('aria.hardware')
_hw.send_servo_command = lambda *a, **k: None
_hw.SERVO_POS = {'pan': 90, 'tilt': 45}
sys.modules['aria.hardware'] = _hw
sys.modules['aria.agent.brain'] = types.ModuleType('aria.agent.brain')
import importlib
providers = importlib.import_module('aria.agent.providers')
vision = importlib.import_module('aria.vision')

def test_role_vision():
    assert providers.resolve_role_model('vision') == 'gemma4:31b-cloud'

def test_role_code():
    assert providers.resolve_role_model('code') == 'qwen3-coder:480b-cloud'

def test_role_default_none():
    assert providers.resolve_role_model('default') is None
    assert providers.resolve_role_model('') is None
    assert providers.resolve_role_model('bogus-role') is None

def test_role_empty_override_disables():
    old = providers.OLLAMA_VISION_MODEL
    providers.OLLAMA_VISION_MODEL = ''
    try:
        assert providers.resolve_role_model('vision') is None
    finally:
        providers.OLLAMA_VISION_MODEL = old

def _user_msg(contents):
    msgs = providers.gemini_contents_to_oai_messages('sys', contents)
    return [m for m in msgs if m.get('role') == 'user']

def test_image_becomes_image_url():
    contents = [{'role': 'user', 'parts': [{'text': 'what is this'}, {'inline_data': {'mime_type': 'image/jpeg', 'data': 'QUJD'}}]}]
    um = _user_msg(contents)
    assert len(um) == 1, um
    content = um[0]['content']
    assert isinstance(content, list), content
    kinds = [c['type'] for c in content]
    assert kinds == ['image_url', 'text'], kinds
    assert content[1]['text'] == 'what is this'
    assert content[0]['image_url']['url'] == 'data:image/jpeg;base64,QUJD'

def test_pure_text_unchanged():
    contents = [{'role': 'user', 'parts': [{'text': 'a'}, {'text': 'b'}]}]
    um = _user_msg(contents)
    assert len(um) == 1, um
    assert um[0]['content'] == 'a\nb', um[0]['content']

class StubProvider:

    def __init__(self, name, model, fail_on=()):
        self.name = name
        self.model = model
        self.fail_on = set(fail_on)
        self.seen = []
        self.last_error = None

    def is_available(self):
        return True

    def call(self, system_instruction, contents, tool_decls=None, on_text_chunk=None):
        self.seen.append(self.model)
        if self.model in self.fail_on:
            raise RuntimeError(f'model {self.model} exploded')
        return {'candidates': [{'content': {'parts': [{'text': 'ok'}]}}]}

def test_override_used_and_restored():
    p = StubProvider('ollama_cloud', 'gpt-oss:120b')
    data, fb = providers._attempt_provider_call(p, 's', [], None, None, 'gemma4:31b-cloud')
    assert data['candidates'][0]['content']['parts'][0]['text'] == 'ok'
    assert fb is False
    assert p.seen == ['gemma4:31b-cloud'], p.seen
    assert p.model == 'gpt-oss:120b', 'model not restored'

def test_override_fails_falls_back_no_raise():
    p = StubProvider('ollama_cloud', 'gpt-oss:120b', fail_on={'gemma4:31b-cloud'})
    try:
        data, fb = providers._attempt_provider_call(p, 's', [], None, None, 'gemma4:31b-cloud')
        assert fb is True
        assert p.seen == ['gemma4:31b-cloud', 'gpt-oss:120b'], p.seen
        assert p.model == 'gpt-oss:120b', 'model not restored after fallback'
    finally:
        providers.clear_role_model_cooldown()

def test_override_none_result_falls_back():

    class NoneThenOk(StubProvider):

        def call(self, system_instruction, contents, tool_decls=None, on_text_chunk=None):
            self.seen.append(self.model)
            if self.model == 'gemma4:31b-cloud':
                self.last_error = "HTTP 400: {'error': 'bad request'}"
                return None
            return {'candidates': [{'content': {'parts': [{'text': 'ok'}]}}]}
    p = NoneThenOk('ollama_cloud', 'gpt-oss:120b')
    p.last_error = None
    try:
        data, fb = providers._attempt_provider_call(p, 's', [], None, None, 'gemma4:31b-cloud')
        assert fb is True
        assert p.seen == ['gemma4:31b-cloud', 'gpt-oss:120b'], p.seen
        assert data['candidates'][0]['content']['parts'][0]['text'] == 'ok'
        assert p.model == 'gpt-oss:120b'
    finally:
        providers.clear_role_model_cooldown()

def test_both_fail_returns_none_and_restores():
    p = StubProvider('ollama_cloud', 'gpt-oss:120b', fail_on={'gemma4:31b-cloud', 'gpt-oss:120b'})
    try:
        data, fb = providers._attempt_provider_call(p, 's', [], None, None, 'gemma4:31b-cloud')
        assert data is None
        assert fb is True
    finally:
        providers.clear_role_model_cooldown()
    assert p.model == 'gpt-oss:120b', 'model not restored after double failure'

def test_override_ignored_off_ollama_cloud():
    p = StubProvider('groq', 'llama-3.3-70b-versatile')
    data, fb = providers._attempt_provider_call(p, 's', [], None, None, 'gemma4:31b-cloud')
    assert p.seen == ['llama-3.3-70b-versatile'], p.seen
    assert fb is False

def test_vision_hook_attached():
    assert vision._VISION_TEXT_CALL is not None
    assert vision._VISION_TEXT_CALL.__name__ == '_default_vision_call'

def _canned_contents():
    return [{'role': 'user', 'parts': [{'text': 'describe'}, {'inline_data': {'mime_type': 'image/jpeg', 'data': 'QUJD'}}]}]

def test_vision_uses_chain_not_gemini():
    calls = {}

    def fake_provider_call(sys_prompt, contents, tool_decls=None, on_text_chunk=None, model_override=None, **kwargs):
        calls['override'] = model_override
        return {'candidates': [{'content': {'parts': [{'text': 'a red bicycle'}]}}]}
    old = providers.provider_call
    providers.provider_call = fake_provider_call
    try:
        out = vision._default_vision_call('sys', _canned_contents())
    finally:
        providers.provider_call = old
    assert out == 'a red bicycle', out
    assert calls['override'] == 'gemma4:31b-cloud', calls

def test_vision_chain_down_falls_back_cleanly():

    def dead_provider_call(*a, **k):
        return None
    old = providers.provider_call
    providers.provider_call = dead_provider_call
    try:
        out = vision._default_vision_call('sys', _canned_contents())
    finally:
        providers.provider_call = old
    assert out.startswith('[Vision unavailable:'), out

def test_http_400_does_not_quarantine_key():
    import io
    import urllib.error
    quarantined = []
    old_q = providers.quarantine_key
    providers.quarantine_key = lambda key, dur, code: quarantined.append((key, dur, code))
    old_urlopen = providers.urllib.request.urlopen

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, 'Bad Request', {}, io.BytesIO(b'{"error":{"message":"unknown model"}}'))
    providers.urllib.request.urlopen = boom
    try:
        p = providers.OpenAICompatProvider('ollama_cloud', 'https://ollama.com/v1', 'test-key-123', 'gemma4:31b-cloud')
        out = p.call('sys', [{'role': 'user', 'parts': [{'text': 'hi'}]}])
    finally:
        providers.quarantine_key = old_q
        providers.urllib.request.urlopen = old_urlopen
    assert out is None
    assert quarantined == [], quarantined
    assert 'unknown model' in (p.last_error or ''), p.last_error

def test_only_provider_restricts_chain():
    tried = []

    class ChainStub(StubProvider):

        def call(self, system_instruction, contents, tool_decls=None, on_text_chunk=None):
            tried.append(self.name)
            return {'candidates': [{'content': {'parts': [{'text': 'ok'}]}}]}
    old_build = providers._build_chain
    providers._build_chain = lambda: [ChainStub('groq', 'm1'), ChainStub('ollama_cloud', 'm2')]
    try:
        data = providers.provider_call('s', [{'role': 'user', 'parts': [{'text': 'hi'}]}], only_provider='ollama_cloud')
    finally:
        providers._build_chain = old_build
    assert tried == ['ollama_cloud'], tried
    assert data['candidates'][0]['content']['parts'][0]['text'] == 'ok'

def test_native_vision_payload_format():
    import io
    import json as _json
    try:
        vision_mod = importlib.import_module('aria.vision')
    except Exception as e:
        print(f'SKIP t_native_vision_payload_format: {e}')
        return
    sent = {}

    class FakeResp:

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps({'message': {'role': 'assistant', 'content': 'a red bicycle'}}).encode()

    def fake_urlopen(req, timeout=None):
        sent['url'] = req.full_url
        sent['auth'] = req.get_header('Authorization', '')
        sent['body'] = _json.loads(req.data.decode())
        return FakeResp()
    old_key = getattr(providers, 'OLLAMA_CLOUD_API_KEY', '')
    old_urlopen = vision_mod.urllib.request.urlopen
    providers.OLLAMA_CLOUD_API_KEY = 'test-key-xyz'
    vision_mod.urllib.request.urlopen = fake_urlopen
    try:
        out = vision_mod._ollama_native_vision_call('sys', [{'role': 'user', 'parts': [{'text': 'what'}, {'inline_data': {'mime_type': 'image/jpeg', 'data': 'QUJD'}}]}])
    finally:
        providers.OLLAMA_CLOUD_API_KEY = old_key
        vision_mod.urllib.request.urlopen = old_urlopen
    assert out == 'a red bicycle', out
    assert sent['url'] == 'https://ollama.com/api/chat', sent['url']
    assert sent['auth'] == 'Bearer test-key-xyz', sent['auth']
    assert sent['body']['model'] == 'gemma4:31b-cloud', sent['body']['model']
    assert sent['body']['stream'] is False
    assert sent['body']['messages'][-1]['images'] == ['QUJD'], sent['body']['messages']
    assert sent['body']['messages'][-1]['content'] == 'what'

def test_native_probe_distinguishes_model_vs_format():
    import io
    import json as _json
    import urllib.error
    try:
        vision_mod = importlib.import_module('aria.vision')
    except Exception as e:
        print(f'SKIP t_native_probe_distinguishes_model_vs_format: {e}')
        return

    def boom_404(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', {}, io.BytesIO(b'{"error":"not found"}'))

    class FakeResp:

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps({'message': {'role': 'assistant', 'content': 'ok'}}).encode()
    old_key = getattr(providers, 'OLLAMA_CLOUD_API_KEY', '')
    old_urlopen = vision_mod.urllib.request.urlopen
    providers.OLLAMA_CLOUD_API_KEY = 'test-key-xyz'
    try:
        vision_mod.urllib.request.urlopen = lambda req, timeout=None: FakeResp()
        assert vision_mod._probe_model_text('gemma4:31b-cloud', 'k') is None
        vision_mod.urllib.request.urlopen = boom_404
        err = vision_mod._probe_model_text('gemma4:31b-cloud', 'k')
        assert err is not None and '404' in err, err
    finally:
        providers.OLLAMA_CLOUD_API_KEY = old_key
        vision_mod.urllib.request.urlopen = old_urlopen

def test_default_vision_call_prefers_native():
    import json as _json
    try:
        vision_mod = importlib.import_module('aria.vision')
    except Exception as e:
        print(f'SKIP t_default_vision_call_prefers_native: {e}')
        return
    assert vision_mod._VISION_TEXT_CALL is vision_mod._default_vision_call

    class FakeResp:

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps({'message': {'role': 'assistant', 'content': 'a red bicycle'}}).encode()
    chain_touched = []

    def fake_urlopen(req, timeout=None):
        return FakeResp()
    old_key = getattr(providers, 'OLLAMA_CLOUD_API_KEY', '')
    old_urlopen = vision_mod.urllib.request.urlopen
    old_chain = providers.provider_call
    providers.OLLAMA_CLOUD_API_KEY = 'test-key-xyz'
    vision_mod.urllib.request.urlopen = fake_urlopen
    providers.provider_call = lambda *a, **k: chain_touched.append(1) or None
    try:
        out = vision_mod._default_vision_call('sys', [{'role': 'user', 'parts': [{'text': 'what'}, {'inline_data': {'mime_type': 'image/jpeg', 'data': 'QUJD'}}]}])
    finally:
        providers.OLLAMA_CLOUD_API_KEY = old_key
        vision_mod.urllib.request.urlopen = old_urlopen
        providers.provider_call = old_chain
    assert out == 'a red bicycle', out
    assert chain_touched == [], 'native succeeded but chain was still walked'

def _load_scheduler_stubbed():
    """Import aria.scheduler with heavy deps stubbed (memory, speech)."""
    import types
    import threading
    import queue
    mem_stub = types.ModuleType('aria.memory')
    mem_stub.DB_LOCK = threading.Lock()
    mem_stub.DB_PATH = ':memory:'
    sys.modules['aria.memory'] = mem_stub
    speech_stub = types.ModuleType('aria.speech')
    speech_stub.speak = lambda *a, **k: None
    speech_stub._SPEECH_QUEUE = queue.Queue()
    sys.modules['aria.speech'] = speech_stub
    return importlib.import_module('aria.scheduler')

def test_schedule_purges_dead_dock_entries():
    import json as _json
    import tempfile
    sched = _load_scheduler_stubbed()
    with tempfile.NamedTemporaryFile('w+', suffix='.json', delete=False) as f:
        _json.dump([{'date': '2026-10-06', 'start': '16:00', 'end': '21:00', 'summary': 'Dock of the Bay — Wait'}, {'date': '2026-11-20', 'start': '19:30', 'end': '23:00', 'summary': 'Doja Cat — Tour Ma Vie'}], f)
        path = f.name
    old = sched.SCHEDULE_FILE
    sched.SCHEDULE_FILE = path
    try:
        entries = sched._load_schedule()
        assert all(('dock of the bay' not in e['summary'].lower() for e in entries)), entries
        assert any(('Doja Cat' in e['summary'] for e in entries)), entries
        on_disk = _json.load(open(path))
        assert all(('dock of the bay' not in e['summary'].lower() for e in on_disk)), on_disk
    finally:
        sched.SCHEDULE_FILE = old
        os.unlink(path)

def test_today_entries_prefers_live_then_falls_back():
    sched = _load_scheduler_stubbed()
    from datetime import datetime as _dt
    today_s = _dt.now().strftime('%Y-%m-%d')
    sentinel = [{'date': 'x', 'summary': 'local'}]
    old_live = sched._live_entries
    old_today = sched._today_entries
    try:
        sched._live_entries = lambda days=2: ([{'date': today_s, 'summary': 'Live Event'}], True)
        entries, is_live = sched.today_entries_prefer_live()
        assert is_live is True and entries[0]['summary'] == 'Live Event', entries
        sched._live_entries = lambda days=2: ([], False)
        sched._today_entries = lambda: sentinel
        entries, is_live = sched.today_entries_prefer_live()
        assert is_live is False and entries is sentinel, entries
        sched._live_entries = lambda days=2: ([{'date': today_s, 'summary': 'Dock of the Bay — Wait'}], True)
        entries, is_live = sched.today_entries_prefer_live()
        assert entries == [], entries
    finally:
        sched._live_entries = old_live
        sched._today_entries = old_today

def test_describe_camera_tool_wired():
    try:
        vision_mod = importlib.import_module('aria.vision')
    except Exception as e:
        print(f'SKIP t_describe_camera_tool_wired: {e}')
        return
    old_cap = vision_mod.capture_webcam
    old_hook = vision_mod._VISION_TEXT_CALL
    seen = {}
    vision_mod.capture_webcam = lambda: b'\xff\xd8fakejpeg'

    def fake_call(sys_prompt, contents):
        seen['sys'] = sys_prompt
        seen['contents'] = contents
        return 'a person wearing a red shirt'
    vision_mod._VISION_TEXT_CALL = fake_call
    try:
        out = vision_mod.tool_describe_camera('what do I look like?')
    finally:
        vision_mod.capture_webcam = old_cap
        vision_mod._VISION_TEXT_CALL = old_hook
    assert out == 'a person wearing a red shirt', out
    parts = seen['contents'][0]['parts']
    assert parts[0]['text'] == 'what do I look like?', parts[0]
    assert parts[1]['inline_data']['mime_type'] == 'image/jpeg'
    import base64 as _b64
    assert _b64.b64decode(parts[1]['inline_data']['data']) == b'\xff\xd8fakejpeg'
    assert 'camera eyes' in seen['sys']

def test_describe_camera_schema_and_dispatch():
    import importlib.util
    spec = importlib.util.spec_from_file_location('schemas_diag', os.path.join(PKG, 'tools', 'schemas.py'))
    schemas = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(schemas)
    names = [d['name'] for d in schemas.ALL_FUNCTION_DECLARATIONS]
    assert 'describe_camera' in names, 'schema missing describe_camera'
    decl = next((d for d in schemas.ALL_FUNCTION_DECLARATIONS if d['name'] == 'describe_camera'))
    assert 'webcam' in decl['description'].lower(), decl['description']
    assert 'describe_camera' in schemas.TOOLKITS['vision']['tools'], 'vision toolkit missing it'
    with open(os.path.join(PKG, 'tools', 'dispatch.py'), encoding='utf-8') as f:
        src = f.read()
    assert '_REGISTRY["describe_camera"]' in src, 'dispatch missing describe_camera'
    assert 'tool_describe_camera' in src

def test_no_directive_executed_lie():
    with open(os.path.join(PKG, 'agent', 'brain.py'), encoding='utf-8') as f:
        src = f.read()
    assert 'Directive executed' not in src, 'brain.py still claims success on empty model responses'

def test_empty_response_retries_then_admits():
    with open(os.path.join(PKG, 'agent', 'brain.py'), encoding='utf-8') as f:
        src = f.read()
    assert "I didn't catch that" in src, 'missing honest empty-response fallback'
    assert '_empty_retries' in src, 'missing empty-response retry guard'

def test_system_prompt_forbids_empty_response():
    with open(os.path.join(PKG, 'agent', 'brain.py'), encoding='utf-8') as f:
        src = f.read()
    assert 'never return an empty response' in src.lower(), 'system prompt must forbid empty model responses'

def test_sentinel_trips_on_runaway_loop():
    from aria.agent import sentinel
    sentinel.set_enabled(True)
    sentinel.reset()
    t0 = 1000.0
    trip = None
    for i in range(6):
        trip = sentinel.record('read_file', {'filename': 'x.py'}, now=t0 + i)
    assert trip is not None, '6 identical calls in 10s must trip'
    assert trip['tool'] == 'read_file' and trip['count'] == 6, trip

def test_sentinel_allows_five_and_varied_calls():
    from aria.agent import sentinel
    sentinel.set_enabled(True)
    sentinel.reset()
    t0 = 2000.0
    for i in range(5):
        assert sentinel.record('read_file', {'filename': 'x.py'}, now=t0 + i) is None
    sentinel.reset()
    for i in range(6):
        assert sentinel.record('tool_%d' % i, {}, now=t0 + i) is None, 'varied tools must not trip'
    sentinel.reset()
    for i in range(6):
        assert sentinel.record('read_file', {'filename': f'{i}.py'}, now=t0 + i) is None, 'varied args must not trip'

def test_sentinel_window_expiry_and_cooldown():
    from aria.agent import sentinel
    sentinel.set_enabled(True)
    sentinel.reset()
    t0 = 3000.0
    for i in range(5):
        sentinel.record('read_file', {'a': 1}, now=t0 + i)
    assert sentinel.record('read_file', {'a': 1}, now=t0 + 11) is None
    sentinel.reset()
    trip = None
    for i in range(6):
        trip = sentinel.record('read_file', {'a': 1}, now=t0 + i)
    assert trip is not None
    assert sentinel.record('read_file', {'a': 1}, now=t0 + 6) is None
    st = sentinel.status()
    assert st['tripped'] is True, st
    assert sentinel._cooldown_until > t0 + 6, 'cooldown must be armed'
    sentinel.reset()
    assert sentinel.trip_info() is None
    sentinel.set_enabled(False)
    for i in range(10):
        assert sentinel.record('read_file', {'a': 1}, now=t0 + i) is None
    sentinel.set_enabled(True)

def test_event_ring_bounded_and_meta():
    import aria.config as cfg
    cfg._EVENT_RING.clear()
    for i in range(1100):
        cfg.add_log(f'event {i}', meta={'duration_ms': i})
    assert len(cfg._EVENT_RING) == 1000, len(cfg._EVENT_RING)
    evts = cfg.recent_events(5)
    assert len(evts) == 5 and evts[0][2] == 'event 1095', evts[0]
    assert evts[-1][3] == {'duration_ms': 1099}, evts[-1][3]
    cfg.add_log('something failed badly')
    assert cfg.recent_events(1)[0][1] == 'error'
    cfg.add_log('all good', level='warn')
    assert cfg.recent_events(1)[0][1] == 'warn'
    cfg._EVENT_RING.clear()

def test_provider_stats_recorded():
    import aria.agent.providers as prov
    prov._record_call_stats('groq', 412, {'prompt_tokens': 340, 'completion_tokens': 112})
    st = prov.get_last_call_stats()
    assert st == {'provider': 'groq', 'latency_ms': 412, 'prompt_tokens': 340, 'completion_tokens': 112}, st
    prov._record_call_stats('ollama_cloud', 100, None)
    st = prov.get_last_call_stats()
    assert st['prompt_tokens'] is None and st['completion_tokens'] is None, st
_BRAIN_SYS_SNAPSHOT = None
_BRAIN_PARENT_VISION = None
_BRAIN_PARENT_HAD_VISION = False

def _load_brain_stubbed():
    """Import the REAL aria.agent.brain with heavy deps stubbed; returns module.
    Snapshots sys.modules and must be paired with _unload_brain_stubbed()."""
    global _BRAIN_SYS_SNAPSHOT, _BRAIN_PARENT_VISION, _BRAIN_PARENT_HAD_VISION
    import types
    _BRAIN_SYS_SNAPSHOT = dict(sys.modules)
    _parent = sys.modules.get('aria')
    _BRAIN_PARENT_HAD_VISION = hasattr(_parent, 'vision')
    _BRAIN_PARENT_VISION = getattr(_parent, 'vision', None)
    sys.modules.pop('aria.agent.brain', None)

    def _mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m
    _mod('aria.memory', build_prompt_memories=lambda *a, **k: '', spine_append=lambda *a, **k: None, spine_unbroken_thread=lambda: '', log_conversation=lambda *a, **k: None)
    _mod('aria.tools.dispatch', execute_tool=lambda *a, **k: ('', None), reset_turn_state=lambda: None, set_turn_context=lambda *a: None, get_last_tool_executed=lambda: None, get_loaded_toolkits=lambda: [], set_hud_hook=lambda *a: None, auto_resolve_toolkits=lambda *a: None)
    _mod('aria.speech', speak=lambda *a, **k: None)
    _mod('aria.agent.shortcuts', check_voice_shortcut=lambda *a, **k: None)
    _mod('aria.agent.providers', provider_call=lambda *a, **k: None, get_active_provider=lambda: 'none', get_last_call_stats=lambda: {}, resolve_role_model=lambda role: None, chain_all_quarantined=lambda: False)
    _mod('aria.hud', set_hud_state=lambda *a: None, draw_hud=lambda: None, set_hud_subtitle=lambda *a: None)
    import importlib
    return importlib.import_module('aria.agent.brain')

def _unload_brain_stubbed():
    """Restore sys.modules to the pre-brain-import snapshot."""
    global _BRAIN_SYS_SNAPSHOT, _BRAIN_PARENT_VISION
    if _BRAIN_SYS_SNAPSHOT is not None:
        sys.modules.clear()
        sys.modules.update(_BRAIN_SYS_SNAPSHOT)
        _parent = sys.modules.get('aria')
        if _parent is not None:
            if _BRAIN_PARENT_HAD_VISION:
                _parent.vision = _BRAIN_PARENT_VISION
            else:
                try:
                    delattr(_parent, 'vision')
                except AttributeError:
                    pass
        _BRAIN_SYS_SNAPSHOT = None
        _BRAIN_PARENT_VISION = None

def _install_vision_stub(fn):
    """Install a fake aria.vision (both sys.modules and the parent attr,
    since `from aria import vision` prefers the parent attribute)."""
    import types
    vision_stub = types.ModuleType('aria.vision')
    vision_stub._ollama_native_vision_call = fn
    sys.modules['aria.vision'] = vision_stub
    sys.modules['aria'].vision = vision_stub

def test_vision_prepass_describes_and_folds_into_prompt():
    brain = _load_brain_stubbed()
    try:
        seen = {}

        def fake_native(sys_prompt, contents):
            seen['sys'] = sys_prompt
            parts = contents[0]['parts']
            seen['has_image'] = any(('inline_data' in p for p in parts))
            return 'a red square on a table'
        _install_vision_stub(fake_native)
        out = brain._vision_prepass('what do you see?', b'fakejpeg', False)
        assert seen['has_image'] is True, 'pre-pass must send the image to native vision'
        assert 'a red square on a table' in out, out
        assert 'camera' in out and 'what do you see?' in out, out
        out2 = brain._vision_prepass('read this', b'x', True)
        assert 'screen' in out2, out2
    finally:
        _unload_brain_stubbed()

def test_vision_prepass_never_raises():
    brain = _load_brain_stubbed()
    try:

        def boom(*a, **k):
            raise RuntimeError('vision down')
        _install_vision_stub(boom)
        out = brain._vision_prepass('hello', b'x', False)
        assert out == 'hello', out
    finally:
        _unload_brain_stubbed()

def _load_classify_ops_intent():
    """Extract _classify_ops_intent from main.py via AST (main.py itself is
    too heavy to import: cv2/pygame/speech)."""
    import ast
    src = open(os.path.join(PKG, 'main.py'), encoding='utf-8').read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_classify_ops_intent':
            from typing import Optional
            ns = {'Optional': Optional}
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), ns)
            return ns['_classify_ops_intent']
    raise AssertionError('_classify_ops_intent not found in main.py')

def test_ops_intent_visual():
    fn = _load_classify_ops_intent()
    assert fn('look at the ops screen') == 'visual'
    assert fn('show me the ops overlay') == 'visual'
    assert fn('open ops') == 'visual'
    assert fn('what do you see on the ops display') == 'visual'

def test_ops_intent_code():
    fn = _load_classify_ops_intent()
    assert fn('review the ops screen') == 'code'
    assert fn('redesign the ops screen') == 'code'
    assert fn('fix the ops code') == 'code'

def test_ops_intent_none():
    fn = _load_classify_ops_intent()
    assert fn('what time is it') is None
    assert fn('look at my screen') is None
    assert fn('ops') is None

def _extract_fn(path, name, namespace=None):
    """AST-extract a single function from a source file without importing the
    module (avoids heavy deps). `namespace` supplies the globals it needs."""
    import ast
    src = open(os.path.join(PKG, path), encoding='utf-8').read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            ns = dict(namespace or {})
            exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), ns)
            return ns[name]
    raise AssertionError(f'{name} not found in {path}')

def _stub_self_healing_module():
    sh = types.ModuleType('aria.agent.self_healing')
    sh.diagnose_error = lambda source, err, ctx=None: {'diagnosis': 'stub diagnosis', 'recommended_action': 'stub fix', 'category': 'STUB', 'can_auto_heal': False}
    sys.modules['aria.agent.self_healing'] = sh

def test_selfrepair_hallucination_match():
    from typing import Optional
    fn = _extract_fn('tools/dispatch.py', '_closest_tool', {'_REGISTRY': {'get_time': 1, 'read_file': 2, 'set_timer': 3}, 'Optional': Optional})
    assert fn('get_tim') == 'get_time'
    assert fn('readfile') == 'read_file'
    assert fn('container.exec') is None
    assert fn('xyzzy_nope') is None
    assert fn('') is None

def test_selfrepair_budget_allows_then_exhausts():
    _stub_self_healing_module()
    failures = {}
    fn = _extract_fn('tools/dispatch.py', '_repair_or_exhaust', {'_TURN_FAILURES': failures, '_REPAIR_BUDGET': 2})
    args = {'x': 1}
    r1 = fn('some_tool', args, 'ValueError: bad')
    assert '[Repair attempt 1/2' in r1, r1
    assert 'Do not repeat the identical call' in r1
    r2 = fn('some_tool', args, 'ValueError: bad')
    assert '[Repair attempt 2/2' in r2, r2
    r3 = fn('some_tool', args, 'ValueError: bad')
    assert 'budget exhausted' in r3.lower(), r3
    assert 'report the failure' in r3.lower()
    r4 = fn('some_tool', {'x': 2}, 'ValueError: bad')
    assert '[Repair attempt 1/2' in r4, r4

def test_selfrepair_lesson_promotion():
    saved = []
    logged = []
    incs = [{'category': 'NETWORK_TRANSIENT', 'source': 'fetch_url', 'resolved': 0}, {'category': 'NETWORK_TRANSIENT', 'source': 'fetch_url', 'resolved': 0}, {'category': 'NETWORK_TRANSIENT', 'source': 'fetch_url', 'resolved': 0}]
    fn = _extract_fn('agent/self_healing.py', '_maybe_promote_lesson', {'incident_db_list': lambda limit: incs, 'memory_save': lambda cat, key, val: saved.append((cat, key, val)), 'add_log': lambda msg: logged.append(msg)})
    fn('NETWORK_TRANSIENT', 'fetch_url', 'diag text', 'tried retry')
    assert len(saved) == 1, saved
    cat, key, val = saved[0]
    assert cat == 'self_heal' and key == 'lesson:NETWORK_TRANSIENT:fetch_url'
    saved.clear()
    fn2 = _extract_fn('agent/self_healing.py', '_maybe_promote_lesson', {'incident_db_list': lambda limit: incs[:2], 'memory_save': lambda cat, key, val: saved.append((cat, key, val)), 'add_log': lambda msg: None})
    fn2('NETWORK_TRANSIENT', 'fetch_url', 'diag', 'tried')
    assert saved == []

def test_selfrepair_preflight():
    fn = _extract_fn('routines.py', '_preflight_steps')
    steps = [{'tool': 'get_time', 'args': {}}, {'tool': 'bogus_tool', 'args': {}}, {'tool': 'read_file', 'args': {}}]
    assert fn(steps, {'get_time', 'read_file', 'set_timer'}) == ['bogus_tool']
    assert fn(steps, {'get_time', 'read_file', 'bogus_tool'}) == []
    assert fn([], {'get_time'}) == []
    assert fn(None, None) == []

def test_dynamic_routing_classifier():
    fn = _extract_fn('agent/brain.py', 'classify_task_role', {'_CODE_VERBS': ('edit', 'write', 'fix', 'debug', 'refactor', 'rewrite', 'implement', 'patch'), '_CODE_NOUNS': ('code', 'script', 'function', 'bug', 'traceback', '.py', 'ops_screen', 'hud.py')})
    assert fn('edit her code') == 'code'
    assert fn('review the ops screen') == 'code'
    assert fn('write a python script to sort files') == 'code'
    assert fn('fix the bug in hud.py') == 'code'
    assert fn('what time is it') == 'default'
    assert fn('remember that my favorite color is blue') == 'default'
    assert fn('what do I look like') == 'default'
    assert fn('write a shopping list') == 'default'
    assert fn('') == 'default'
    assert fn(None) == 'default'

def test_dynamic_routing_role_cooldown():
    from typing import Optional
    import time as _time
    logged = []
    cooldown = {}
    ns = {'_ROLE_MODEL_COOLDOWN': cooldown, 'OLLAMA_VISION_MODEL': 'gemma4:31b-cloud', 'OLLAMA_CODE_MODEL': 'qwen3-coder:480b-cloud', 'time': _time, 'Optional': Optional, 'add_log': lambda m: logged.append(m)}
    fn = _extract_fn('agent/providers.py', 'resolve_role_model', ns)
    assert fn('code') == 'qwen3-coder:480b-cloud'
    assert fn('vision') == 'gemma4:31b-cloud'
    assert fn('default') is None
    assert fn('bogus') is None
    cooldown['qwen3-coder:480b-cloud'] = _time.time() + 900
    assert fn('code') is None
    assert fn('vision') == 'gemma4:31b-cloud'
    cooldown['qwen3-coder:480b-cloud'] = _time.time() - 1
    assert fn('code') == 'qwen3-coder:480b-cloud'

def test_dynamic_routing_all_quarantined():
    true_fn = _extract_fn('agent/providers.py', 'chain_all_quarantined', {'_build_chain': lambda: [types.SimpleNamespace(api_key='k1'), types.SimpleNamespace(api_key='k2')], 'key_is_quarantined': lambda k: True})
    assert true_fn() is True
    false_fn = _extract_fn('agent/providers.py', 'chain_all_quarantined', {'_build_chain': lambda: [types.SimpleNamespace(api_key='k1'), types.SimpleNamespace(api_key='k2')], 'key_is_quarantined': lambda k: k == 'k1'})
    assert false_fn() is False
    empty_fn = _extract_fn('agent/providers.py', 'chain_all_quarantined', {'_build_chain': lambda: [], 'key_is_quarantined': lambda k: True})
    assert empty_fn() is False
for name, fn in sorted([(k, v) for k, v in list(globals().items()) if k.startswith('t_')]):
    check(name, fn)
