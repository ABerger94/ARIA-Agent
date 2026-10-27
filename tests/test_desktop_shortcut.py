"""Desktop shortcut creation (Mark-LVI port): aria.desktop_shortcut.

Pure stdlib module, exercised directly. The Windows .lnk writer is
tested with a fake win32com injected into sys.modules — no real
registry/COM in the sandbox; the Linux .desktop branch runs
end-to-end against a tmp desktop.
"""
import sys
import types
from pathlib import Path

pkg = types.ModuleType('aria'); pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg
from aria import desktop_shortcut as dsc


def _fake_runner_ok(exe):
    def runner(cmd, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout=str(exe) + '\n',
                                     stderr='')
    return runner


def _fake_runner_missing(cmd, **kwargs):
    raise FileNotFoundError('py launcher not present')


# ── Linux .desktop, end to end ───────────────────────────────────────

def test_linux_desktop_file_end_to_end(tmp_path):
    desktop = tmp_path / 'Desktop'; desktop.mkdir()
    fake_py = tmp_path / 'python3.12'; fake_py.touch()
    detail = dsc.build_shortcut(system='Linux', desktop=desktop,
                                 executable=str(fake_py))
    assert detail == ''
    desk = desktop / 'A.R.I.A.desktop'
    assert desk.is_file()
    text = desk.read_text(encoding='utf-8')
    assert text.startswith('[Desktop Entry]\n')
    assert 'Name=A.R.I.A\n' in text
    assert f'Exec={fake_py} {dsc.SCRIPT}\n' in text
    assert f'Path={dsc.ROOT}\n' in text
    assert 'Type=Application\n' in text
    assert 'Terminal=false\n' in text
    assert 'Categories=Utility;\n' in text
    assert desk.stat().st_mode & 0o111          # executable bit set


# ── macOS .app bundle shape ──────────────────────────────────────────

def test_macos_app_bundle_shape(tmp_path):
    detail = dsc.build_shortcut(system='Darwin', desktop=tmp_path)
    assert detail == ''
    app = tmp_path / 'A.R.I.A.app'
    launcher = app / 'Contents' / 'MacOS' / 'ARIA'
    assert launcher.is_file()
    assert launcher.stat().st_mode & 0o111
    body = launcher.read_text(encoding='utf-8')
    assert body.startswith('#!/usr/bin/env bash\n')
    assert f'cd "{dsc.ROOT}"' in body
    assert f'"{dsc.SCRIPT}"' in body
    plist = (app / 'Contents' / 'Info.plist').read_text(encoding='utf-8')
    assert '<key>CFBundleName</key><string>A.R.I.A</string>' in plist
    assert '<key>CFBundleExecutable</key><string>ARIA</string>' in plist


# ── resolve_python312 ────────────────────────────────────────────────

def test_resolve_python312_via_py_launcher(tmp_path):
    exe = tmp_path / 'python.exe'; exe.touch()
    found = dsc.resolve_python312(runner=_fake_runner_ok(exe),
                                  version_info=(3, 14, 0),
                                  executable='/somewhere/else/python.exe')
    assert found == exe


def test_resolve_python312_launcher_bad_result(tmp_path):
    def runner(cmd, **kwargs):
        return types.SimpleNamespace(returncode=1, stdout='', stderr='')
    assert dsc.resolve_python312(runner=runner, version_info=(3, 14, 0),
                                 executable='/x/python.exe') is None


def test_resolve_python312_not_found(tmp_path):
    # Launcher missing, no registry (non-Windows sandbox), and the
    # current interpreter is not 3.12 → None (caller falls back
    # and says so).
    assert dsc.resolve_python312(runner=_fake_runner_missing,
                                 version_info=(3, 14, 0),
                                 executable='/x/python.exe') is None


def test_resolve_python312_current_interpreter_is_312(tmp_path):
    exe = tmp_path / 'python.exe'; exe.touch()
    found = dsc.resolve_python312(runner=_fake_runner_missing,
                                  version_info=(3, 12, 10),
                                  executable=str(exe))
    assert found == exe


# ── console interpreter selection ────────────────────────────────────

def test_console_interpreter_swaps_pythonw(tmp_path):
    pythonw = tmp_path / 'pythonw.exe'; pythonw.touch()
    console = tmp_path / 'python.exe'; console.touch()
    assert dsc.console_interpreter(pythonw) == console
    console.unlink()
    # No console sibling: the windowless exe is used as-is.
    assert dsc.console_interpreter(pythonw) == pythonw
    plain = tmp_path / 'python.exe'; plain.touch()
    assert dsc.console_interpreter(plain) == plain


# ── Windows branch via an injected .lnk writer ───────────────────────

def test_windows_branch_targets_console_python312(tmp_path):
    desktop = tmp_path / 'Desktop'; desktop.mkdir()
    pythonw = tmp_path / 'pythonw.exe'; pythonw.touch()
    console = tmp_path / 'python.exe'; console.touch()
    recorded = {}
    def writer(lnk, target, args, work_dir, icon_loc):
        recorded.update(lnk=lnk, target=target, args=args,
                        work_dir=work_dir, icon_loc=icon_loc)
    detail = dsc.build_shortcut(system='Windows', desktop=desktop,
                                 runner=_fake_runner_ok(pythonw),
                                 lnk_writer=writer)
    # Resolved 3.12 was pythonw → the shortcut targets python.exe.
    assert recorded['target'] == str(console)
    assert recorded['args'] == str(dsc.SCRIPT)
    assert recorded['work_dir'] == str(dsc.ROOT)
    assert recorded['lnk'] == str(desktop / 'A.R.I.A.lnk')
    assert detail == f'Python 3.12 — {console}'


def test_windows_branch_fallback_says_so(tmp_path, monkeypatch):
    desktop = tmp_path / 'Desktop'; desktop.mkdir()
    current = tmp_path / 'python.exe'; current.touch()
    recorded = {}
    def writer(lnk, target, args, work_dir, icon_loc):
        recorded.update(target=target, icon_loc=icon_loc)
    # No icon available at all (none shipped, render failed) →
    # the interpreter's own icon is referenced.
    monkeypatch.setattr(dsc, 'find_or_create_icon', lambda root=None: None)
    detail = dsc.build_shortcut(system='Windows', desktop=desktop,
                                 executable=str(current),
                                 version_info=(3, 14, 0),
                                 runner=_fake_runner_missing,
                                 lnk_writer=writer)
    assert recorded['target'] == str(current)
    assert detail == f'Python 3.12 not found — used {current}'
    assert recorded['icon_loc'] == f'{current},0'


def test_windows_branch_uses_orb_icon(tmp_path, monkeypatch):
    # Alek, 2026-10-27: the shortcut wears the waveform orb,
    # not the Python window — whatever find_or_create_icon
    # resolves is what lands in IconLocation.
    desktop = tmp_path / 'Desktop'; desktop.mkdir()
    current = tmp_path / 'python.exe'; current.touch()
    ico = tmp_path / 'aria.ico'; ico.touch()
    monkeypatch.setattr(dsc, 'find_or_create_icon', lambda root=None: ico)
    recorded = {}
    def writer(lnk, target, args, work_dir, icon_loc):
        recorded.update(icon_loc=icon_loc)
    dsc.build_shortcut(system='Windows', desktop=desktop,
                       executable=str(current),
                       version_info=(3, 12, 0),
                       runner=_fake_runner_missing,
                       lnk_writer=writer)
    assert recorded['icon_loc'] == str(ico)


# ── the orb icon itself (Alek, 2026-10-27) ───────────────────────────

def test_repo_ships_the_orb_icon():
    ico = dsc.find_icon()
    assert ico is not None and ico.name == 'aria.ico' and ico.exists()


def test_build_aria_icon_multires_and_deterministic(tmp_path):
    pytest = __import__('pytest')
    PIL = pytest.importorskip('PIL.Image')
    from aria import icon_render
    a, b = tmp_path / 'a.ico', tmp_path / 'b.ico'
    assert icon_render.build_aria_icon(a) is True
    assert a.exists() and a.stat().st_size > 10_000
    sizes = set(PIL.open(a).info.get('sizes', []))
    for want in ((256, 256), (128, 128), (48, 48), (32, 32), (16, 16)):
        assert want in sizes, (want, sizes)
    assert icon_render.build_aria_icon(b) is True
    assert a.read_bytes() == b.read_bytes()


# ── the .lnk writer itself, against a fake win32com ──────────────────

def test_create_lnk_windows_with_fake_win32com(monkeypatch, tmp_path):
    recorded = {}
    class FakeShortcut:
        def save(self):
            recorded['saved'] = True
    class FakeShell:
        def CreateShortCut(self, lnk):
            recorded['lnk'] = lnk
            sc = FakeShortcut()
            recorded['shortcut'] = sc
            return sc
    def fake_dispatch(name):
        recorded['dispatch'] = name
        return FakeShell()
    win32com = types.ModuleType('win32com')
    client = types.ModuleType('win32com.client')
    client.Dispatch = fake_dispatch
    win32com.client = client
    monkeypatch.setitem(sys.modules, 'win32com', win32com)
    monkeypatch.setitem(sys.modules, 'win32com.client', client)
    lnk = str(tmp_path / 'A.R.I.A.lnk')
    dsc.create_lnk_windows(lnk, r'C:\Python312\python.exe',
                           r'E:\ARIA\aria.py', r'E:\ARIA',
                           r'C:\Python312\python.exe,0')
    assert recorded['dispatch'] == 'WScript.Shell'
    assert recorded['lnk'] == lnk
    sc = recorded['shortcut']
    assert sc.TargetPath == r'C:\Python312\python.exe'
    assert sc.Arguments == r'"E:\ARIA\aria.py"'   # quoted script path
    assert sc.WorkingDirectory == r'E:\ARIA'
    assert sc.Description == 'A.R.I.A AI Assistant'
    assert sc.IconLocation == r'C:\Python312\python.exe,0'
    assert recorded['saved'] is True


# ── create_desktop_shortcut: the never-raise wrapper ─────────────────

def test_create_desktop_shortcut_success_line(monkeypatch, tmp_path):
    desktop = tmp_path / 'Desktop'; desktop.mkdir()
    monkeypatch.setattr(dsc, 'get_desktop_dir',
                        lambda system=None, home=None: desktop)
    result = dsc.create_desktop_shortcut()
    assert isinstance(result, str)
    assert result == 'Desktop shortcut created.'
    assert (desktop / 'A.R.I.A.desktop').is_file()


def test_create_desktop_shortcut_failure_line_not_raise(monkeypatch,
                                                       tmp_path):
    blocker = tmp_path / 'not_a_dir'; blocker.touch()
    monkeypatch.setattr(dsc, 'get_desktop_dir',
                        lambda system=None, home=None: blocker)
    result = dsc.create_desktop_shortcut()
    assert isinstance(result, str)
    assert result.startswith('Shortcut failed — ')
