"""
VLM Provider 패키지.

디자인 패턴:
- Adapter / Strategy: 공급자별 상이한 API를 BaseVlmAdapter 인터페이스로 일원화
- Template Method: BaseVlmAdapter.caption()을 통한 공통 전후처리 파이프라인
- Factory Method: VlmFactory를 통한 어댑터 동적 생성 및 등록(Registry) 지원
"""

from .base import (
    BaseVlmAdapter,
    VlmAbortError,
    VlmError,
    VlmQuotaExceededError,
)
from .colab import ColabAiAdapter
from .gemini import GeminiAdapter
from .local import LocalVlmAdapter
from .gpt import GptCrawlerAdapter
from .google import GoogleCrawlerAdapter
from .auto import AutoFallbackAdapter
from .factory import VlmFactory

__all__ = [
    "BaseVlmAdapter",
    "VlmError",
    "VlmQuotaExceededError",
    "VlmAbortError",
    "ColabAiAdapter",
    "GeminiAdapter",
    "LocalVlmAdapter",
    "GptCrawlerAdapter",
    "GoogleCrawlerAdapter",
    "AutoFallbackAdapter",
    "VlmFactory",
]
