@echo off
REM A.R.I.A. watchdog (original v6, updated for aria.py)
REM Rename to aria_watchdog.bat and drop in shell:startup folder.
:loop
python "E:\ARIA\aria.py"
echo ARIA exited %date% %time% - restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
