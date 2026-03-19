@echo off
echo === Nolvus Translator - venv setup ===
cd /d "%~dp0"

echo [1/4] Creating virtual environment...
python -m venv venv
call venv\Scripts\activate.bat

echo [2/4] Installing PyTorch with CUDA 12.8 (RTX 5080 Blackwell)...
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

echo [3/4] Installing remaining dependencies...
pip install -r requirements.txt

echo [4/4] Installing package in editable mode...
pip install -e .

echo.
echo === Setup complete! ===
echo To activate venv: call venv\Scripts\activate.bat
echo.
echo CLI usage:
echo   nolvus-translate translate-mod "ModName"
echo.
echo Web UI:
echo   python web_server.py
echo   Open: http://127.0.0.1:5000
pause
