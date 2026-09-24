@echo off
cd /d "%~dp0"
title YouTube Local Auto Uploader - Offline Setup

echo ====================================================
echo   YouTube Local Auto Uploader - v3.2.3
 echo ====================================================
echo.

echo [INFO] Offline setup requires a standard CPython 3.11-3.13 build.
echo [INFO] Free-threaded Python 3.13t is not supported by the current Google auth dependency chain.
echo.

where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    py -3.13 -m venv .venv
    if errorlevel 1 py -3.12 -m venv .venv
    if errorlevel 1 py -3.11 -m venv .venv
) else (
    python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Could not create a standard Python virtual environment.
    pause
    exit /b 1
)

if exist "wheels" (
    .\.venv\Scripts\python.exe -m pip install --no-index --find-links=wheels -r requirements.txt
) else (
    echo [ERROR] No wheels directory found. Offline setup cannot continue.
    pause
    exit /b 1
)

if errorlevel 1 (
    echo [ERROR] Offline dependency installation failed.
    pause
    exit /b 1
)

echo.
echo ====================================================
echo Setup complete. Run start.bat or: python run.py
echo ====================================================
pause
