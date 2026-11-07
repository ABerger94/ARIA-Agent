"""Pass 27 #20: Live must bind the project at turn onset, and
must stop when the project policy changes.

Before: run_session entered its per-turn execution context
BEFORE session.receive() waited — so the project bound was the
one selected when the pass started waiting, and a project switch
during the wait meant the turn's tools executed against the old
project. And _start_live_if_configured checked
usage.project_budget_enabled only once at startup, so enabling
budgets mid-session left Live running unbudgeted.

Fix contract pinned here:
- the turn's project is captured from the live user selection
  at the first user input of the turn, and the turn's tool calls
  execute under it (a project switch during the wait binds the
  NEW project; no switch keeps the old one; a tool call with no
  user input yet keeps the pass-entry project);
- policy_check is re-checked at every turn boundary; True ends
  the session cleanly (run_session returns "policy") before any
  further receive, so the manager re-arms the pipeline;
- LiveManager stores and forwards the check; main.py passes
  usage.project_budget_enabled.
"""
import asyncio
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import speech_recognition  # noqa: F401
except ImportError:
    sys.modules.setdefault(
        'speech_recognition', types.ModuleType('speech_recognition'))

_TMPHOME = tempfile.mkdtemp(prefix='aria_live_proj20_')
os.environ['HOME'] = _TMPHOME

from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, 'ARIA')
from aria import live as live_mod
from aria import workspaces as ws_mod

_MANIFEST = os.path.join(_TMPHOME, 'ARIA', 'workspaces.json')


def _write_manifest(active):
    os.makedirs(os.path.dirname(_MANIFEST), exist_ok=True)
    with open(_MANIFEST, 'w', encoding='utf-8') as f:
        json.dump({'active': active, 'projects': {
            'default': {'id': 'default', 'name': 'Personal',
                        'settings': {}},
            'other': {'id': 'other', 'name': 'Other',
                      'settings': {}}}}, f)


def _resp(**kw):
    return types.SimpleNamespace(
        server_content=kw.get('sc'), tool_call=kw.get('tc'))


class _FakeSession:
    """One turn: user input, then a tool call. Optionally switches
    the selected project just before the user input arrives (the
    user switching projects while Live waits for the turn)."""

    def __init__(self, *, switch_to=None):
        self._switch_to = switch_to
        self._passes = 0
        self.entered = False
        self.sent = []

    async def receive(self):
        self.entered = True
        if self._passes == 0:
            self._passes += 1
            if self._switch_to is not None:
                ws_mod.select(self._switch_to)
            yield _resp(sc=types.SimpleNamespace(
                model_turn=None,
                input_transcription=types.SimpleNamespace(
                    text="do the thing"),
                output_transcription=None,
                turn_complete=False))
            fc = types.SimpleNamespace(
                name="do_thing", id="fc1", args={})
            yield _resp(tc=types.SimpleNamespace(function_calls=[fc]))
        return

    async def send_tool_response(self, function_responses):
        self.sent.append(function_responses)


def _run_turn(switch_to=None, policy_check=None):
    _write_manifest('default')
    seen = []

    def tool_runner(name, args):
        seen.append(ws_mod.current())
        return "done"

    async def main():
        session = _FakeSession(switch_to=switch_to)
        result = await live_mod.run_session(
            session, tool_runner=tool_runner,
            policy_check=policy_check)
        return result, session

    result, session = asyncio.run(main())
    return result, session, seen


def test_tool_call_binds_project_at_turn_onset():
    result, _session, seen = _run_turn(switch_to='other')
    assert result is None
    assert seen == ['other'], seen


def test_tool_call_keeps_pass_entry_project_without_switch():
    result, _session, seen = _run_turn()
    assert result is None
    assert seen == ['default'], seen


def test_policy_check_ends_session_before_first_turn():
    result, session, seen = _run_turn(policy_check=lambda: True)
    assert result == "policy"
    assert not session.entered, "receive() ran despite the policy stop"
    assert seen == []


def test_policy_check_false_runs_normally():
    result, _session, seen = _run_turn(policy_check=lambda: False)
    assert result is None
    assert seen == ['default'], seen


def test_manager_stores_policy_check():
    check = lambda: False
    mgr = live_mod.LiveManager(api_key="x", policy_check=check)
    assert mgr._policy_check is check
