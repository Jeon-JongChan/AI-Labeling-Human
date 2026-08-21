"""
VLM(비전-언어 모델) 캡셔닝 — llama-server 멀티모달 (OpenAI 호환 API).

설정은 config.py. VL 서버 기동은 scripts/ensure_vlm_server.py
(labeler.bat|sh 가 호출)가 담당합니다.
"""

from __future__ import annotations

import base64

import requests

from config import (
    IMAGES_DIR,
    VLM_API_KEY,
    VLM_BASE_URL,
    VLM_MAX_TOKENS,
    VLM_MODEL,
    VLM_PROMPT,
    VLM_TEMPERATURE,
    VLM_TIMEOUT,
    vlm_server_cmd_hint,
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
                    {"type": "text", "text": VLM_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
        "temperature": VLM_TEMPERATURE,
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
            "labeler.bat|sh 로 VL 기동을 확인하거나 "
            f"수동 실행: {vlm_server_cmd_hint()}"
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
