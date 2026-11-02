@echo off
setlocal enabledelayedexpansion
title SurroundUpmix v2.0 - Environment Setup & Installer
cd /d "%~dp0"

echo ====================================================================
echo   SURROUND UPMIX v2.0 - AUTOMATED INSTALLER
echo   Python DSP & Neural Stem Separation Environment Setup
echo ====================================================================
echo.

REM 1. Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not found in system PATH.
    echo Please install Python 3.10 or 3.11 from https://www.python.org/
    echo Ensure "Add Python to PATH" is checked during setup.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [1/3] Found Python: !PY_VER!

REM 2. Upgrade pip
echo [2/3] Upgrading pip...
python -m pip install --upgrade pip --quiet

REM 3. Install dependencies
echo [3/3] Installing dependencies from requirements.txt...
python -m pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo [WARNING] An error occurred while installing some packages.
    echo Retrying core audio packages...
    python -m pip install numpy scipy soundfile mutagen Pillow demucs pedalboard
)

echo.
echo ====================================================================
echo   Installation Successful!
echo.
echo   Launch Options:
echo     - GUI Studio:  Double-click run_gui.bat
echo     - CLI Runner:  Drag and drop audio files onto run_cli.bat
echo ====================================================================
echo.
pause
