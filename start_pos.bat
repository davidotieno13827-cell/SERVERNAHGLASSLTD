@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo POS environment not found.
    echo Run the setup commands in README.md first.
    pause
    exit /b 1
)

powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '.venv\Scripts\python.exe' -ArgumentList 'run.py' -WorkingDirectory '%CD%' -WindowStyle Hidden; Start-Sleep -Milliseconds 2000; Start-Process 'http://127.0.0.1:5000'"