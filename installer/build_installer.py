#!/usr/bin/env python3
"""ARIA Windows one-folder installer build (PyInstaller).

Usage (run from the repo root, on the Windows build machine):

    python installer/build_installer.py --dry-run   # print the exact command, do nothing
    python installer/build_installer.py             # run the build via subprocess

The generated command is a PyInstaller **one-folder** (--onedir) build of the
root launcher (aria.py). It is validated headlessly on Linux by:
  1. py_compile over the whole tree (syntax gate), and
  2. --dry-run, which must exit 0 and print a command containing "pyinstaller".

Notes:
  - The build itself must run on Windows (pywin32 / pycaw / pygetwindow are
    Windows-only). The --add-data separators below use ";" (Windows style).
  - Lazy imports inside functions are invisible to PyInstaller's static
    analysis, so --collect-submodules covers aria.tools + aria.agent and
    --hidden-import covers the top-level lazy modules. If a worker module is
    added later, extend HIDDEN_IMPORTS.
"""

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_NAME = "ARIA"
ENTRY = os.path.join(REPO_ROOT, "aria.py")
DIST_PATH = os.path.join(REPO_ROOT, "dist")
BUILD_PATH = os.path.join(REPO_ROOT, "build")

# (source rel-to-repo, dest rel-to-bundle) — ";" separator = Windows style.
ADD_DATA = [
    (os.path.join("aria", "assets"), os.path.join("aria", "assets")),
    ("soul.md", "."),
]

HIDDEN_IMPORTS = [
    "aria.persona",
    "aria.first_run",
    "aria.tools.usertools",
    "aria.mcp",
    "aria.ops_screen",
    "aria.inbox",
    "aria.ical",
]

COLLECT_SUBMODULES = [
    "aria.tools",
    "aria.agent",
]


def build_command():
    """Return the exact pyinstaller command as a list of argv tokens."""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",
        "--name", APP_NAME,
        "--distpath", DIST_PATH,
        "--workpath", BUILD_PATH,
    ]
    for src, dst in ADD_DATA:
        cmd += ["--add-data", f"{src};{dst}"]
    for pkg in COLLECT_SUBMODULES:
        cmd += ["--collect-submodules", pkg]
    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    cmd.append(ENTRY)
    return cmd


def command_string(cmd):
    """Human-readable single-line command (for --dry-run output)."""
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


def syntax_gate():
    """py_compile every .py in the repo. Raises SystemExit(1) on failure."""
    import py_compile
    bad = []
    for dirpath, _dirnames, filenames in os.walk(REPO_ROOT):
        if os.path.basename(dirpath) in (".git", "build", "dist", "__pycache__"):
            continue
        for fn in filenames:
            if fn.endswith(".py"):
                path = os.path.join(dirpath, fn)
                try:
                    py_compile.compile(path, doraise=True)
                except py_compile.PyCompileError as e:
                    bad.append(str(e))
    if bad:
        print("py_compile FAILED:", flush=True)
        for b in bad:
            print("  " + b, flush=True)
        sys.exit(1)
    print("py_compile: clean.")


def main(argv):
    dry_run = "--dry-run" in argv
    cmd = build_command()
    if dry_run:
        print("# dry-run: the exact pyinstaller command (not executed)")
        print(command_string(cmd))
        return 0
    syntax_gate()
    print("# running:", flush=True)
    print(command_string(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=REPO_ROOT)
    if proc.returncode != 0:
        print(f"pyinstaller FAILED (exit {proc.returncode})", flush=True)
        return proc.returncode
    print(f"Build OK: {os.path.join(DIST_PATH, APP_NAME)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
