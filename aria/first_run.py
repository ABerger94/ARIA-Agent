"""First-run wizard: environment sanity check + API key onboarding.

Invoked from main.py when ~/ARIA/.first_run_done is absent:

    import os
    if not os.path.exists(os.path.expanduser("~/ARIA/.first_run_done")):
        from aria import first_run
        first_run.run_first_run_wizard()

(The coordinator wires this hook into main.py — this module only provides the
wizard itself.)

Behavior:
  1. Checks the Python version (>= 3.9) and critical imports (cv2, PIL),
     printing clear missing-dependency messages (never crashes on absence).
  2. Verifies aria_keys.json exists (config.KEYS_FILE); config creates a
     template automatically on import if it is missing.
  3. Prompts on the console for any missing API keys and writes them back
     via config.save_keys(). Keys are read with getpass when available so
     they do not echo; they are never printed or logged.
  4. Non-blocking and fully skippable: typing "skip", sending EOF, or hitting
     Ctrl+C at any prompt exits the wizard immediately. The done-flag is still
     written so main.py does not nag on every boot.

Top-level imports: stdlib + aria.config only. All heavy imports (cv2, PIL,
getpass) happen lazily inside functions.
"""

import os
import sys

from aria import config as _config

_DONE_FLAG = os.path.expanduser(os.path.join("~", "ARIA", ".first_run_done"))

# (keys-file field, friendly prompt). Only asked when the stored value is
# missing/"INSERT" — never re-asks for keys that are already set.
_KEY_PROMPTS = [
    ("GEMINI_API_KEY", "Gemini API key (main brain — required)"),
    ("GROQ_API_KEY", "Groq API key (fallback provider, optional)"),
    ("OPENROUTER_API_KEY", "OpenRouter API key (fallback provider, optional)"),
    ("GITHUB_TOKEN", "GitHub token (code pushes, optional)"),
]

_MIN_PY = (3, 9)


class _Skipped(Exception):
    """Raised when the user skips the wizard (skip / EOF / Ctrl+C)."""


def _ask(prompt, secret=False):
    """One console prompt. Raises _Skipped on 'skip', EOF, or Ctrl+C."""
    try:
        if secret:
            try:
                import getpass
                raw = getpass.getpass(prompt)
            except Exception:
                raw = input(prompt)  # getpass unavailable (some consoles)
        else:
            raw = input(prompt)
    except (EOFError, KeyboardInterrupt):
        raise _Skipped()
    value = raw.strip()
    if value.lower() == "skip":
        raise _Skipped()
    return value


def _check_python():
    ok = sys.version_info >= _MIN_PY
    ver = ".".join(str(x) for x in sys.version_info[:3])
    if ok:
        print(f"  [ok] Python {ver}")
    else:
        print(f"  [!!] Python {ver} — ARIA needs {_MIN_PY[0]}.{_MIN_PY[1]}+.")
        print("       Install a newer Python from https://www.python.org/downloads/")
    return ok


def _check_imports():
    results = {}
    for mod, pip_name in (("cv2", "opencv-python"), ("PIL", "Pillow")):
        try:
            __import__(mod)
            print(f"  [ok] {mod}")
            results[mod] = True
        except Exception:
            print(f"  [!!] {mod} is missing — install with:  pip install {pip_name}")
            results[mod] = False
    return results


def _missing_keys():
    missing = []
    for field, _label in _KEY_PROMPTS:
        val = _config._KEYS.get(field)
        if not val or val == "INSERT":
            missing.append(field)
    return missing


def _prompt_keys():
    changed = False
    for field, label in _KEY_PROMPTS:
        val = _config._KEYS.get(field)
        if val and val != "INSERT":
            continue
        print(f"\n  {label}")
        print("  (type 'skip' to skip the rest of the wizard)")
        entered = _ask(f"  > {field}: ", secret=True)
        if entered:
            _config._KEYS[field] = entered
            changed = True
        else:
            print("  (left blank — you can add it later in aria_keys.json)")
    if changed:
        if _config.save_keys():
            print("\n  [ok] Keys saved to aria_keys.json")
        else:
            print("\n  [!!] Could not save aria_keys.json — check the file is writable.")


def _mark_done():
    try:
        os.makedirs(os.path.dirname(_DONE_FLAG), exist_ok=True)
        with open(_DONE_FLAG, "w", encoding="utf-8") as _f:
            _f.write("first-run wizard completed (or skipped)\n")
    except Exception as _e:
        print(f"  [!!] Could not write done-flag: {_e}")


def run_first_run_wizard():
    """Run the wizard. Returns True if completed, False if skipped/aborted."""
    print("\n" + "=" * 60)
    print("  A.R.I.A. — first-run setup")
    print("  (type 'skip' or press Ctrl+C at any prompt to skip)")
    print("=" * 60)
    try:
        print("\n[1/3] Python version")
        py_ok = _check_python()
        print("\n[2/3] Critical packages")
        imports = _check_imports()
        print("\n[3/3] API keys")
        print(f"  Keys file: {_config.KEYS_FILE}")
        if not os.path.exists(_config.KEYS_FILE):
            print("  [!!] aria_keys.json not found — config will create a template.")
        _prompt_keys()
        print("\nSetup finished.", end=" ")
        if not py_ok or not all(imports.values()):
            print("Fix the items marked [!!] above, then restart ARIA.")
        else:
            print("You're good to go.")
        return True
    except _Skipped:
        print("\nWizard skipped — ARIA will start anyway. "
              "Run it again by deleting ~/ARIA/.first_run_done.")
        return False
    finally:
        _mark_done()
