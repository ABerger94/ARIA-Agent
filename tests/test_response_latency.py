"""Exercise the actual entry-point routing without importing microphone drivers."""
import ast
from pathlib import Path
import sys
import time
import types
from typing import Optional

import pytest


@pytest.fixture
def route(monkeypatch):
    root = Path(__file__).parents[1] / 'aria'
    package = types.ModuleType('aria'); package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, 'aria', package)
    logs, spoken, model_calls = [], [], []
    state = {'attached': None, 'locked': False}
    def module(name, **attrs):
        item = types.ModuleType(name)
        for key, value in attrs.items(): setattr(item, key, value)
        monkeypatch.setitem(sys.modules, name, item)
    module('aria.applock', is_locked=lambda: state['locked'])
    module('aria.bookmark', try_save=lambda *a: None, is_resume=lambda *a: False)
    module('aria.context_attach', consume_block=lambda: state['attached'])
    module('aria.memory', log_conversation=lambda who, text: logs.append((who, text)))
    brain = types.SimpleNamespace(current_history=lambda: [], LAST_ACTIVITY=0)
    def model(text, **kwargs):
        model_calls.append((text, kwargs))
        return 'Model path'
    agent = types.SimpleNamespace(brain=brain, run_agent=model,
                                  proactive=types.SimpleNamespace(mood_note_interaction=lambda: None))
    speech = types.SimpleNamespace(speak=spoken.append, STOP_WORDS=[])
    vision = types.SimpleNamespace(capture_webcam=lambda: b'image',
                                   publish_vision_preview=lambda *a: None)
    source = ast.parse((root / 'main.py').read_text(encoding='utf-8'))
    function = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'handle_action')
    namespace = {'Optional': Optional, 'agent': agent, 'speech': speech,
                 'vision': vision, 'time': time, 'add_log': lambda *a: None}
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'aria/main.py', 'exec'), namespace)
    return namespace['handle_action'], state, logs, spoken, model_calls


@pytest.mark.parametrize('greeting', ['hello', 'Hi ARIA!', 'hey there'])
def test_greeting_bypasses_the_model_and_its_foreground_queue(route, greeting):
    handle, state, logs, spoken, calls = route
    assert handle('voice', greeting) == "I'm here. What can I help with?"
    assert calls == [] and spoken == ["I'm here. What can I help with?"]
    assert logs[0] == ('User', greeting)
    assert logs[1][0] == 'A.R.I.A.'


@pytest.mark.parametrize('prompt', ['hello, check my calendar', 'hi, show my unread email'])
def test_greeting_with_an_actual_request_still_reaches_the_model(route, prompt):
    handle, state, logs, spoken, calls = route
    assert handle('voice', prompt) == 'Model path'
    assert calls[0][0] == prompt


def test_greeting_does_not_discard_staged_context(route):
    handle, state, logs, spoken, calls = route
    state['attached'] = '[Clipboard attachment]\nExplain this document.'
    assert handle('voice', 'hello') == 'Model path'
    assert '[Clipboard attachment]' in calls[0][0]
    assert spoken == []


def test_greeting_does_not_discard_an_explicit_camera_request(route):
    handle, state, logs, spoken, calls = route
    assert handle('camera', 'hello') == 'Model path'
    assert calls[0][1]['image_bytes'] == b'image'


def test_local_greeting_preserves_the_app_lock(route):
    handle, state, logs, spoken, calls = route
    state['locked'] = True
    assert handle('voice', 'hello') == 'ARIA is locked.'
    assert logs == spoken == calls == []
