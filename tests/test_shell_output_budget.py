"""Shell output limits must apply DURING collection, not after (pass 26 B4#8).

_execute used proc.communicate() — collecting complete
stdout+stderr in memory — and only afterwards cut the rendered
body to OUTPUT_LIMIT. A process streaming output for the full
timeout window grew memory unboundedly before truncation.
_execute now reads incrementally into bounded buffers (cap per
stream, still draining past the budget so the child never
deadlocks on a full pipe) and marks truncation in the result.
"""

import resource
import sys
import time
import types
from pathlib import Path

pkg = types.ModuleType("aria")
pkg.__path__ = [str(Path(__file__).parents[1] / "aria")]
sys.modules.setdefault("aria", pkg)

tools_pkg = types.ModuleType("aria.tools")
tools_pkg.__path__ = [str(Path(__file__).parents[1] / "aria" / "tools")]
sys.modules.setdefault("aria.tools", tools_pkg)

import importlib  # noqa: E402
shelltool = importlib.import_module("aria.tools.shelltool")


def _py(code):
    return f'"{sys.executable}" -c "{code}"'


def _maxrss_kb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def test_streaming_output_memory_is_bounded(tmp_path):
    # ~60 MB of output: unbounded collection would raise this
    # process's high-water mark by ~60 MB; bounded readers keep
    # it to a few MB. (The child generates the flood; only the
    # parent's RSS is measured.)
    code = ("import sys; "
            "[print('y' * 100, flush=True) for _ in range(600000)]")
    before = _maxrss_kb()
    started = time.monotonic()
    out = shelltool._execute(_py(code), str(tmp_path), 60)
    elapsed = time.monotonic() - started
    growth_kb = _maxrss_kb() - before
    assert out.startswith("Exit code: 0"), out[:200]
    assert "[output truncated]" in out
    assert len(out) <= shelltool.OUTPUT_LIMIT + 1000, len(out)
    assert elapsed < 30, elapsed
    assert growth_kb < 20 * 1024, f"RSS grew by {growth_kb} KB"
    print(f"ok streaming_bounded growth={growth_kb}KB elapsed={elapsed:.1f}s")


def test_normal_command_unaffected(tmp_path):
    out = shelltool._execute(_py("print('hello')"), str(tmp_path), 30)
    assert out.startswith("Exit code: 0")
    assert "hello" in out
    assert "[output truncated]" not in out
    print("ok normal_command")


def test_streaming_timeout_still_kills(tmp_path):
    code = ("import sys, time; "
            "[print(i, flush=True) or time.sleep(0.01) "
            "for i in range(100000)]")
    out = shelltool._execute(_py(code), str(tmp_path), 2)
    # Pass 28 #27: a timeout is UNVERIFIED (its effects are
    # unknown), not FAILED.
    assert "status=unverified" in out and "timed out after 2s" in out
    print("ok streaming_timeout")
