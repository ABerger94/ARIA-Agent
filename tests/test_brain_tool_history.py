"""Tool exchanges survive into conversation history.

run_agent used to keep only the final reply text in
CONVERSATION_HISTORY, so the next turn could not see what a tool
returned (a file listing, a search result) unless the reply
repeated it. The turn's functionCall / functionResponse entries
are now kept — capped — between the user message and the reply,
and compaction never leaves a tool entry orphaned at the front.

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import types
import tempfile

os.environ['HOME'] = tempfile.mkdtemp(prefix='aria_brain_hist_')
PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
tools_pkg = types.ModuleType('aria.tools')
tools_pkg.__path__ = [os.path.join(PKG, 'tools')]
sys.modules['aria.tools'] = tools_pkg
_ps = types.ModuleType('psutil')
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules['psutil'] = _ps
for _name in ('aria.vision', 'aria.hardware', 'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)
_sp = types.ModuleType('aria.spotify')
_sp.tool_media_key = lambda *a, **k: ''
_sp.tool_spotify = lambda *a, **k: ''
sys.modules['aria.spotify'] = _sp

from aria.agent import brain  # noqa: E402


def _user(text):
    return {"role": "user", "parts": [{"text": text}]}


def _model(text):
    return {"role": "model", "parts": [{"text": text}]}


def _call(name, args=None):
    return {"role": "model", "parts": [
        {"functionCall": {"name": name, "args": args or {}}}]}


def _resp(name, output):
    return {"role": "user", "parts": [
        {"functionResponse": {"name": name,
                              "response": {"output": output}}}]}


def test_turn_exchange_is_extracted_and_capped():
    user = _user("User: list my files")
    contents = [_user("You are online."), _model("ready"), user,
                {"role": "model", "parts": [
                    {"text": "thinking", "thought": True},
                    {"functionCall": {"name": "find_file",
                                      "args": {"q": "*.py"}}}]},
                _resp("find_file", "x" * 50000),
                _model("Here are your files.")]
    out = brain._turn_tool_exchange(contents, user)
    assert [e["role"] for e in out] == ["model", "user"], out
    # thought text is dropped, the call is kept as-is
    assert out[0]["parts"] == [{"functionCall": {
        "name": "find_file", "args": {"q": "*.py"}}}]
    output = out[1]["parts"][0]["functionResponse"]["response"]["output"]
    assert len(output) < 2000 and "truncated" in output, len(output)
    # the original contents are not mutated
    assert len(contents[4]["parts"][0]["functionResponse"]
               ["response"]["output"]) == 50000


def test_turn_exchange_shares_one_budget():
    user = _user("User: go")
    contents = [user]
    for i in range(10):
        contents += [_call("t"), _resp("t", "y" * 5000)]
    out = brain._turn_tool_exchange(contents, user)
    total = sum(len(e["parts"][0]["functionResponse"]["response"]["output"])
                for e in out if e["role"] == "user")
    assert total <= brain._HISTORY_TOOL_TURN_BUDGET + 10 * 40, total


def test_trailing_call_without_response_is_dropped():
    user = _user("User: go")
    contents = [user, _call("a"), _resp("a", "ok"), _call("b")]
    out = brain._turn_tool_exchange(contents, user)
    assert [e["role"] for e in out] == ["model", "user"], out


def test_no_tools_no_entries():
    user = _user("User: hi")
    assert brain._turn_tool_exchange([user, _model("hello")], user) == []
    assert brain._turn_tool_exchange([_model("x")], user) == []


def test_compaction_cuts_at_a_user_turn():
    hist = []
    for i in range(20):
        hist += [_user(f"User: q{i}"), _call("t"), _resp("t", "r"),
                 _model(f"a{i}")]
    brain.CONVERSATION_HISTORY = hist
    brain._compact_conversation_history()
    h = brain.CONVERSATION_HISTORY
    assert brain._is_turn_start(h[0]), h[0]
    # tool entries do not count toward HISTORY_TURNS
    plain = [e for e in h if not brain._is_tool_entry(e)]
    assert len(plain) <= brain.HISTORY_TURNS
    assert any(brain._is_tool_entry(e) for e in h)
    assert h[-1] == _model("a19")


def test_char_cap_never_orphans_a_tool_response():
    hist = [_user("User: old"), _model("old answer"),
            _user("User: big"), _call("t"), _resp("t", "z" * 30000),
            _model("done")]
    brain.CONVERSATION_HISTORY = hist
    brain._compact_conversation_history(max_chars=24000)
    h = brain.CONVERSATION_HISTORY
    assert not h or brain._is_turn_start(h[0]), h[:1]
    assert not any(e.get("parts", [{}])[0].get("functionResponse")
                   for e in h[:1])
