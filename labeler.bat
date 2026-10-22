@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "LLAMA_CACHE=%~dp0..\..\data\models"

:: 1. Search portable python
set "PORTABLE_PY="
for /d %%I in ("%~dp0..\python\cpython-*") do (
    if not defined PORTABLE_PY if exist "%%~fI\python.exe" (
        "%%~fI\python.exe" -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PORTABLE_PY=%%~fI\python.exe"
    )
)
if not defined PORTABLE_PY if exist "%~dp0..\python\python.exe" (
    "%~dp0..\python\python.exe" -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PORTABLE_PY=%~dp0..\python\python.exe"
)

:: 2. Check and fix .venv python
set "PY=.venv\Scripts\python.exe"
set "VENV_OK=0"

if exist "%PY%" (
    "%PY%" -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "VENV_OK=1"
    )
)

if "%VENV_OK%"=="0" if exist "%PY%" if defined PORTABLE_PY (
    echo [labeler] Restoring .venv pyvenv.cfg binding...
    for %%P in ("!PORTABLE_PY!") do set "H_HOME=%%~dpP"
    if "!H_HOME:~-1!"=="\" set "H_HOME=!H_HOME:~0,-1!"
    (
        echo home = !H_HOME!
        echo include-system-site-packages = false
        echo version = 3.13.16
        echo executable = !PORTABLE_PY!
        echo command = !PORTABLE_PY! -m venv %CD%\.venv
    ) > ".venv\pyvenv.cfg"
    "%PY%" -c "import sys" >nul 2>&1
    if not errorlevel 1 set "VENV_OK=1"
)

:: 3. Create .venv if needed
if "%VENV_OK%"=="0" (
    echo [labeler] Creating .venv ...
    if defined PORTABLE_PY (
        "!PORTABLE_PY!" -m venv .venv
    ) else (
        py -3 -m venv .venv 2>nul || python -m venv .venv
    )
    if errorlevel 1 (
        echo [labeler] Failed to create venv.
        pause
        exit /b 1
    )
)

:: 4. Check dependencies
echo [labeler] Checking dependencies ...
"%PY%" -c "import pypdfium2, PIL, requests, opendataloader_pdf, easyocr" >nul 2>&1
if errorlevel 1 (
    echo [labeler] Installing requirements ...
    "%PY%" -m pip install -U pip
    "%PY%" .tools\ensure.py gpu
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [labeler] Pip install failed.
        pause
        exit /b 1
    )
)

:: 5. Check environment (GPU, Java, VLM)
echo [labeler] Checking environment (GPU, Java, VLM) ...
"%PY%" .tools\ensure.py
if exist "%~dp0.tools\java\bin\java.exe" (
    set "JAVA_HOME=%~dp0.tools\java"
    set "PATH=%~dp0.tools\java\bin;%PATH%"
)

:: 6. Launch web UI and server
echo [labeler] Starting server at http://localhost:8788 ...
start "" "http://localhost:8788"
"%PY%" app\server.py
