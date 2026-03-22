@echo off
REM Skylator Remote Worker — Windows setup
REM Run once: setup.bat
REM Then start: python server.py

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==============================
echo   Skylator Remote Worker setup
echo ==============================

REM ── Python check ──────────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: python not found. Install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo %%v

REM ── Virtual environment ────────────────────────────────────────────────────
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat

python -m pip install --upgrade pip --quiet

REM ── Base dependencies ──────────────────────────────────────────────────────
echo Installing base dependencies...
pip install -r requirements.txt --quiet

REM ── llama-cpp-python (CUDA) ────────────────────────────────────────────────
echo.
echo Install llama-cpp-python with CUDA support?
echo   Pre-built wheel (no compiler needed — CUDA 12.x):
echo   pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
echo.
set /p CHOICE="Install CUDA pre-built wheel now? [y/N]: "
if /i "!CHOICE!"=="y" (
    pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
    if errorlevel 1 (
        echo Pre-built wheel install failed. Build from source:
        echo   set CMAKE_ARGS=-DGGML_CUDA=on
        echo   pip install llama-cpp-python --no-binary llama-cpp-python
    )
)

REM ── Config ────────────────────────────────────────────────────────────────
if not exist "server_config.yaml" (
    echo.
    echo Copying example config...
    copy server_config.example.yaml server_config.yaml
    echo ^>^>^> Edit server_config.yaml before starting the server! ^<^<^<
)

echo.
echo Setup complete.
echo.
echo Next steps:
echo   1. Edit server_config.yaml  — set your model path / repo_id
echo   2. Activate venv:  venv\Scripts\activate
echo   3. Start server:   python server.py
echo        or:           python server.py --model-path C:\path\to\model.gguf
echo        or:           python server.py --host-url http://HOST_IP:5000
echo.
pause
