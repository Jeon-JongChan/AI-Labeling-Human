"""
VLM(비전-언어 모델) 캡셔닝 — llama-server 멀티모달 (OpenAI 호환 API).

그림·표 크롭 이미지를 VL 모델에 보내 한국어 설명을 생성합니다.
OCR이 못 잡는 다이어그램의 연결 관계·구조를 텍스트로 만들어
이후 인제스트(텍스트 임베딩)에 쓸 수 있게 합니다.

VL 서버 실행 예 (모델+mmproj 자동 다운로드, 별도 포트):
  llama-server -hf ggml-org/Qwen2.5-VL-3B-Instruct-GGUF --port 8090

환경 변수:
  VLM_BASE_URL  기본 http://localhost:8090/v1
  VLM_API_KEY   기본 no-key
  VLM_MODEL     모델 alias (보통 비워둠)
"""

from __future__ import annotations

import base64
import os

import requests

from config import IMAGES_DIR

VLM_BASE_URL = os.getenv("VLM_BASE_URL", "http://localhost:8090/v1").rstrip("/")
VLM_API_KEY = os.getenv("VLM_API_KEY", "no-key")
VLM_MODEL = os.getenv("VLM_MODEL", "").strip()
VLM_TIMEOUT = float(os.getenv("VLM_TIMEOUT", "300"))
VLM_MAX_TOKENS = int(os.getenv("VLM_MAX_TOKENS", "1024"))

_PROMPT = (
    "이 이미지는 기술 문서(PDF)에서 추출한 그림 또는 표입니다. "
    "검색용 텍스트로 쓸 수 있도록 한국어로 설명하세요.\n"
    "- 다이어그램이면: 구성요소 이름과 연결 관계(무엇이 무엇과 어떻게 연결되는지)를 빠짐없이\n"
    "- 표면: 행·열 구조와 핵심 값\n"
    "- 이미지에 보이는 글자·수치·단위는 그대로 인용\n"
    "- 보이지 않는 내용을 추측하지 말 것. 설명만 출력하고 서두·맺음말 생략."
)


class VlmError(RuntimeError):
    pass


def caption_image(image_rel_path: str) -> str:
    """크롭 이미지(IMAGES_DIR 상대 경로)를 VL 모델로 설명. 실패 시 VlmError."""
    path = IMAGES_DIR / image_rel_path
    if not path.is_file():
        raise VlmError(f"이미지 파일 없음: {image_rel_path}")

    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    payload: dict = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
        "temperature": 0.2,
        "max_tokens": VLM_MAX_TOKENS,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
        "enable_thinking": False,
    }
    if VLM_MODEL:
        payload["model"] = VLM_MODEL

    try:
        resp = requests.post(
            f"{VLM_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {VLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=VLM_TIMEOUT,
        )
    except requests.ConnectionError as exc:
        raise VlmError(
            f"VLM 서버({VLM_BASE_URL})에 연결할 수 없습니다. "
            "VL 모델 llama-server가 실행 중인지 확인하세요. "
            "(예: llama-server -hf ggml-org/Qwen2.5-VL-3B-Instruct-GGUF --port 8090)"
        ) from exc
    except requests.Timeout as exc:
        raise VlmError(f"VLM 응답 시간 초과 ({VLM_TIMEOUT:.0f}초)") from exc

    if resp.status_code != 200:
        raise VlmError(f"VLM HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise VlmError("VLM 응답에 choices 없음")

    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        content = (message.get("reasoning_content") or "").strip()
    if not content:
        raise VlmError("VLM 응답 본문이 비어 있음")
    return content
