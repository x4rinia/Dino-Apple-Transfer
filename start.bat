@echo off
title Dino-Apple-Transfer 🦕
cd /d "%~dp0"

:: 1. Standalone Release Exe
if exist "Dino-Apple-Transfer.exe" (
    start "" "Dino-Apple-Transfer.exe"
    exit
)
if exist "dist\Dino-Apple-Transfer\Dino-Apple-Transfer.exe" (
    start "" "dist\Dino-Apple-Transfer\Dino-Apple-Transfer.exe"
    exit
)

:: 2. Lokale Virtual Environment (.venv)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" run.py
    exit
)

:: 3. Nachbar-Venv (.venv in Dino-iPad-Transfer)
if exist "..\Dino-iPad-Transfer\.venv\Scripts\pythonw.exe" (
    start "" "..\Dino-iPad-Transfer\.venv\Scripts\pythonw.exe" run.py
    exit
)

:: 4. Fallback: System Python
python run.py
pause
