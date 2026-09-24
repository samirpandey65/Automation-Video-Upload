@echo off
setlocal enabledelayedexpansion
title YouTube Local Auto Uploader - AI Setup & Verification
cd /d "%~dp0"

echo ================================================================
echo   YouTube Local Auto Uploader - Local AI Multi-System Setup
echo ================================================================
echo.

:: 1. Check if Ollama is installed
set OLLAMA_EXE=
where ollama >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set OLLAMA_EXE=ollama
) else if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
) else if exist "C:\Program Files\Ollama\ollama.exe" (
    set "OLLAMA_EXE=C:\Program Files\Ollama\ollama.exe"
)

if "%OLLAMA_EXE%"=="" (
    echo [!] Ollama was not found on this system.
    echo.
    echo Please install Ollama from: https://ollama.com/download/windows
    echo.
    set /p INSTALL_CHOICE="Would you like to open the download page now? (Y/N): "
    if /i "!INSTALL_CHOICE!"=="Y" (
        start https://ollama.com/download/windows
    )
    echo Once installed, run this script again.
    pause
    exit /b 1
)

echo [OK] Ollama detected at: "%OLLAMA_EXE%"
echo.

:: 2. Ensure Ollama service is running
echo Checking if Ollama service is active...
powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 -UseBasicParsing; exit 0 } catch { exit 1 }" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Starting Ollama background service...
    start "" /B "%OLLAMA_EXE%" serve
    timeout /t 3 /nobreak >nul
)

:: 3. Check if qwen2.5:3b is already available
echo Checking if model qwen2.5:3b is already downloaded on this system...
"%OLLAMA_EXE%" list | findstr /I "qwen2.5:3b" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo.
    echo ================================================================
    echo [OK] Model 'qwen2.5:3b' is ALREADY installed and ready!
    echo No re-download needed. Instant inference active.
    echo ================================================================
) else (
    echo.
    echo [!] Model 'qwen2.5:3b' was not found on this system.
    echo Downloading model once via Ollama (approx. 1.9 GB)...
    "%OLLAMA_EXE%" pull qwen2.5:3b
    if %ERRORLEVEL% equ 0 (
        echo.
        echo ================================================================
        echo [OK] Model 'qwen2.5:3b' successfully downloaded and ready!
        echo Future runs on this system will NOT re-download.
        echo ================================================================
    ) else (
        echo [ERROR] Failed to download model. Please check internet connection.
    )
)

echo.
pause

