"""
VLM 공급자 팩토리(Factory) 모듈.

공급자 명칭(gemini, local, gpt, colab, auto 등)에 따라 적합한 어댑터 객체를 생성 및 제공합니다.
새로운 VLM 공급자가 추가되어도 레지스트리에 등록(register)하면 기존 코드를 수정할 필요가 없습니다.
"""

from __future__ import annotations

from typing import Dict, List, Type

from .base import BaseVlmAdapter, VlmError
from .colab import ColabAiAdapter
from .gemini import GeminiAdapter
from .local import LocalVlmAdapter
from .gpt import GptCrawlerAdapter
from .auto import AutoFallbackAdapter


class VlmFactory:
    """
    VLM 공급자 어댑터 팩토리 클래스 (Factory Method & Registry 패턴).
    """

    _registry: Dict[str, Type[BaseVlmAdapter]] = {
        "gemini": GeminiAdapter,
        "local": LocalVlmAdapter,
        "gpt": GptCrawlerAdapter,
        "colab": ColabAiAdapter,
        "auto": AutoFallbackAdapter,
    }

    @classmethod
    def register(cls, name: str, adapter_cls: Type[BaseVlmAdapter]) -> None:
        """
        새로운 VLM 공급자 어댑터를 팩토리 레지스트리에 등록합니다.
        
        Args:
            name: 공급자 식별 이름 (예: 'claude', 'ollama')
            adapter_cls: BaseVlmAdapter를 상속받은 어댑터 클래스
        """
        clean_name = name.lower().strip()
        cls._registry[clean_name] = adapter_cls

    @classmethod
    def list_providers(cls) -> List[str]:
        """현재 등록된 모든 VLM 공급자 이름 목록을 반환합니다."""
        return sorted(cls._registry.keys())

    @classmethod
    def create(cls, provider: str | None = None) -> BaseVlmAdapter:
        """
        요청된 공급자 이름에 해당하는 VLM 어댑터 인스턴스를 생성하여 반환합니다.
        
        Args:
            provider: 공급자 이름 (미지정 시 config.VLM_PROVIDER 또는 GEMINI_API_KEY 유무에 따라 결정)
        Returns:
            생성된 BaseVlmAdapter 인스턴스
        """
        from config import GEMINI_API_KEY, VLM_PROVIDER

        raw_name = provider or VLM_PROVIDER
        if not raw_name:
            raw_name = "gemini" if GEMINI_API_KEY else "local"

        name = raw_name.lower().strip()
        adapter_cls = cls._registry.get(name)

        if not adapter_cls:
            available = ", ".join(cls.list_providers())
            raise VlmError(
                f"지원하지 않는 VLM 공급자입니다: '{name}'. "
                f"사용 가능한 공급자: [{available}]"
            )

        return adapter_cls()

