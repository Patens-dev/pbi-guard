@echo off
title Uninstalling PBI Guard...
setlocal

echo Removing PBI Guard from Power BI Desktop External Tools...
python "%LOCALAPPDATA%\pbi-guard\main.py" pbitool --uninstall

echo Removing files from %LOCALAPPDATA%\pbi-guard...
if exist "%LOCALAPPDATA%\pbi-guard" (
    rmdir /s /q "%LOCALAPPDATA%\pbi-guard"
)

echo.
echo [SUCCESS] PBI Guard has been completely removed.
echo.
pause