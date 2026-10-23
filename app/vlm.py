"""
VLM(비전-언어 모델) 캡셔닝 공식 진입점 및 파사드(Facade) 모듈.

vlm_provider 패키지의 어댑터(Adapter/Strategy) 및 팩토리(Factory)를 직접 호출하는
단일 창구 역할을 수행합니다.
"""

from __future__ import annotations

from pathlib import Path

import config
from config import VLM_PROMPT
from vlm_provider import (
    AutoFallbackAdapter,
    BaseVlmAdapter,
    ChainedVlmAdapter,
    ColabAiAdapter,
    FreeModelAdapter,
    GeminiAdapter,
    GptCrawlerAdapter,
    LocalVlmAdapter,
    VlmAbortError,
    VlmError,
    VlmFactory,
    VlmQuotaExceededError,
)


def caption_image(
    image_rel_path: str,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """
    [공식 진입점] 상대 경로상의 이미지를 지정된 VLM 공급자(또는 기본값)로 분석하여 설명을 생성합니다.

    Args:
        image_rel_path: config.IMAGES_DIR 기준 상대 경로
        provider: 'gemini', 'free_model', 'free', 'gemini+free', 'local', 'gpt', 'google', 'colab', 'auto' (기본값: .env 설정 참조)
        model: 특정 모델명 또는 'all' (gemini, free_model, 체인 지원)
    Returns:
        생성된 텍스트 설명
    """
    path = config.IMAGES_DIR / image_rel_path
    if not path.is_file():
        raise VlmError(f"이미지 파일이 존재하지 않습니다: {image_rel_path}")

    adapter = VlmFactory.create(provider, model=model)
    if model is not None and hasattr(adapter, "model"):
        adapter.model = model
    return adapter.caption(path)


# ---------------------------------------------------------------------------
# 기존 내부 함수 및 단위 테스트와의 100% 하위 호환성을 위한 위임(Delegation) 함수들
# ---------------------------------------------------------------------------

def _caption_colab_ai(path: Path) -> str | None:
    """Colab AI 어댑터 호출 위임."""
    try:
        return ColabAiAdapter()._generate_caption(path, VLM_PROMPT)
    except Exception:
        return None


def _caption_gemini(path: Path, api_key: str | None = None, model: str | None = None) -> str:
    """Gemini 어댑터 호출 위임."""
    return GeminiAdapter(api_key=api_key, model=model)._generate_caption(path, VLM_PROMPT)


def _caption_local(path: Path) -> str:
    """로컬 VLM 어댑터 호출 위임."""
    return LocalVlmAdapter()._generate_caption(path, VLM_PROMPT)


def _caption_gpt(path: Path) -> str:
    """GPT 크롤러 어댑터 호출 위임."""
    return GptCrawlerAdapter()._generate_caption(path, VLM_PROMPT)


def _caption_free_model(path: Path, model: str | None = None) -> str:
    """무료 VLM(FreeModel) 어댑터 호출 위임."""
    return FreeModelAdapter(model=model)._generate_caption(path, VLM_PROMPT)


__all__ = [
    "caption_image",
    "VlmFactory",
    "BaseVlmAdapter",
    "ColabAiAdapter",
    "GeminiAdapter",
    "LocalVlmAdapter",
    "GptCrawlerAdapter",
    "AutoFallbackAdapter",
    "FreeModelAdapter",
    "ChainedVlmAdapter",
    "VlmError",
    "VlmQuotaExceededError",
    "VlmAbortError",
    "_caption_colab_ai",
    "_caption_gemini",
    "_caption_local",
    "_caption_gpt",
    "_caption_free_model",
]
