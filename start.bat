@echo off
cd /d "%~dp0"
title YouTube Local Auto Uploader

echo ====================================================
echo   YouTube Local Auto Uploader - v3.2.3
echo ====================================================
echo.

:: Delegate environment creation to run.py. This avoids Windows Python
:: launcher selecting the incompatible free-threaded 3.13t build.
python run.py
if errorlevel 1 (
    echo.
    echo [ERROR] Application failed to start.
    echo See the messages above for dependency or Python version details.
    pause
    exit /b 1
)
pause
