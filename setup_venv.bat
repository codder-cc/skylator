@echo off
echo === Skylator - Setup ===
cd /d "%~dp0"

echo [1/5] Creating Python virtual environment...
python -m venv venv
call venv\Scripts\activate.bat

echo [2/5] Installing PyTorch with CUDA 12.8 (RTX 5080 Blackwell)...
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

echo [3/5] Installing remaining Python dependencies...
pip install -r requirements.txt

echo [4/5] Installing package in editable mode...
pip install -e .

echo [5/5] Installing frontend (Node) dependencies...
pushd frontend
npm install
popd

echo.
echo === Setup complete! ===
echo.
echo  Production:    start_server.bat   (builds frontend, serves /app/)
echo  Development:   dev.bat            (Vite HMR + Flask, open :5173/app/)
echo.
echo  App URL:  http://127.0.0.1:5000/app/
echo.
pause
