@echo off
title Dino-Apple-Transfer 🦕
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" run.py
    exit
)
if exist "dist\Dino-Apple-Transfer\Dino-Apple-Transfer.exe" (
    start "" "dist\Dino-Apple-Transfer\Dino-Apple-Transfer.exe"
    exit
)
python run.py
pause
