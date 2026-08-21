"""
라벨링 도구 설정.

독립 사용:
  1) 아래 DOCS_DIR / LABEL_DIR 에 Path 를 직접 넣거나
  2) 환경 변수로 지정 (직접 설정보다 우선)
       LABELER_DOCS_DIR=D:\\pdfs
       LABELER_DATA_DIR=D:\\labeling-out
       LABELER_HOST=0.0.0.0
       LABELER_PORT=8788

local-rag 안에서 None 그대로 두면 data/docs, data/labeling 을 씁니다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

TOOL_DIR = Path(__file__).resolve().parent
# tools/labeler → 프로젝트 루트 (폴더만 따로 복사했다면 DOCS_DIR/LABEL_DIR 을 직접 지정)
_PROJECT_ROOT = TOOL_DIR.parent.parent

# ---------------------------------------------------------------------------
# 사용자 설정 — 독립 실행 시 여기만 바꿔도 됩니다 (None = 기본 경로)
# ---------------------------------------------------------------------------

DOCS_DIR: Optional[Path] = None
# 예: DOCS_DIR = Path(r"D:\my-pdfs")

LABEL_DIR: Optional[Path] = None
# 예: LABEL_DIR = Path(r"D:\labeling-out")

HOST = "0.0.0.0"
PORT = 8788

# ---------------------------------------------------------------------------
# 해석: 환경 변수 > 위 직접 설정 > local-rag 기본값
# ---------------------------------------------------------------------------


def _resolve(override: Optional[Path], env_name: str, default: Path) -> Path:
    env_val = os.getenv(env_name, "").strip()
    if env_val:
        return Path(env_val).expanduser().resolve()
    if override is not None:
        return Path(override).expanduser().resolve()
    return default.expanduser().resolve()


DOCS_DIR = _resolve(DOCS_DIR, "LABELER_DOCS_DIR", _PROJECT_ROOT / "data" / "docs")
LABEL_DIR = _resolve(LABEL_DIR, "LABELER_DATA_DIR", _PROJECT_ROOT / "data" / "labeling")

_host_env = os.getenv("LABELER_HOST", "").strip()
if _host_env:
    HOST = _host_env

_port_env = os.getenv("LABELER_PORT", "").strip()
if _port_env:
    PORT = int(_port_env)

IMAGES_DIR = LABEL_DIR / "images"
WORK_DIR = LABEL_DIR / "odl-work"
DB_PATH = LABEL_DIR / "labeler.sqlite3"


def ensure_dirs() -> None:
    """실행에 필요한 디렉터리를 만듭니다."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
