@echo off
title Surround Upmix CLI v2.0
cd /d "%~dp0"
if "%~1"=="" (
    echo ============================================================
    echo   Surround Upmix CLI Runner v2.0
    echo   Drag and drop audio files or folders onto this batch file!
    echo ============================================================
    echo.
    python cli.py --help
    echo.
    pause
    exit /b
)

python cli.py %*
if errorlevel 1 (
    echo.
    echo An error occurred during conversion.
    pause
)
