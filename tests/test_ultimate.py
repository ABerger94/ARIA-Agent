"""ARIA ULTIMATE — aggregated test runner.

Runs the pytest-style suites (test_ultimate_a/b/c/d/e) as subprocesses for
isolation (each stubs sys.modules differently) and aggregates pass/fail.
Each suite is independently runnable under pytest as well.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ["test_ultimate_a.py", "test_ultimate_b.py", "test_ultimate_c.py",
          "test_ultimate_d.py", "test_ultimate_e.py"]

# Inline runner lives in _run_suite.py (importable, debuggable).
RUNNER = os.path.join(HERE, "_run_suite.py")

total_pass, total_fail = 0, 0
for suite in SUITES:
    path = os.path.join(HERE, suite)
    print(f"=== {suite} ===")
    try:
        proc = subprocess.run(
            [sys.executable, RUNNER, path],
            capture_output=True, text=True, timeout=300,
            cwd=os.path.dirname(HERE),
        )
    except Exception as e:
        print(f"ERROR running {suite}: {e}")
        total_fail += 1
        continue
    out = proc.stdout + proc.stderr
    m = re.search(r"RESULT (\d+) passed, (\d+) failed", out)
    if m:
        p, f = int(m.group(1)), int(m.group(2))
    else:
        p, f = 0, 1
        print(out[-2000:])
    total_pass += p
    total_fail += f
    print(f"--- {suite}: {p} passed, {f} failed (exit {proc.returncode}) ---\n")

print(f"ULTIMATE TOTAL: {total_pass} passed, {total_fail} failed")
sys.exit(1 if total_fail else 0)
