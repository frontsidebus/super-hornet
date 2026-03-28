@echo off
:: =============================================================================
:: Super Hornet Cockpit UI — Opens as a standalone app window
:: Gets its own entry in the Windows Volume Mixer for independent volume control.
:: =============================================================================

set HORNET_URL=http://localhost:3838

:: Try Chrome first, then Edge
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --app=%HORNET_URL% --window-size=1400,900
    exit /b
)

if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
    start "" "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" --app=%HORNET_URL% --window-size=1400,900
    exit /b
)

if exist "C:\Program Files\Microsoft\Edge\Application\msedge.exe" (
    start "" "C:\Program Files\Microsoft\Edge\Application\msedge.exe" --app=%HORNET_URL% --window-size=1400,900
    exit /b
)

if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" (
    start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --app=%HORNET_URL% --window-size=1400,900
    exit /b
)

:: Fallback: open in default browser
start %HORNET_URL%
