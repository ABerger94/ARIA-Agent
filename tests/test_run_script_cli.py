"""Pass 36 #42: aria.py --run-script preserves Python exit semantics.

A script that exits with a message (sys.exit("boom")) must fail the
run (exit 1, message on stderr), not report success — the old code
mapped every non-int SystemExit.code to 0, so the sandbox read a
failed validation as done. And the script must see its own argv
([script, *args]), not ARIA's launcher argv.
"""
import subprocess
import sys
from pathlib import Path

ARIA_PY = Path(__file__).resolve().parents[1] / "aria.py"


def _run_script(tmp_path, source, args=()):
    script = tmp_path / "child.py"
    script.write_text(source, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ARIA_PY), "--run-script", str(script),
         *[str(a) for a in args]],
        capture_output=True, text=True, timeout=60)
    return proc


def test_message_exit_fails(tmp_path):
    proc = _run_script(tmp_path, 'import sys\nsys.exit("validation failed")\n')
    assert proc.returncode == 1, proc
    assert "validation failed" in proc.stderr


def test_int_exit_preserved(tmp_path):
    proc = _run_script(tmp_path, "import sys\nsys.exit(3)\n")
    assert proc.returncode == 3, proc


def test_bare_exit_succeeds(tmp_path):
    proc = _run_script(tmp_path, "print('ok')\n")
    assert proc.returncode == 0, proc
    assert "ok" in proc.stdout


def test_script_gets_its_own_argv(tmp_path):
    proc = _run_script(
        tmp_path,
        "import sys, json\nprint(json.dumps(sys.argv))\n",
        args=("alpha", "beta"))
    assert proc.returncode == 0, proc
    import json
    argv = json.loads(proc.stdout.strip().splitlines()[-1])
    assert argv[0].endswith("child.py"), argv
    assert argv[1:] == ["alpha", "beta"], argv
