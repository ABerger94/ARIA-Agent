"""Discover every test file, retaining process isolation for legacy module stubs."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='*')
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--logs', default='test-results')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    files = [root / p for p in args.files] if args.files else sorted((root / 'tests').glob('test_*.py'))
    logs = root / args.logs; logs.mkdir(parents=True, exist_ok=True)
    failed = []
    for file in files:
        try:
            run = subprocess.run([sys.executable, '-m', 'pytest', str(file), '-q'], cwd=root,
                                 env={**os.environ, 'PYTHONPATH': str(root / 'tests' / 'offline_runtime') + os.pathsep + os.environ.get('PYTHONPATH', ''), 'QT_QPA_PLATFORM': os.environ.get('QT_QPA_PLATFORM', 'offscreen'), 'TZ':'UTC'},
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=args.timeout)
            output, code = run.stdout, run.returncode
        except subprocess.TimeoutExpired as exc:
            output = f'Timeout after {args.timeout}s\n{exc.stdout!r}'; code = 124
        (logs / f'{file.stem}.txt').write_text(output, encoding='utf-8')
        print(f'{"PASS" if code in (0, 5) else "FAIL"} {file.name}: {output.strip().splitlines()[-1:] or [code]}', flush=True)
        # Empty platform-only suites are allowed, import/collection errors are not.
        if code not in (0, 5): failed.append(file.name)
    print(f'{len(files)} files; {len(failed)} failed: {", ".join(failed)}', flush=True)
    return bool(failed)


if __name__ == '__main__': sys.exit(main())
