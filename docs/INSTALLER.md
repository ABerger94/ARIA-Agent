# Build and install ARIA for Windows

Supported runtime: Python 3.12, Windows x64. The source installation and frozen
app both keep data at their installation root; new projects live beneath its
workspace. `ARIA_DATA_ROOT` can explicitly select a separate data root.

## Install from source

```powershell
py -3.12 -m pip install --require-hashes -r requirements-lock.txt
py -3.12 aria.py
```

The lock contains the complete Windows dependency resolution and package
hashes. It includes native Qt WebEngine support and resolves the previous
py-vapid/cryptography incompatibility. `requirements.txt` is the editable input.

## Build an executable and installer

Install Inno Setup 6, then run from a fresh Windows checkout:

```powershell
py -3.12 -m pip install --require-hashes -r requirements-build-lock.txt
py -3.12 -m pip check
py -3.12 aria.py --diagnostics
py -3.12 installer/build_installer.py
.\dist\ARIA\ARIA.exe --diagnostics
iscc installer/ARIA-Setup.iss
```

The one-folder build lives at `dist/ARIA/`; setup is
`installer/Output/ARIA-Setup-1.1.0.exe`. The installer is per-user and needs no
administrator privileges. It creates Start Menu and optional desktop shortcuts.
Qt assets, the face mesh, the persona seed and lazy package modules are bundled.
Frozen COM and life-room workers use explicit executable entry modes rather
than trying to execute source files with the frozen launcher.

Diagnostics create the real native dashboard and Work Center against temporary
state, stay offline and check the isolated Windows COM helper. They do not
start microphones or prove physical-device compatibility. Test real speech,
media and camera behavior on the target laptop before replacing a working
installation. Keep the existing workspace and credentials when updating.

## Regenerate locks

Using uv from a dependency-update environment:

```text
uv pip compile requirements.txt --python-platform x86_64-pc-windows-msvc --python-version 3.12 --generate-hashes --output-file requirements-lock.txt
uv pip compile requirements-build.in --python-platform x86_64-pc-windows-msvc --python-version 3.12 --generate-hashes --output-file requirements-build-lock.txt
uv pip compile requirements-test.in --python-version 3.12 --output-file requirements-test.txt
```

CI runs all discovered suites in separate processes, real browser acceptance,
and a Windows job installing the actual build lock, checking startup, building
the app and compiling Inno Setup. Download the `ARIA-Windows` workflow artifact
for the packaged outputs after that job passes.
