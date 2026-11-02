@echo off
title Surround Upmix Studio v2.0
setlocal
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>nul && (py -3.10 gui.py 2>nul || py gui.py) || python gui.py
if errorlevel 1 (
    echo.
    echo Press any key to exit...
    pause >nul
)
