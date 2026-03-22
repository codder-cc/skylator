@echo off
title Skylator — Dev Mode
cd /d "%~dp0"

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║        Skylator — Development Mode       ║
echo  ║   Flask (BE) + Vite HMR (FE)            ║
echo  ╚══════════════════════════════════════════╝
echo.

:: Check venv
if not exist "venv\Scripts\python.exe" (
    echo  [ERROR] venv not found. Run setup_venv.bat first.
    pause & exit /b 1
)

:: Check node_modules
if not exist "frontend\node_modules" (
    echo  [SETUP] Installing frontend dependencies...
    pushd frontend
    npm install
    popd
)

:: Check config
if not exist "config.yaml" (
    echo  [WARN] config.yaml not found — some features will be disabled.
    echo.
)

echo  Starting Flask on  http://127.0.0.1:5000
echo  Starting Vite on   http://127.0.0.1:5173
echo.
echo  Open the app at:   http://127.0.0.1:5173/app/
echo  (Vite proxies all API calls to Flask)
echo.
echo  Press Ctrl+C in each window to stop.
echo.

:: Start Vite in a new console window
start "Skylator — Vite HMR" cmd /k "cd /d "%~dp0frontend" && npm run dev"

:: Give Vite a moment to bind
timeout /t 2 /nobreak >nul

:: Start Flask in this window
venv\Scripts\python.exe web_server.py --host 127.0.0.1 --log-level INFO

echo.
echo  Flask stopped. Close the Vite window manually.
pause
