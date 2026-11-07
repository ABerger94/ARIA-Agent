"""Pass 28 #3: user-tool verification and execution must read the
source bytes ONCE.

The handler hashed the file (read #1), released it, then
_load_module_from_path reopened it (read #2) and compiled those
bytes. A writer swapping the file between the two reads executed
replacement code under the approved digest — and a third read
after execution cached a digest that could describe yet different
bytes. The fix reads the bytes once under the import lock,
hashes those exact bytes, and compiles those exact bytes.
"""
import builtins
import hashlib
import importlib
import io
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_usertools_single_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, 'ARIA')
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
for _name in ('aria.scheduler', 'aria.vision', 'aria.hardware',
              'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)

outcome = importlib.import_module('aria.outcome')
usertools = importlib.import_module('aria.tools.usertools')


def _tool_src(name, run_body):
    return ('\n'
            f'TOOL_NAME = "{name}"\n'
            f'TOOL_DESCRIPTION = "Probe tool {name}"\n'
            'TOOL_PARAMETERS = {"type": "OBJECT", "properties": {}, '
            '"required": []}\n'
            'def run(args):\n'
            f'    {run_body}\n')


class _SwappingOpen:
    """open() that returns `first` bytes on the first binary read
    of the target path and `later` bytes on every read after —
    a deterministic stand-in for a writer swapping the file
    between a digest check and an import."""

    def __init__(self, target, first, later):
        self.target = str(target)
        self.first = first
        self.later = later
        self.opens = 0
        self._real = builtins.open

    def __call__(self, file, mode="r", *args, **kwargs):
        if str(file) == self.target and "b" in str(mode):
            self.opens += 1
            data = self.first if self.opens == 1 else self.later
            return io.BytesIO(data)
        return self._real(file, mode, *args, **kwargs)


def _load(tmp_path, name, run_body):
    scan = tmp_path / "user_tools"
    scan.mkdir(exist_ok=True)
    (scan / f"{name}.py").write_text(_tool_src(name, run_body))
    usertools.load_user_tools_into_registry(str(scan))
    return scan, usertools._USER_TOOLS[name][0]


def test_swap_between_check_and_import_never_executes_replacement(
        tmp_path, monkeypatch):
    scan, handler = _load(tmp_path, "single_read_a", 'return "V1"')
    frozen = usertools.freeze_user_tool_args("single_read_a", {})
    target = scan / "single_read_a.py"
    v1 = target.read_bytes()
    v2 = _tool_src("single_read_a", 'return "V2"').encode()
    swap = _SwappingOpen(target, v1, v2)
    monkeypatch.setattr(builtins, "open", swap)
    out = handler(frozen)
    assert out == "V1", out
    assert "V2" not in str(out), out
    assert swap.opens == 1, f"expected a single read, got {swap.opens}"


def test_cached_digest_describes_executed_bytes(tmp_path, monkeypatch):
    # The cached digest must be the digest of the executed (V1)
    # bytes, not of a later re-read: after the first execution
    # (which the swap makes look like V1-then-V2), an approval
    # for the V1 digest must run the cached module with NO
    # re-import — observable via the top-level import marker,
    # which gains one line per import. The old code cached the
    # digest of a post-execution re-read (V2), so the V1 approval
    # forced a second import.
    marker = tmp_path / "imports.txt"

    def _src(body):
        return ('import pathlib\n'
                f"pathlib.Path({str(marker)!r}).open("
                "'a', encoding='utf-8').write('x\\n')\n"
                'TOOL_NAME = "single_read_b"\n'
                'TOOL_DESCRIPTION = "Probe tool single_read_b"\n'
                'TOOL_PARAMETERS = {"type": "OBJECT", "properties": {}, '
                '"required": []}\n'
                'def run(args):\n'
                f'    {body}\n')

    scan = tmp_path / "user_tools"
    scan.mkdir(exist_ok=True)
    target = scan / "single_read_b.py"
    target.write_text(_src('return "V1"'))
    usertools.load_user_tools_into_registry(str(scan))
    handler = usertools._USER_TOOLS["single_read_b"][0]
    v1 = target.read_bytes()
    v2 = _src('return "V2"').encode()
    swap = _SwappingOpen(target, v1, v2)
    monkeypatch.setattr(builtins, "open", swap)
    assert handler({}) == "V1"
    assert swap.opens == 1
    assert marker.read_text(encoding="utf-8") == "x\n"
    # Restore the real open: the file on disk is still V1.
    builtins.open = swap._real
    approved_v1 = {"_source_digest": hashlib.sha256(v1).hexdigest()}
    assert handler(approved_v1) == "V1"
    assert marker.read_text(encoding="utf-8") == "x\n", \
        "V1 approval re-imported the module instead of running the cache"
