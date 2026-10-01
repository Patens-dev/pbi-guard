@echo off
title Installing PBI Guard...
setlocal

:: 1. Self-elevate to Administrator (Required to write to CommonProgramFiles)
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting Administrator privileges to register External Tool...
    powershell -NoProfile -Command "Start-Process cmd -ArgumentList '/c \"\"%~dpnx0\"\"' -Verb RunAs"
    exit /b
)

echo ========================================================
echo             Installing PBI Guard for Power BI
echo ========================================================
echo.

:: 2. Verify Python availability
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo Please install Python 3.10+ from python.org or the Microsoft Store.
    echo.
    pause
    exit /b 1
)

:: 3. Copy files to permanent %LOCALAPPDATA% directory
set "TARGET_DIR=%LOCALAPPDATA%\pbi-guard"
echo [1/3] Copying files to %TARGET_DIR%...
if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"
xcopy /s /e /y /q /i "%~dp0*" "%TARGET_DIR%\" >nul

:: 4. Ensure required dependency is installed
echo [2/3] Installing dependencies (pyyaml)...
python -m pip install --quiet --upgrade pyyaml

:: 5. Register external tool into CommonProgramFiles
echo [3/3] Registering into Power BI Desktop ribbon...
cd /d "%TARGET_DIR%"
python main.py pbitool

echo.
echo ========================================================
echo [SUCCESS] PBI Guard successfully installed!
echo.
echo Note: If Power BI Desktop is currently open, restart it
echo to see the "PBI Guard" button in the External Tools tab.
echo ========================================================
echo.
pause