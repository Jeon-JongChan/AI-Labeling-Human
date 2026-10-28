"""
라벨링 도구 설정 — 경로·서버·OCR·VLM 은 여기만 보면 됩니다.

독립 사용:
  1) 아래 DOCS_DIR / LABEL_DIR 등에 값을 직접 넣거나
  2) 환경 변수로 지정 (직접 설정보다 우선)
  3) 이 폴더의 labeler.bat / labeler.sh 로 실행

local-rag 의 tools/labeler 로 있으면 상위 data/docs, data/labeling 을 기본으로 씁니다.
단독 클론이면 이 폴더 아래 data/docs, data/labeling 을 씁니다.

VLM llama-server:
  labeler.bat|sh → .tools/ensure.py (vlm) 가 VLM_PORT 를 보고,
  사용자 확인 후 llama-cpp 설치와 -hf VLM_HF_REPO 기동을 진행합니다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

APP_DIR = Path(__file__).resolve().parent
TOOL_DIR = APP_DIR.parent


def _default_project_root() -> Path:
    """local-rag 안이면 그 루트, 아니면 이 폴더(독립 배포)."""
    if TOOL_DIR.parent.name == "tools":
        candidate = TOOL_DIR.parent.parent
        if (candidate / "data").is_dir() or (candidate / "scripts").is_dir() or (
            (candidate / "app").is_dir() or (candidate / "requirements.txt").is_file()
        ):
            return candidate
    return TOOL_DIR


_PROJECT_ROOT = _default_project_root()


def _load_env_file() -> None:
    """tools/labeler/.env 또는 프로젝트 루트 .env 파일을 찾아 환경변수에 로드 (멀티라인 배열 지원)."""
    candidates = [
        TOOL_DIR / ".env",
        _PROJECT_ROOT / ".env",
    ]
    for env_path in candidates:
        if env_path.is_file():
            try:
                current_key = None
                current_val_parts = []
                in_multiline = False

                for line in env_path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not in_multiline:
                        if not stripped or stripped.startswith("#") or "=" not in stripped:
                            continue
                        key, val = stripped.split("=", 1)
                        key = key.strip()
                        val = val.strip()

                        # 괄호나 따옴표가 열린 채 다음 줄로 이어지는 멀티라인 배열/문자열 감지
                        if (val.startswith("[") and not val.endswith("]")) or (val.startswith("{") and not val.endswith("}")):
                            current_key = key
                            current_val_parts = [val]
                            in_multiline = True
                        else:
                            clean_val = val.strip("'\"")
                            if key and key not in os.environ:
                                os.environ[key] = clean_val
                    else:
                        current_val_parts.append(stripped)
                        combined = " ".join(current_val_parts)
                        if stripped.endswith("]") or stripped.endswith("}"):
                            in_multiline = False
                            clean_val = combined.strip().strip("'\"")
                            if current_key and current_key not in os.environ:
                                os.environ[current_key] = clean_val
                            current_key = None
                            current_val_parts = []
            except Exception:
                pass


_load_env_file()
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
# PDF 파싱 (문서 구조 및 텍스트/이미지 추출) 설정
# ---------------------------------------------------------------------------

EXTRACT_ENGINE = os.getenv("EXTRACT_ENGINE", "auto").strip().lower()
PDF_RENDER_SCALE = float(os.getenv("PDF_RENDER_SCALE", "2.0"))

# ---------------------------------------------------------------------------
# OCR (EasyOCR) 및 하드웨어 가속 설정
# ---------------------------------------------------------------------------

OCR_LANGS: List[str] = ["ko", "en"]

# GPU 우선 설정 (기본값 True: CUDA 사용 가능 시 GPU 가속, 불가 또는 실패 시 CPU 자동 폴백)
OCR_GPU = True
_ocr_gpu_env = os.getenv("OCR_GPU", os.getenv("LABELER_OCR_GPU", "")).strip().lower()
if _ocr_gpu_env in ("0", "false", "no"):
    OCR_GPU = False
elif _ocr_gpu_env in ("1", "true", "yes"):
    OCR_GPU = True
else:
    # 기본값은 GPU 우선 사용
    OCR_GPU = True

# CPU 모드로 실행될 때 사용할 최대 CPU 스레드 수 (기본 2~4개로 제한하여 CPU 점유율 100% 폭주 방지)
OCR_CPU_THREADS = int(os.getenv("OCR_CPU_THREADS", os.getenv("LABELER_CPU_THREADS", "2")))

# 전역 디바이스 기본값 ("cuda" 우선, 불가 시 "cpu")
def _detect_device() -> str:
    env_dev = os.getenv("LABELER_DEVICE", os.getenv("DEVICE", "")).strip().lower()
    if env_dev in ("cuda", "gpu"):
        return "cuda"
    if env_dev == "cpu":
        return "cpu"
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"

DEVICE = _detect_device()

# ---------------------------------------------------------------------------
# VLM (llama-server 멀티모달 — 별도 프로세스를 사용자가 실행)
# ---------------------------------------------------------------------------

def parse_gemini_api_keys(raw: str | None = None) -> List[str]:
    """
    환경변수나 문자열에서 복수 Gemini API 키 목록을 추출하여 리스트로 반환합니다.

    - 쉼표(,), 세미콜론(;), 줄바꿈, 공백 구분자 지원
    - JSON 배열 및 파이썬 리스트 포맷 지원 (예: '["key1", "key2"]', "['key1', 'key2']", "[key1, key2]")
    - 중복 키 자동 제거 및 입력 순서 보존
    """
    if raw is None:
        raw = os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY", "")
    raw = raw.strip()
    if not raw:
        return []

    # 1. 배열 포맷 (JSON / Python 리스트 형태: ["key1", "key2"] 또는 ['key1', 'key2'])
    if raw.startswith("[") and raw.endswith("]"):
        try:
            import json
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(k).strip().strip("'\"") for k in parsed if str(k).strip().strip("'\"")]
        except Exception:
            pass
        try:
            import ast
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, (list, tuple)):
                return [str(k).strip().strip("'\"") for k in parsed if str(k).strip().strip("'\"")]
        except Exception:
            pass

    # 2. 일반 텍스트 및 따옴표 없는 배열([key1, key2]) 처리
    clean_raw = raw.strip().strip("[]()")
    delimiters = [",", ";", "\n", "\r"]
    text_clean = clean_raw
    for d in delimiters:
        text_clean = text_clean.replace(d, " ")
    keys = [k.strip().strip("'\"") for k in text_clean.split() if k.strip().strip("'\"")]
    seen = set()
    return [k for k in keys if not (k in seen or seen.add(k))]


GEMINI_API_KEYS: List[str] = parse_gemini_api_keys()
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
GEMINI_SLEEP_INTERVAL = float(os.getenv("GEMINI_SLEEP_INTERVAL", "3600.0"))
VLM_CHAIN_FALLBACK_ON = os.getenv("VLM_CHAIN_FALLBACK_ON", "limit").strip().lower()
VLM_CHAIN_SLEEP_INTERVAL = float(os.getenv("VLM_CHAIN_SLEEP_INTERVAL", "3600.0"))


def _detect_vlm_provider() -> str:
    """가용한 설정을 바탕으로 기본 VLM 공급자를 자동 판별합니다."""
    explicit = os.getenv("VLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if GEMINI_API_KEYS or GEMINI_API_KEY:
        return "gemini"
    base_url = os.getenv("VLM_BASE_URL", "").strip().lower()
    if base_url and not any(h in base_url for h in ("localhost", "127.0.0.1", "0.0.0.0")):
        return "colab"
    return "local"

VLM_PROVIDER = _detect_vlm_provider()

# VLM_PROVIDER=gpt 일 때 사용하는 ChatGPT 웹 크롤러 서버 (.tools/auto-query-web/gpt.py)
GPT_CRAWLER_URL = os.getenv("GPT_CRAWLER_URL", "http://127.0.0.1:8791").strip().rstrip("/")

# VLM_PROVIDER=google 일 때 사용하는 Google AI 모드 크롤러 서버 (.tools/auto-query-web/google.py)
GOOGLE_CRAWLER_URL = os.getenv("GOOGLE_CRAWLER_URL", "http://127.0.0.1:8792").strip().rstrip("/")

VLM_HF_REPO = "ggml-org/Qwen2.5-VL-3B-Instruct-GGUF"
VLM_PORT = 8090

VLM_BASE_URL = f"http://localhost:{VLM_PORT}/v1"
VLM_API_KEY = "no-key"
VLM_MODEL = ""  # 서버 alias (보통 비움)
VLM_TIMEOUT = 300.0
VLM_MAX_TOKENS = 1536
VLM_TEMPERATURE = 0.1

VLM_PROMPT = (
    "이 이미지는 기술 문서에서 추출한 이미지 또는 표입니다. 아래 규칙에 따라 검색용 텍스트를 추출하세요.\n\n"
    "[1. 노이즈/장식 이미지]\n"
    "- 단순 로고, 회사 마크, 서명, 구분선, 장식용 그래픽 등 정보 가치가 없는 이미지라면 정확히 '[내용 없음(장식/로고)]' 라고만 출력하고 끝내세요.\n\n"
    "[2. 표(Table)인 경우]\n"
    "- '무슨 표이다'라는 메타 설명만 하지 말고, 표의 헤더와 각 행의 핵심 데이터/값/수치/단위를 빠짐없이 마크다운 표 형태나 목록으로 직접 인용해 적으세요.\n\n"
    "[3. 다이어그램/그림(Figure)인 경우]\n"
    "- 이미지에 실제로 적힌 텍스트, 블록 이름, 수치를 빠짐없이 읽으세요.\n"
    "- 연결 관계나 흐름은 'A -> B' 형태로 간결하게 불릿포인트로 요약하세요.\n\n"
    "[공통 금지 사항 - 엄격 준수]\n"
    "- 챗봇식 대화 문구 절대 금지: 서두('이 이미지는...', '제시된 이미지는...'), 맺음말, 인사, 사족을 절대 붙이지 마세요.\n"
    "- 질문 유도 및 제안 문구 금지: '~가 필요하시면 말씀해 주세요', '도움이 필요하시면 알려주세요', '궁금한 점이 있으시면...' 등의 대화 유도 멘트를 절대 적지 마세요.\n"
    "- 오직 이미지에서 직접 확인되는 사실 데이터(텍스트/수치/표)만 군더더기 없이 출력하세요."
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

# ---------------------------------------------------------------------------
# 미세 노이즈/장식 이미지 필터링 임계값 (PDF pt 및 렌더링 픽셀 단위)
# ---------------------------------------------------------------------------
MIN_IMAGE_WIDTH_PT = float(os.getenv("MIN_IMAGE_WIDTH_PT", "20.0"))
MIN_IMAGE_HEIGHT_PT = float(os.getenv("MIN_IMAGE_HEIGHT_PT", "20.0"))
MIN_IMAGE_WIDTH_PX = int(os.getenv("MIN_IMAGE_WIDTH_PX", "40"))
MIN_IMAGE_HEIGHT_PX = int(os.getenv("MIN_IMAGE_HEIGHT_PX", "40"))


def is_small_image(
    kind: str,
    bbox: Any = None,
    image_path: Path | str | None = None,
    min_pt_w: float = MIN_IMAGE_WIDTH_PT,
    min_pt_h: float = MIN_IMAGE_HEIGHT_PT,
    min_px_w: int = MIN_IMAGE_WIDTH_PX,
    min_px_h: int = MIN_IMAGE_HEIGHT_PX,
) -> bool:
    """
    이미지 항목이 너무 작아 유효한 도면/표가 아닌 미세 장식/선/아이콘인지 판별.

    - kind != 'image' (예: table)인 경우 표 구조 보존을 위해 False 반환.
    - bbox 정보([x0, y0, x1, y1])가 있는 경우 pt 기준 검사 (가로 < min_pt_w 또는 세로 < min_pt_h).
    - bbox가 없고 이미지 파일이 존재하면 이미지 픽셀 크기 검사 (가로 < min_px_w 또는 세로 < min_px_h).
    """
    if kind != "image":
        return False

    if bbox:
        try:
            import json

            b = json.loads(bbox) if isinstance(bbox, str) else bbox
            if isinstance(b, (list, tuple)) and len(b) >= 4:
                w = abs(float(b[2]) - float(b[0]))
                h = abs(float(b[3]) - float(b[1]))
                return w < min_pt_w or h < min_pt_h
        except Exception:
            pass

    if image_path:
        try:
            from PIL import Image

            p = Path(image_path)
            if p.exists():
                with Image.open(p) as im:
                    return im.width < min_px_w or im.height < min_px_h
        except Exception:
            pass

    return False


def vlm_server_cmd_hint() -> str:
    """사용자가 수동으로 띄울 VL llama-server 예시 명령."""
    return f"llama-server -hf {VLM_HF_REPO} --port {VLM_PORT}"


def ensure_dirs() -> None:
    """실행에 필요한 디렉터리를 만듭니다."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
