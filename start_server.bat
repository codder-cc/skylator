@echo off
title Nolvus Translator
cd /d "%~dp0"

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║        Nolvus Translator v2.0            ║
echo  ║   Skyrim Mod Localization Engine         ║
echo  ╚══════════════════════════════════════════╝
echo.

:: Check venv exists
if not exist "venv\Scripts\python.exe" (
    echo  [ERROR] Virtual environment not found.
    echo  Run setup_venv.bat first.
    echo.
    pause
    exit /b 1
)

:: Check config exists
if not exist "config.yaml" (
    echo  [ERROR] config.yaml not found.
    echo.
    pause
    exit /b 1
)

echo  Starting server on http://0.0.0.0:5000
echo  Press Ctrl+C to stop.
echo  (Use --log-level DEBUG for verbose output)
echo.

venv\Scripts\python.exe web_server.py --host 0.0.0.0 --log-level INFO

echo.
echo  Server stopped.
pause
