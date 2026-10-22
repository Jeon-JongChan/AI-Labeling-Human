#!/usr/bin/env bash
# 라벨링 도구 독립 런처 (Windows: labeler.bat)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# llama.cpp Hugging Face 모델 캐시: local-rag/data/models 사용
export LLAMA_CACHE="$ROOT/../../data/models"

BASE_PY=""
for cand in "$ROOT"/../python/cpython-*/bin/python3 "$ROOT/../python/bin/python3"; do
  if [[ -x "$cand" ]] && "$cand" -c "import sys" >/dev/null 2>&1; then
    BASE_PY="$cand"
    break
  fi
done
[[ -z "$BASE_PY" ]] && BASE_PY="$(command -v python3 || command -v python || true)"

PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]] || ! "$PY" -c "import sys" >/dev/null 2>&1; then
  echo "[labeler] .venv 생성 중..."
  if [[ -n "$BASE_PY" ]]; then
    "$BASE_PY" -m venv .venv
  else
    echo "[labeler] ERROR: Python 인터프리터를 찾을 수 없습니다."
    exit 1
  fi
  PY="$ROOT/.venv/bin/python"
fi

if ! "$PY" -c "import pypdfium2, PIL, easyocr, requests, opendataloader_pdf" >/dev/null 2>&1; then
  echo "[labeler] 의존성 설치 중..."
  "$PY" -m pip install -U pip
  "$PY" .tools/ensure.py gpu
  "$PY" -m pip install -r requirements.txt
fi

echo "[labeler] 환경(GPU, Java, VLM) 확인 중..."
if ! "$PY" .tools/ensure.py; then
  echo "[경고] 일부 환경 준비 실패 - 라벨러는 계속 띄웁니다."
fi

if [[ -x "$ROOT/.tools/java/bin/java" ]]; then
  export JAVA_HOME="$ROOT/.tools/java"
  export PATH="$ROOT/.tools/java/bin:$PATH"
fi

echo "[labeler] http://localhost:8788 시작"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://localhost:8788" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open "http://localhost:8788" >/dev/null 2>&1 || true
fi

exec "$PY" app/server.py
