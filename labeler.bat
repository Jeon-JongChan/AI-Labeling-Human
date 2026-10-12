@echo off
setlocal
cd /d "%~dp0"

set "LLAMA_CACHE=%~dp0..\..\data\models"
set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [labeler] Creating .venv ...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo [labeler] Failed to create venv.
    pause
    exit /b 1
  )
)

echo [labeler] Checking dependencies ...
"%PY%" -c "import pypdfium2, PIL, requests, opendataloader_pdf, easyocr" >nul 2>&1
if errorlevel 1 (
  echo [labeler] Installing requirements ...
  "%PY%" -m pip install -U pip
  "%PY%" ensure_gpu.py
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [labeler] Pip install failed.
    pause
    exit /b 1
  )
)

echo [labeler] Checking GPU ...
"%PY%" ensure_gpu.py

echo [labeler] Checking Java ...
"%PY%" ensure_java.py
if exist "%~dp0java\bin\java.exe" (
  set "JAVA_HOME=%~dp0java"
  set "PATH=%~dp0java\bin;%PATH%"
)

echo [labeler] Checking VLM ...
"%PY%" ensure_vlm_server.py

echo [labeler] Starting server at http://localhost:8788 ...
start "" "http://localhost:8788"
"%PY%" server.py
