"""
로컬/원격 OpenAI 호환 VLM 서버(llama-server, Qwen2.5-VL 등) 연동 어댑터 모듈.
"""

from __future__ import annotations

from pathlib import Path
import requests

from config import (
    VLM_API_KEY,
    VLM_BASE_URL,
    VLM_MAX_TOKENS,
    VLM_MODEL,
    VLM_TEMPERATURE,
    VLM_TIMEOUT,
    vlm_server_cmd_hint,
)
from .base import BaseVlmAdapter, VlmError


class LocalVlmAdapter(BaseVlmAdapter):
    """로컬 및 원격 OpenAI 호환 VLM 서버 어댑터."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        """
        로컬 VLM 어댑터 초기화.
        
        Args:
            base_url: VLM 서버 주소 (기본값: config.VLM_BASE_URL)
            api_key: API 인증 키 (기본값: config.VLM_API_KEY)
            model: 모델 별칭 (기본값: config.VLM_MODEL)
        """
        self.base_url = (base_url or VLM_BASE_URL).rstrip("/")
        self.api_key = api_key or VLM_API_KEY
        self.model = model if model is not None else VLM_MODEL

    @property
    def name(self) -> str:
        """어댑터 식별 명칭."""
        return "local"

    def _generate_caption(self, image_path: Path, prompt: str) -> str:
        """OpenAI 호환 /chat/completions 엔드포인트로 이미지 캡션 요청."""
        b64 = self.encode_image_b64(image_path)
        payload: dict = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
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
        if self.model:
            payload["model"] = self.model

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=VLM_TIMEOUT,
            )
        except requests.ConnectionError as exc:
            raise VlmError(
                f"로컬 VLM 서버({self.base_url})에 연결할 수 없습니다. "
                "labeler.bat|sh 또는 tools/labeler/vlm_server.py 기동 필요: "
                f"{vlm_server_cmd_hint()}"
            ) from exc
        except requests.Timeout as exc:
            raise VlmError(f"로컬 VLM 응답 시간 초과 ({VLM_TIMEOUT:.0f}초)") from exc

        if resp.status_code != 200:
            raise VlmError(f"로컬 VLM HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise VlmError("로컬 VLM 응답에 choices 없음")

        message = choices[0].get("message") or {}
        content = (message.get("content") or "").strip()
        if not content:
            content = (message.get("reasoning_content") or "").strip()
        if not content:
            raise VlmError("로컬 VLM 응답 본문이 비어 있음")

        return content

