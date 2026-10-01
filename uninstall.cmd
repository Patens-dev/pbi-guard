@echo off
title Uninstalling PBI Guard...
setlocal

:: 1. Self-elevate to Administrator (Required to remove from CommonProgramFiles)
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting Administrator privileges to remove Power BI External Tool...
    powershell -NoProfile -Command "Start-Process cmd -ArgumentList '/c \"\"%~dpnx0\"\"' -Verb RunAs"
    exit /b
)

echo ========================================================
echo             Uninstalling PBI Guard
echo ========================================================
echo.

:: 2. Run Python uninstaller if main.py is present
if exist "%LOCALAPPDATA%\pbi-guard\main.py" (
    echo [1/3] Unregistering from Power BI ribbon via Python...
    python "%LOCALAPPDATA%\pbi-guard\main.py" pbitool --uninstall >nul 2>&1
)

:: 3. Direct cleanup fallback (ensures manifests are deleted even if Python failed)
echo [2/3] Cleaning up manifest files across all External Tools directories...
del /f /q "%CommonProgramFiles%\Microsoft Shared\Power BI Desktop\External Tools\pbi-guard.pbitool.json" >nul 2>&1
if defined CommonProgramFiles(x86) (
    del /f /q "%CommonProgramFiles(x86)%\Microsoft Shared\Power BI Desktop\External Tools\pbi-guard.pbitool.json" >nul 2>&1
)
del /f /q "%LOCALAPPDATA%\Microsoft\Power BI Desktop Store App\External Tools\pbi-guard.pbitool.json" >nul 2>&1
del /f /q "%LOCALAPPDATA%\Microsoft\Power BI Desktop\External Tools\pbi-guard.pbitool.json" >nul 2>&1

:: Remove from Microsoft Store UWP app cache if present
powershell -NoProfile -Command "Get-ChildItem -Path \"$env:LOCALAPPDATA\Packages\Microsoft.MicrosoftPowerBIDesktop*\" -Recurse -Filter 'pbi-guard.pbitool.json' -ErrorAction SilentlyContinue | Remove-Item -Force" >nul 2>&1

:: 4. Remove local installation directory
echo [3/3] Removing files from %LOCALAPPDATA%\pbi-guard...
if exist "%LOCALAPPDATA%\pbi-guard" (
    rmdir /s /q "%LOCALAPPDATA%\pbi-guard"
)

echo.
echo ========================================================
echo [SUCCESS] PBI Guard has been completely removed.
echo.
echo Note: If Power BI Desktop is open, restart it for the
echo ribbon button to disappear.
echo ========================================================
echo.
pause