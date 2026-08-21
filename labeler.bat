@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [labeler] .venv 생성 중...
  where py >nul 2>&1 && (
    py -3 -m venv .venv
  ) || (
    python -m venv .venv
  )
  if errorlevel 1 (
    echo [labeler] venv 생성 실패. Python 3 가 PATH 에 있는지 확인하세요.
    pause
    exit /b 1
  )
)

"%PY%" -c "import pypdfium2, PIL, easyocr, requests, opendataloader_pdf" >nul 2>&1
if errorlevel 1 (
  echo [labeler] 의존성 설치 중...
  "%PY%" -m pip install -U pip
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [labeler] pip 설치 실패.
    pause
    exit /b 1
  )
)

echo [labeler] VL llama-server 확인...
"%PY%" ensure_vlm_server.py
if errorlevel 1 (
  echo [경고] VL 서버 준비 실패 — 라벨러는 띄웁니다. VLM 설명 생성은 실패할 수 있습니다.
)

echo [labeler] http://localhost:8788 시작
start "" http://localhost:8788
"%PY%" server.py
