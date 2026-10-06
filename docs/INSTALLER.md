# ARIA Windows installer — build guide

How to produce the `ARIA-Setup-<ver>.exe` Windows installer. Everything runs
on a **Windows machine** (pywin32 / pycaw / pygetwindow are Windows-only);
the one-folder bundle must be built there too.

## 0. Prereqs (Windows)

- Python 3.9+ from python.org, with **"Add python.exe to PATH"** checked
- This repo, cloned or pulled fresh
- Inno Setup 6 (https://jrsoftware.org/isdl.php)

## 1. Install Python deps

```bat
cd <repo>
pip install -r requirements.txt
pip install pyinstaller
```

## 2. Sanity-check the build command (optional, works on Linux too)

```bat
python installer\build_installer.py --dry-run
```

This prints the exact PyInstaller command without executing it. If you are
validating on Linux, exit code 0 + the word `pyinstaller` in the output is
the green light — the real build still happens on Windows.

## 3. Build the one-folder bundle

```bat
python installer\build_installer.py
```

This runs `py_compile` over the tree first (fails fast on syntax errors),
then PyInstaller in `--onedir` mode. Output lands in `dist\ARIA\`.

What gets bundled: the `aria.py` launcher, `aria/assets` (fonts), `soul.md`,
plus `--collect-submodules aria.tools aria.agent` and `--hidden-import`
entries for the lazily-imported modules (`aria.persona`, `aria.first_run`,
`aria.tools.usertools`, `aria.mcp`, `aria.ops_screen`, `aria.inbox`,
`aria.ical`). If a new lazily-imported module is added later, extend
`HIDDEN_IMPORTS` in `installer/build_installer.py`.

## 4. Compile the installer

Open `installer\ARIA-Setup.iss` in Inno Setup and press **Compile**
(Ctrl+F9). The setup exe lands in `installer\Output\ARIA-Setup-1.0.0.exe`.

What the installer does:
- Per-user install to `%LOCALAPPDATA%\Programs\ARIA` (no admin needed)
- Start Menu shortcut + optional desktop icon
- Deletes `~/ARIA/.first_run_done` so the **first-run wizard** fires on
  first launch (skippable via `skip` / Ctrl+C — it re-verifies keys)
- Uninstall leaves `~/ARIA` user data (memory, schedules, keys) in place

## 5. Test

1. Run the setup exe on a clean Windows user account.
2. Launch ARIA from the Start Menu — the first-run wizard should check
   Python version, cv2/PIL, and prompt for missing API keys.
3. Type `skip` at a key prompt to confirm skipping works; the wizard
   must not block startup.

## Versioning

Bump `#define MyAppVersion` at the top of `installer/ARIA-Setup.iss`
before compiling a release build.
