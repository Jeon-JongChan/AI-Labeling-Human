"""
라벨링 도구 설정 — 경로·서버·OCR·VLM 은 여기만 보면 됩니다.

독립 사용:
  1) 아래 DOCS_DIR / LABEL_DIR 등에 값을 직접 넣거나
  2) 환경 변수로 지정 (직접 설정보다 우선)
  3) 이 폴더의 labeler.bat / labeler.sh 로 실행

local-rag 의 tools/labeler 로 있으면 상위 data/docs, data/labeling 을 기본으로 씁니다.
단독 클론이면 이 폴더 아래 data/docs, data/labeling 을 씁니다.

VLM llama-server:
  labeler.bat|sh → ensure_vlm_server.py 가 VLM_PORT 를 보고,
  없으면 llama-cpp 설치 후 -hf VLM_HF_REPO 로 기동합니다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

TOOL_DIR = Path(__file__).resolve().parent


def _default_project_root() -> Path:
    """local-rag 안이면 그 루트, 아니면 이 폴더(독립 배포)."""
    if TOOL_DIR.parent.name == "tools":
        candidate = TOOL_DIR.parent.parent
        if (candidate / "scripts").is_dir() and (
            (candidate / "app").is_dir() or (candidate / "requirements.txt").is_file()
        ):
            return candidate
    return TOOL_DIR


_PROJECT_ROOT = _default_project_root()
# ---------------------------------------------------------------------------
# 경로·웹 서버 — 독립 실행 시 여기만 바꿔도 됩니다 (None = 기본 경로)
# ---------------------------------------------------------------------------

DOCS_DIR: Optional[Path] = None
# 예: DOCS_DIR = Path(r"D:\my-pdfs")

LABEL_DIR: Optional[Path] = None
# 예: LABEL_DIR = Path(r"D:\labeling-out")

HOST = "0.0.0.0"
PORT = 8788

# ---------------------------------------------------------------------------
# OCR (EasyOCR)
# ---------------------------------------------------------------------------

OCR_LANGS: List[str] = ["ko", "en"]
OCR_GPU = False

# ---------------------------------------------------------------------------
# VLM (llama-server 멀티모달 — 별도 프로세스를 사용자가 실행)
# ---------------------------------------------------------------------------

# Hugging Face 레포 (-hf). 모델 바꾸면 여기와 실행 명령을 같이 맞춤.
VLM_HF_REPO = "ggml-org/Qwen2.5-VL-3B-Instruct-GGUF"
VLM_PORT = 8090

VLM_BASE_URL = f"http://localhost:{VLM_PORT}/v1"
VLM_API_KEY = "no-key"
VLM_MODEL = ""  # 서버 alias (보통 비움)
VLM_TIMEOUT = 300.0
VLM_MAX_TOKENS = 1024
VLM_TEMPERATURE = 0.2

VLM_PROMPT = (
    "이 이미지는 기술 문서(PDF)에서 추출한 그림 또는 표입니다. "
    "검색용 텍스트로 쓸 수 있도록 한국어로 설명하세요.\n"
    "- 다이어그램이면: 구성요소 이름과 연결 관계(무엇이 무엇과 어떻게 연결되는지)를 빠짐없이\n"
    "- 표이면: 행·열 구조와 핵심 값\n"
    "- 이미지에 보이는 글자·수치·단위는 그대로 인용\n"
    "- 보이지 않는 내용을 추측하지 말 것. 설명만 출력하고 서두·맺음말 생략."
)

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


def _env_or(name: str, current: str) -> str:
    val = os.getenv(name, "").strip()
    return val if val else current


DOCS_DIR = _resolve(DOCS_DIR, "LABELER_DOCS_DIR", _PROJECT_ROOT / "data" / "docs")
LABEL_DIR = _resolve(LABEL_DIR, "LABELER_DATA_DIR", _PROJECT_ROOT / "data" / "labeling")

_host_env = os.getenv("LABELER_HOST", "").strip()
if _host_env:
    HOST = _host_env

_port_env = os.getenv("LABELER_PORT", "").strip()
if _port_env:
    PORT = int(_port_env)

_vlm_port_env = os.getenv("VLM_PORT", "").strip()
if _vlm_port_env:
    VLM_PORT = int(_vlm_port_env)
    # 포트만 env로 바꾼 경우 BASE_URL 기본도 맞춤 (VLM_BASE_URL env가 있으면 그쪽 우선)
    if not os.getenv("VLM_BASE_URL", "").strip():
        VLM_BASE_URL = f"http://localhost:{VLM_PORT}/v1"

VLM_HF_REPO = _env_or("VLM_HF_REPO", VLM_HF_REPO)
VLM_BASE_URL = _env_or("VLM_BASE_URL", VLM_BASE_URL).rstrip("/")
VLM_API_KEY = _env_or("VLM_API_KEY", VLM_API_KEY)
VLM_MODEL = _env_or("VLM_MODEL", VLM_MODEL)

_timeout_env = os.getenv("VLM_TIMEOUT", "").strip()
if _timeout_env:
    VLM_TIMEOUT = float(_timeout_env)

_max_tokens_env = os.getenv("VLM_MAX_TOKENS", "").strip()
if _max_tokens_env:
    VLM_MAX_TOKENS = int(_max_tokens_env)

IMAGES_DIR = LABEL_DIR / "images"
WORK_DIR = LABEL_DIR / "odl-work"
DB_PATH = LABEL_DIR / "labeler.sqlite3"


def vlm_server_cmd_hint() -> str:
    """사용자가 수동으로 띄울 VL llama-server 예시 명령."""
    return f"llama-server -hf {VLM_HF_REPO} --port {VLM_PORT}"


def ensure_dirs() -> None:
    """실행에 필요한 디렉터리를 만듭니다."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
