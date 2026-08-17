@echo off
rem --- SurroundUpmix launcher: double-click to open the GUI ---
cd /d "%~dp0"
start "" powershell.exe -STA -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0SurroundUpmix-GUI.ps1"
