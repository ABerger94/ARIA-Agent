"""ARIA ULTIMATE — aggregated test runner.

Runs the four standalone worker suites (test_ultimate_a/b/c/d.py) as
subprocesses and aggregates pass/fail counts. Each suite follows the
test_headless.py stub pattern and is independently runnable.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ["test_ultimate_a.py", "test_ultimate_b.py", "test_ultimate_c.py", "test_ultimate_d.py", "test_ultimate_e.py"]

total_pass, total_fail = 0, 0
for suite in SUITES:
    path = os.path.join(HERE, suite)
    print(f"=== {suite} ===")
    try:
        proc = subprocess.run(
            [sys.executable, path], capture_output=True, text=True, timeout=300,
            cwd=os.path.dirname(HERE),
        )
    except Exception as e:
        print(f"ERROR running {suite}: {e}")
        total_fail += 1
        continue
    out = proc.stdout + proc.stderr
    print(out.strip()[-3000:] if len(out.strip()) > 3000 else out.strip())
    p = len(re.findall(r"^PASS\s", out, re.M))
    f = len(re.findall(r"^(FAIL|ERROR)\s", out, re.M))
    # Fallback: parse "N passed" style summaries if present
    if p == 0 and f == 0:
        m = re.search(r"(\d+)\s+passed", out)
        if m:
            p = int(m.group(1))
    total_pass += p
    total_fail += f
    print(f"--- {suite}: {p} passed, {f} failed (exit {proc.returncode}) ---\n")

print(f"ULTIMATE TOTAL: {total_pass} passed, {total_fail} failed")
sys.exit(1 if total_fail else 0)
