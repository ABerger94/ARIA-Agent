"""Real request/response boundaries for connection recovery; never use live keys."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import urllib.error

import pytest

os.environ['ARIA_DATA_ROOT'] = tempfile.mkdtemp(prefix='aria-provider-recovery-')
root = Path(__file__).parents[1] / 'aria'
pkg = types.ModuleType('aria'); pkg.__path__ = [str(root)]
agent = types.ModuleType('aria.agent'); agent.__path__ = [str(root / 'agent')]
sys.modules['aria'] = pkg
sys.modules['aria.agent'] = agent
from aria import config, runtime
from aria.agent import providers as pv


KEY = 'test-only-provider-key'
OK = {'choices': [{'message': {'content': 'Done.'}}]}
SIGNATURE = {'google': {'thought_signature': 'opaque/base64+signature=='}}
REAL_RESERVE = pv.usage.reserve
REAL_FINISH = pv.usage.finish


class Response:
    def __init__(self, value=None, lines=()):
        self.value, self.lines = value, lines
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.value).encode()
    def __iter__(self): return iter(self.lines)


def provider(name='gemini', key=KEY):
    return pv.OpenAICompatProvider(name, 'https://example.invalid/v1', key, 'test-model')


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    config._KEY_QUARANTINE_UNTIL.clear()
    config._KEY_QUARANTINE_CODE.clear()
    pv.clear_provider_cooldown()
    pv.clear_model_cooldown()
    pv.clear_role_model_cooldown()
    pv._PROVIDER_HEALTH.clear()
    pv._RECENT_FOREGROUND_PROVIDER.clear()
    monkeypatch.setattr(pv.usage, 'reserve', lambda *a, **k: 'test-reservation')
    monkeypatch.setattr(pv.usage, 'finish', lambda *a, **k: None)
    monkeypatch.setattr(pv.usage, 'order', lambda chain: chain)
    monkeypatch.setattr(pv.usage, 'max_output_tokens', lambda: 2048)
    with runtime.execution():
        yield


def test_gemini_tool_result_request_retains_signature(monkeypatch):
    requests = []
    def serve(req, **kwargs):
        body = json.loads(req.data)
        requests.append(body)
        if len(requests) == 1:
            return Response({'choices': [{'message': {'tool_calls': [{
                'id': 'call_1', 'type': 'function', 'extra_content': SIGNATURE,
                'function': {'name': 'get_time', 'arguments': '{"zone":"UTC"}'},
            }]}}]})
        assert body['messages'][1]['tool_calls'][0]['extra_content'] == SIGNATURE
        assert body['messages'][2]['tool_call_id'] == 'call_1'
        return Response(OK)
    monkeypatch.setattr(pv.urllib.request, 'urlopen', serve)
    p = provider()
    contents = [{'role': 'user', 'parts': [{'text': 'What time is it?'}]}]
    first = p.call('', contents)
    contents += [
        {'role': 'model', 'parts': first['candidates'][0]['content']['parts']},
        {'role': 'user', 'parts': [{'functionResponse': {
            'name': 'get_time', 'response': {'output': '12:00 UTC'}}}]},
    ]
    result = p.call('', contents)
    assert result['candidates'][0]['content']['parts'] == [{'text': 'Done.'}]
    assert len(requests) == 2


def test_streamed_signature_arriving_after_arguments_survives(monkeypatch):
    deltas = [
        {'tool_calls': [{'index': 0, 'id': 'call_1',
                         'function': {'name': 'get_time', 'arguments': '{"zone":'}}]},
        {'tool_calls': [{'index': 0, 'function': {'arguments': '"UTC"}'}}]},
        {'tool_calls': [{'index': 0, 'extra_content': SIGNATURE}]},
    ]
    lines = [b'data: ' + json.dumps({'choices': [{'delta': delta}]}).encode() + b'\n'
             for delta in deltas] + [b'data: [DONE]\n']
    monkeypatch.setattr(pv.urllib.request, 'urlopen', lambda *a, **k: Response(lines=lines))
    chunks = []
    data = provider().call('', [], on_text_chunk=chunks.append)
    parts = data['candidates'][0]['content']['parts']
    messages = pv.gemini_contents_to_oai_messages('', [{'role': 'model', 'parts': parts}])
    call = messages[0]['tool_calls'][0]
    assert call['extra_content'] == SIGNATURE
    assert json.loads(call['function']['arguments']) == {'zone': 'UTC'}
    assert chunks == [None]


def test_google_extensions_do_not_break_another_provider(monkeypatch):
    parts = pv.oai_response_to_parts({'choices': [{'message': {
        'tool_calls': [{'id': 'call_1', 'function': {'name': 'get_time', 'arguments': '{}'},
                        'extra_content': SIGNATURE}]}}]})
    def serve(req, **kwargs):
        call = json.loads(req.data)['messages'][0]['tool_calls'][0]
        assert 'extra_content' not in call
        return Response(OK)
    monkeypatch.setattr(pv.urllib.request, 'urlopen', serve)
    assert provider('groq').call('', [{'role': 'model', 'parts': parts}])
    assert parts[0]['functionCall']['_oai_extra_content'] == SIGNATURE


def test_network_blip_does_not_quarantine_key_or_block_next_turn(monkeypatch):
    p = provider()
    monkeypatch.setattr(pv, '_build_chain', lambda: [p])
    attempts = []
    def serve(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise urllib.error.URLError('temporary disconnect')
        return Response(OK)
    monkeypatch.setattr(pv.urllib.request, 'urlopen', serve)
    assert pv.provider_call('', []) is None
    assert 'network connection failed' in pv.provider_failure_message()
    assert not config.key_is_quarantined(KEY)
    assert pv.provider_call('', [])
    assert len(attempts) == 2
    assert pv.provider_health()['gemini']['status'] == 'ok'


def test_budget_rejection_is_reported_without_network_or_cooldown(monkeypatch):
    p = provider()
    monkeypatch.setattr(pv, '_build_chain', lambda: [p])
    def refuse(*args, **kwargs):
        raise pv.usage.BudgetExceeded('No configured price for this model; call refused')
    monkeypatch.setattr(pv.usage, 'reserve', refuse)
    monkeypatch.setattr(pv.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('Budgeted request reached network'))
    assert pv.provider_call('', []) is None
    assert pv.provider_failure_message() == 'No configured price for this model; call refused'
    assert not pv._provider_in_cooldown('gemini')
    assert not pv._model_in_cooldown('gemini', p.model)


@pytest.mark.parametrize('override', [None, 'role-model'])
def test_cancellation_never_falls_back_to_another_model(monkeypatch, override):
    p = provider('ollama_cloud')
    attempts = []
    def cancel(*args, **kwargs):
        attempts.append(1)
        raise runtime.Cancelled('Cancelled')
    monkeypatch.setattr(p, 'call', cancel)
    with pytest.raises(runtime.Cancelled):
        pv._attempt_provider_call(p, '', [], None, None, override)
    assert attempts == [1]
    assert p.model == 'test-model'


@pytest.mark.parametrize('status,kind', [(400, 'request'), (401, 'auth'), (402, 'billing'),
                                        (404, 'model'), (410, 'model'), (429, 'rate_limit'), (503, 'server')])
def test_failure_report_distinguishes_http_causes_without_secrets(monkeypatch, status, kind):
    p = provider()
    monkeypatch.setattr(pv, '_build_chain', lambda: [p])
    def refuse(req, **kwargs):
        raise urllib.error.HTTPError(req.full_url, status, 'failed', {},
                                     io.BytesIO(('{"error":"' + KEY + '"}').encode()))
    monkeypatch.setattr(pv.urllib.request, 'urlopen', refuse)
    assert pv.provider_call('', []) is None
    assert runtime.current().provider_failures[0]['kind'] == kind
    assert f'HTTP {status}' in pv.provider_failure_message()
    assert KEY not in pv.provider_failure_message()
    assert KEY not in pv.connection_report()
    assert KEY not in repr(pv.provider_health())
    if status in (400, 402, 404, 410, 503):
        assert not config.key_is_quarantined(KEY)


def test_missing_keys_are_not_reported_as_rate_limits(monkeypatch):
    monkeypatch.setattr(pv, '_build_chain', lambda: [provider(key='INSERT'), provider('groq', key='')])
    monkeypatch.setattr(pv.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('Missing key reached network'))
    assert pv.provider_call('', []) is None
    assert 'No model provider is configured' in pv.provider_failure_message()
    assert 'rate limit' not in pv.provider_failure_message()


@pytest.mark.parametrize('message', [{}, {'content': ''}, {'content': '   '},
                                     {'extra_content': SIGNATURE}])
def test_empty_provider_response_falls_through_to_a_working_provider(monkeypatch, message):
    first, second = provider('gemini'), provider('groq')
    monkeypatch.setattr(pv, '_build_chain', lambda: [first, second])
    requests = []
    def serve(req, **kwargs):
        requests.append(req)
        return Response({'choices': [{'message': message}]} if len(requests) == 1 else OK)
    monkeypatch.setattr(pv.urllib.request, 'urlopen', serve)
    result = pv.provider_call('', [])
    assert result['candidates'][0]['content']['parts'] == [{'text': 'Done.'}]
    assert len(requests) == 2


def test_metadata_only_stream_is_not_a_successful_response(monkeypatch):
    chunks = []
    lines = [b'data: ' + json.dumps({'choices': [{'delta': {'extra_content': SIGNATURE}}]}).encode(),
             b'data: [DONE]']
    monkeypatch.setattr(pv.urllib.request, 'urlopen', lambda *a, **k: Response(lines=lines))
    assert provider().call('', [], on_text_chunk=chunks.append) is None


def test_done_stream_marker_stops_reading_the_connection():
    def lines():
        yield b'data: {"choices":[{"delta":{"content":"Done."}}]}'
        yield b'data: [DONE]'
        raise AssertionError('Read beyond the end-of-stream marker')
    assert provider()._parse_sse_stream(Response(lines=lines()), None) == [{'text': 'Done.'}]


def test_hello_reaches_the_provider_with_legacy_limits_and_no_prices(monkeypatch, tmp_path):
    from aria import memory
    monkeypatch.setattr(memory, 'db_path', lambda: str(tmp_path / 'usage.db'))
    monkeypatch.setattr(pv.usage, 'settings', lambda: {
        'daily_budget_usd': 0, 'task_budget_usd': 0, 'model_rates': {}})
    monkeypatch.setattr(pv.usage, 'reserve', REAL_RESERVE)
    monkeypatch.setattr(pv.usage, 'finish', REAL_FINISH)
    monkeypatch.setattr(pv, '_build_chain', lambda: [provider('mistral')])
    requests = []
    def serve(req, **kwargs):
        requests.append(json.loads(req.data))
        return Response(OK)
    monkeypatch.setattr(pv.urllib.request, 'urlopen', serve)
    with runtime.execution('foreground'):
        result = pv.provider_call('', [{'role': 'user', 'parts': [{'text': 'hello'}]}])
        assert result['candidates'][0]['content']['parts'] == [{'text': 'Done.'}]
        assert runtime.current().budget_error == ''
    assert requests[0]['messages'][0]['content'] == 'hello'
    assert pv.usage.recent()[0]['status'] == 'ok'
    assert 'Project spending limits: OFF' in pv.connection_report()


def test_background_failure_cannot_replace_foreground_diagnostics(monkeypatch):
    outer = provider()
    outer.last_error = 'HTTP 401: private response'
    pv._record_provider_failure(outer)
    first = pv.provider_failure_message()
    with runtime.execution('delegated'):
        inner = provider('groq')
        inner.last_error = 'HTTP 503: service unavailable'
        pv._record_provider_failure(inner)
        assert 'groq' in pv.provider_failure_message()
    assert pv.provider_failure_message() == first


def test_interactive_stall_timeout_is_shorter_than_background(monkeypatch):
    monkeypatch.setattr(config, 'get_setting', lambda key, default=None: default)
    waits = []
    def serve(req, timeout):
        waits.append(timeout)
        return Response(OK)
    monkeypatch.setattr(pv.urllib.request, 'urlopen', serve)
    with runtime.execution('foreground'):
        assert provider().call('', [])
    with runtime.execution('delegated'):
        assert provider().call('', [])
    assert waits == [8, 30]


def test_working_fallback_gets_the_next_interactive_turn(monkeypatch):
    a, b = provider(), provider('groq')
    a.base_url, b.base_url = 'https://slow.invalid/v1', 'https://ready.invalid/v1'
    monkeypatch.setattr(pv, '_build_chain', lambda: [a, b])
    monkeypatch.setattr(pv.usage, 'settings', lambda: {})
    calls = []
    def serve(req, **kwargs):
        calls.append(req.full_url)
        if 'slow.invalid' in req.full_url:
            raise urllib.error.URLError('temporary disconnect')
        return Response(OK)
    monkeypatch.setattr(pv.urllib.request, 'urlopen', serve)
    with runtime.execution('foreground'):
        assert pv.provider_call('', [])
    with runtime.execution('foreground'):
        assert pv.provider_call('', [])
    assert len(calls) == 3
    assert ['slow.invalid' in url for url in calls] == [True, False, False]
    assert 'last attempt' in pv.connection_report()


def test_explicit_project_provider_preference_remains_authoritative(monkeypatch):
    a, b = provider(), provider('groq')
    monkeypatch.setattr(pv, '_build_chain', lambda: [a, b])
    monkeypatch.setattr(pv.usage, 'settings', lambda: {'provider_order': ['gemini', 'groq']})
    monkeypatch.setattr(pv.urllib.request, 'urlopen', lambda *a, **k: Response(OK))
    import time
    with runtime.execution('foreground') as ctx:
        pv._RECENT_FOREGROUND_PROVIDER[ctx.project] = ('groq', time.monotonic())
        assert pv.provider_call('', [])
        assert pv.get_active_provider() == 'gemini'


def test_unreachable_gemini_host_is_not_retried_for_each_pooled_key(monkeypatch):
    a, b, c = provider(), provider('gemini_2'), provider('groq')
    a.base_url = b.base_url = 'https://unreachable.invalid/v1'
    c.base_url = 'https://ready.invalid/v1'
    monkeypatch.setattr(pv, '_build_chain', lambda: [a, b, c])
    calls = []
    def serve(req, **kwargs):
        calls.append(req.full_url)
        if 'unreachable.invalid' in req.full_url:
            raise urllib.error.URLError('host unavailable')
        return Response(OK)
    monkeypatch.setattr(pv.urllib.request, 'urlopen', serve)
    assert pv.provider_call('', [])
    assert len(calls) == 2


def test_heartbeat_only_stream_cannot_keep_interactive_call_waiting(monkeypatch):
    clock = iter([0, 3, 9])
    monkeypatch.setattr(pv.time, 'monotonic', lambda: next(clock))
    chunks = []
    with pytest.raises(TimeoutError, match='No model output'):
        provider()._parse_sse_stream(Response(lines=[b': heartbeat\n'] * 3),
                                     chunks.append, first_output_deadline=8)
    assert chunks == [None]


def test_stream_that_has_started_output_can_finish_normally(monkeypatch):
    clock = [0]
    monkeypatch.setattr(pv.time, 'monotonic', lambda: clock[0])
    chunks = []
    lines = [b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
             b'data: {"choices":[{"delta":{"content":" there."}}]}\n',
             b'data: [DONE]\n']
    def stream():
        yield lines[0]
        clock[0] = 100
        yield from lines[1:]
    parts = provider()._parse_sse_stream(Response(lines=stream()), chunks.append,
                                         first_output_deadline=8)
    assert parts == [{'text': 'Hello there.'}]
    assert chunks == ['Hello', ' there.', None]
