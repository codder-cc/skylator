@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1

set CMAKE_ARGS=-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=120
set CUDACXX=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin\nvcc.exe
set PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin;C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;%PATH%

echo CMAKE_ARGS=%CMAKE_ARGS% >> H:\Nolvus\Translator\build_log.txt
echo Starting pip install... >> H:\Nolvus\Translator\build_log.txt

H:\Nolvus\Translator\venv\Scripts\pip install llama-cpp-python --upgrade --no-binary :all: --force-reinstall >> H:\Nolvus\Translator\build_log.txt 2>&1

echo Exit: %ERRORLEVEL% >> H:\Nolvus\Translator\build_log.txt
