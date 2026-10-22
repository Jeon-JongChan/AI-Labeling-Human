"""
Google Gemini REST API 연동 어댑터 모듈.
"""

from __future__ import annotations

import time
from pathlib import Path
import requests

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    VLM_MAX_TOKENS,
    VLM_TEMPERATURE,
    VLM_TIMEOUT,
)
from .base import BaseVlmAdapter, VlmError, VlmQuotaExceededError


def _try_get_colab_key() -> str:
    """Colab 환경의 Secrets(userdata)에 GEMINI_API_KEY가 저장되어 있는지 확인."""
    try:
        from google.colab import userdata  # type: ignore
        return userdata.get("GEMINI_API_KEY") or ""
    except Exception:
        return ""


class GeminiAdapter(BaseVlmAdapter):
    """Google Gemini REST API를 활용한 VLM 어댑터 (무료 일일 1500회 티어 지원)."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        """
        Gemini 어댑터 초기화.
        
        Args:
            api_key: Gemini API 키 (미지정 시 환경변수 또는 Colab Secrets 참조)
            model: 기본 Gemini 모델명 (미지정 시 config.GEMINI_MODEL)
        """
        self.api_key = api_key or GEMINI_API_KEY or _try_get_colab_key()
        self.model = model or GEMINI_MODEL

    @property
    def name(self) -> str:
        """어댑터 식별 명칭."""
        return "gemini"

    def _generate_caption(self, image_path: Path, prompt: str) -> str:
        """Gemini REST API로 이미지 캡션 요청."""
        key = self.api_key or GEMINI_API_KEY or _try_get_colab_key()
        if not key:
            raise VlmError("GEMINI_API_KEY 가 설정되지 않았습니다.")

        b64 = self.encode_image_b64(image_path)
        candidate_models = [
            self.model,
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3.5-flash",
            "gemini-flash-latest",
        ]
        seen = set()
        models_to_try = [m for m in candidate_models if m and not (m in seen or seen.add(m))]

        last_error = None
        for model_name in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": "image/png",
                                    "data": b64,
                                }
                            },
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": VLM_TEMPERATURE,
                    "maxOutputTokens": VLM_MAX_TOKENS,
                },
            }

            try:
                resp = requests.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=VLM_TIMEOUT,
                )
            except requests.RequestException as exc:
                last_error = f"Gemini API 연결 실패: {exc}"
                continue

            if resp.status_code == 429:
                # 15 RPM 한도 초과 시 5초 대기 후 1회 재시도
                time.sleep(5)
                try:
                    resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=VLM_TIMEOUT)
                except Exception:
                    pass
                if resp.status_code == 429:
                    raise VlmQuotaExceededError(
                        "Gemini API 사용량 한도 초과 (HTTP 429: Rate Limit / Quota Exceeded)"
                    )
            if resp.status_code in (404, 503):
                last_error = f"Gemini {model_name} HTTP {resp.status_code}: {resp.text[:200]}"
                continue
            if resp.status_code != 200:
                raise VlmError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")

            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise VlmError(f"Gemini 응답에 candidates 없음: {data}")

            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise VlmError("Gemini 응답 내용(parts)이 비어 있음")

            return parts[0].get("text", "").strip()

        raise VlmError(f"Gemini 모델 호출 실패 (모든 모델 시도함): {last_error}")

