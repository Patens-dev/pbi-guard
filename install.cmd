@echo off
title Installing PBI Guard...
setlocal

echo ========================================================
echo             Installing PBI Guard for Power BI
echo ========================================================
echo.

:: 1. Verify Python availability
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo.
    echo Fix: Install Python 3.10+ from python.org or the Microsoft Store
    echo (Microsoft Store version requires zero administrator rights).
    echo.
    pause
    exit /b 1
)

:: 2. Copy files to permanent %LOCALAPPDATA% directory
set "TARGET_DIR=%LOCALAPPDATA%\pbi-guard"
echo [1/3] Copying files to %TARGET_DIR%...
if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"
xcopy /s /e /y /q "%~dp0*" "%TARGET_DIR%\" >nul

:: 3. Ensure required dependency is installed
echo [2/3] Installing dependencies (pyyaml)...
python -m pip install --quiet --upgrade pyyaml

:: 4. Register external tool from the permanent location
echo [3/3] Registering into Power BI Desktop ribbon...
cd /d "%TARGET_DIR%"
python main.py pbitool

echo.
echo ========================================================
echo [SUCCESS] PBI Guard successfully installed!
echo.
echo You can now delete this folder and the downloaded .zip.
echo Open Power BI Desktop and click "PBI Guard" in External Tools.
echo ========================================================
echo.
pause