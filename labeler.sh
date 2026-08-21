#!/usr/bin/env bash
# 라벨링 도구 독립 런처 (Windows: labeler.bat)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "[labeler] .venv 생성 중..."
  python3 -m venv .venv
  PY="$ROOT/.venv/bin/python"
fi

if ! "$PY" -c "import pypdfium2, PIL, easyocr, requests, opendataloader_pdf" >/dev/null 2>&1; then
  echo "[labeler] 의존성 설치 중..."
  "$PY" -m pip install -U pip
  "$PY" -m pip install -r requirements.txt
fi

echo "[labeler] VL llama-server 확인..."
if ! "$PY" ensure_vlm_server.py; then
  echo "[경고] VL 서버 준비 실패 — 라벨러는 띄웁니다. VLM 설명 생성은 실패할 수 있습니다."
fi

echo "[labeler] http://localhost:8788 시작"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://localhost:8788" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open "http://localhost:8788" >/dev/null 2>&1 || true
fi

exec "$PY" server.py
