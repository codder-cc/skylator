@echo off
title Skylator
cd /d "%~dp0"

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║             Skylator                     ║
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

:: Build frontend if dist is missing or stale
if not exist "frontend\dist\index.html" (
    echo  [BUILD] Frontend not built — building now...
    if not exist "frontend\node_modules" (
        echo  [SETUP] Installing frontend dependencies...
        pushd frontend
        npm install
        popd
    )
    pushd frontend
    npm run build
    popd
    echo.
)

echo  Server:  http://0.0.0.0:5000
echo  App:     http://127.0.0.1:5000/app/
echo.
echo  For development with hot reload, use dev.bat instead.
echo  Press Ctrl+C to stop.
echo.

venv\Scripts\python.exe web_server.py --host 0.0.0.0 --log-level INFO

echo.
echo  Server stopped.
pause
