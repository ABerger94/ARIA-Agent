"""Desktop shortcut creation (ported from Mark-LVI, 2026-10-27).

Backs the Settings → General "Add desktop icon" button. Creates
"A.R.I.A.lnk" (Windows), an "A.R.I.A.app" bundle (macOS), or
"A.R.I.A.desktop" (Linux) that launches the repo-root aria.py —
ARIA-Agent's entry point (LVI targets its main.py) — from the
repo root, with the right interpreter.

create_desktop_shortcut() NEVER raises: it returns the human
result line the Settings dialog shows verbatim — a success line
(with detail when there is any) or "Shortcut failed — <error>".
"""

from __future__ import annotations

import os
import platform
import stat as _stat
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "aria.py"
DESCRIPTION = "A.R.I.A AI Assistant"


def get_desktop_dir(system: Optional[str] = None,
                    home: Optional[Path] = None) -> Path:
    """
    Resolve the user's REAL desktop directory instead of assuming
    ~/Desktop, which breaks when:
      • OneDrive "Known Folder Move" relocates the desktop
        (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
      • the XDG desktop is localized on Linux (~/Masaüstü,
        ~/Schreibtisch, ~/Bureau, …).
    Falls back to ~/Desktop only as a last resort.
    """
    home = Path(home) if home else Path.home()
    _os = system or platform.system()

    if _os == "Windows":
        # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
        #       answer; follows OneDrive redirection. No dependencies. ──
        try:
            import ctypes
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD),
                            ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD),
                            ("Data4", ctypes.c_ubyte * 8)]

            # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
            fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                        (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                             0x9A, 0x87, 0xC6, 0x41))
            buf = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                p = Path(buf.value)
                ctypes.windll.ole32.CoTaskMemFree(buf)
                if p.is_dir():
                    return p
        except Exception:
            pass

        # ── 2) Registry: User Shell Folders (may contain %VARS% —
        #       %OneDrive% among them) ────────────────────────────────
        try:
            import winreg
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion"
                    r"\Explorer\User Shell Folders") as key:
                val, _t = winreg.QueryValueEx(key, "Desktop")
            p = Path(os.path.expandvars(val))
            if p.is_dir():
                return p
        except Exception:
            pass

    elif _os == "Linux":
        # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
        try:
            out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                 capture_output=True, text=True,
                                 encoding="utf-8", errors="replace",
                                 timeout=5)
            p = Path(out.stdout.strip())
            if out.stdout.strip() and p != home and p.is_dir():
                return p
        except Exception:
            pass
        try:
            cfg = home / ".config" / "user-dirs.dirs"
            for line in cfg.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("XDG_DESKTOP_DIR"):
                    val = line.split("=", 1)[1].strip().strip('"')
                    p = Path(val.replace("$HOME", str(home)))
                    if p != home and p.is_dir():
                        return p
        except Exception:
            pass

    # macOS: ~/Desktop is always the real path (localization is
    # display-only). Everything else lands here as a last resort.
    return home / "Desktop"


def resolve_python312(runner=None, version_info=None,
                      executable=None) -> Optional[Path]:
    """
    Locate the Python 3.12 interpreter for the desktop shortcut.

    The shortcut must start ARIA on 3.12 — the interpreter her
    dependencies are installed for — no matter which Python the
    current process happens to run. (Launched under the
    system-default 3.14, parts of her silently go missing: the
    push library lived in 3.12's house only.) Resolution order:
    the `py` launcher, the PEP 514 registry, then the current
    interpreter when it already IS 3.12. Returns None when no
    3.12 can be found — the caller falls back and says so.
    """
    runner = runner or subprocess.run
    version_info = version_info or sys.version_info
    executable = executable or sys.executable
    # 1. The py launcher names the exact 3.12 executable.
    try:
        r = runner(["py", "-3.12", "-c",
                    "import sys; print(sys.executable)"],
                   capture_output=True, text=True, timeout=10,
                   creationflags=getattr(subprocess,
                                         "CREATE_NO_WINDOW", 0))
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out and Path(out).is_file():
            return Path(out)
    except Exception:
        pass
    # 2. PEP 514: python.org installs register under PythonCore.
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER,
                     winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(
                        hive,
                        r"Software\Python\PythonCore\3.12"
                        r"\InstallPath") as key:
                    exe, _ = winreg.QueryValueEx(key,
                                                 "ExecutablePath")
                    if exe and Path(exe).is_file():
                        return Path(exe)
            except OSError:
                continue
    except Exception:
        pass
    # 3. Already running under 3.12 — this IS the right Python.
    if tuple(version_info[:2]) == (3, 12):
        return Path(executable)
    return None


def console_interpreter(base) -> Path:
    """The CONSOLE interpreter for `base`.

    resolve_python312 says WHERE Python 3.12 lives; the
    console-vs-windowless choice is made here, at the
    target-selection site: if the resolved exe is pythonw.exe,
    swap to the python.exe beside it when that exists. Under
    pythonw there is no console at all (sys.stdout / sys.stderr
    are None), so every status line vanishes and a startup death
    can leave no trace while the app looks healthy — the shortcut
    must behave exactly like `py -3.12 aria.py` in a terminal.
    """
    base = Path(base)
    if base.name.lower() == "pythonw.exe":
        console_py = base.parent / "python.exe"
        if console_py.exists():
            return console_py
    return base


def create_lnk_windows(lnk: str, target: str, args: str,
                       work_dir: str, icon_loc: str) -> None:
    """
    Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
    Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
    wscript.exe is a GUI-mode host — it never opens a console window.
    """
    # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
    try:
        from win32com.client import Dispatch   # type: ignore
        sh = Dispatch("WScript.Shell")
        sc = sh.CreateShortCut(lnk)
        sc.TargetPath = target
        sc.Arguments = f'"{args}"'
        sc.WorkingDirectory = work_dir
        sc.Description = DESCRIPTION
        sc.IconLocation = icon_loc
        sc.save()
        return
    except ImportError:
        pass

    # ── Option 2: wscript.exe + VBScript (always available on Windows,
    #    GUI-mode executable — never opens a console window) ────────────
    vbs = "\n".join([
        'Set ws = CreateObject("WScript.Shell")',
        f'Set sc = ws.CreateShortcut("{lnk}")',
        f'sc.TargetPath = "{target}"',
        f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
        f'sc.WorkingDirectory = "{work_dir}"',
        f'sc.Description = "{DESCRIPTION}"',
        f'sc.IconLocation = "{icon_loc}"',
        'sc.Save',
    ])
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".vbs")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(vbs)
        proc = subprocess.Popen(
            ["wscript.exe", "/nologo", tmp],
            creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0)
                           | getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        proc.wait(timeout=10)
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def find_icon(root: Optional[Path] = None) -> Optional[Path]:
    """A repo .ico for the shortcut, if one ships — searched in
    assets/config at the root and inside the package. The icon is
    optional: None means the caller falls back to the
    interpreter's own icon."""
    root = Path(root) if root else ROOT
    for folder in (root / "assets", root / "config",
                   root / "aria" / "assets", root / "aria" / "config",
                   root):
        try:
            hits = sorted(folder.glob("*.ico"))
        except Exception:
            continue
        if hits:
            return hits[0]
    return None


def build_shortcut(system: Optional[str] = None,
                   desktop: Optional[Path] = None,
                   home: Optional[Path] = None,
                   executable: Optional[str] = None,
                   version_info=None,
                   runner=None,
                   lnk_writer=None) -> str:
    """Create the desktop shortcut for `system` (default: the
    current OS) and return the detail string — "" when there is
    nothing to add to the success line. Raises on failure;
    create_desktop_shortcut() is the never-raise wrapper."""
    _os = system or platform.system()
    python = Path(executable or sys.executable)
    desktop = (Path(desktop) if desktop
               else get_desktop_dir(system=_os, home=home))

    # ── Windows ───────────────────────────────────────────────────────
    if _os == "Windows":
        py312 = resolve_python312(runner=runner,
                                  version_info=version_info,
                                  executable=executable)
        base = py312 if py312 else python
        target = str(console_interpreter(base))
        detail = (f"Python 3.12 — {target}" if py312 else
                  f"Python 3.12 not found — used {target}")
        ico = find_icon()
        icon_loc = str(ico) if ico else f"{target},0"
        writer = lnk_writer or create_lnk_windows
        writer(str(desktop / "A.R.I.A.lnk"), target, str(SCRIPT),
               str(ROOT), icon_loc)
        return detail

    # ── macOS — proper .app bundle (no Terminal window) ───────────────
    if _os == "Darwin":
        app = desktop / "A.R.I.A.app"
        mac_dir = app / "Contents" / "MacOS"
        res_dir = app / "Contents" / "Resources"
        mac_dir.mkdir(parents=True, exist_ok=True)
        res_dir.mkdir(exist_ok=True)

        # Launcher executable (bash — runs as a background process,
        # macOS does NOT open Terminal for executables inside .app
        # bundles)
        launcher = mac_dir / "ARIA"
        launcher.write_text(
            "#!/usr/bin/env bash\n"
            f'cd "{ROOT}"\n'
            f'exec "{python}" "{SCRIPT}"\n'
        )
        launcher.chmod(launcher.stat().st_mode
                       | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

        # Minimal Info.plist (required for .app recognition)
        (app / "Contents" / "Info.plist").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>\n'
            '  <key>CFBundleExecutable</key><string>ARIA</string>\n'
            '  <key>CFBundleIdentifier</key>'
            '<string>com.aria.assistant</string>\n'
            '  <key>CFBundleName</key><string>A.R.I.A</string>\n'
            '  <key>CFBundlePackageType</key><string>APPL</string>\n'
            '  <key>CFBundleVersion</key><string>1.0</string>\n'
            '</dict></plist>\n'
        )
        return ""

    # ── Linux — .desktop file (Terminal=false, no console) ────────────
    desk = desktop / "A.R.I.A.desktop"
    desk.write_text(
        "[Desktop Entry]\n"
        "Name=A.R.I.A\n"
        f"Exec={python} {SCRIPT}\n"
        f"Path={ROOT}\n"
        "Type=Application\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
    )
    desk.chmod(desk.stat().st_mode | 0o755)
    return ""


def create_desktop_shortcut() -> str:
    """Create the desktop shortcut; NEVER raises.

    Returns the human result line for the Settings dialog:
    "Desktop shortcut created." (plus " (<detail>)" when the
    platform produced one) or "Shortcut failed — <error>"."""
    try:
        detail = build_shortcut()
    except Exception as e:
        return f"Shortcut failed — {e}"
    return ("Desktop shortcut created."
            + (f" ({detail})" if detail else ""))
