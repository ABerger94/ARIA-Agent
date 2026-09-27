@echo off
setlocal
REM ============================================================
REM  A.R.I.A. AUTOSTART INSTALLER  (v6.0)
REM
REM  1. Rename this file to:  aria_autostart.bat
REM  2. Double-click it once and enter your aria.py path.
REM
REM  It creates aria_watchdog.bat (restarts ARIA if it crashes)
REM  and installs it into Windows Startup, so ARIA launches
REM  automatically every time you boot.
REM ============================================================
echo.
set /p ROBOT_PY=Full path to aria.py (example E:\ARIA\aria.py): 
if "%ROBOT_PY%"=="" (
  echo No path entered. Exiting.
  pause
  exit /b 1
)
set "WATCHDOG=%~dp0aria_watchdog.bat"
(
echo @echo off
echo :loop
echo python "%ROBOT_PY%"
echo echo ARIA exited %%%%date%%%% %%%%time%%%% - restarting in 5 seconds...
echo timeout /t 5 /nobreak ^>nul
echo goto loop
) > "%WATCHDOG%"
copy "%WATCHDOG%" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\" >nul
echo.
echo Installed. ARIA will start with Windows and auto-restart on crash.
echo Watchdog: %WATCHDOG%
echo Startup copy: %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\aria_watchdog.bat
echo.
echo To undo: delete aria_watchdog.bat from the Startup folder above.
pause
