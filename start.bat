@echo off
cd /d "%~dp0"
title OmniCast Studio — Multi-Platform Auto Publisher

echo ====================================================
echo   OmniCast Studio — Multi-Platform Auto Publisher
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
