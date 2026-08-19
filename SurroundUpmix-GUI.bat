@echo off
REM Double-click launcher for the SurroundUpmix dark-mode GUI.
REM Uses the Windows launcher to prefer Python 3.10 (Demucs), else default.
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (py -3.10 gui.py 2>nul || py gui.py) || python gui.py
