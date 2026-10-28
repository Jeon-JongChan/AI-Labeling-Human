"""
VLM 공급자 팩토리(Factory) 모듈.

공급자 명칭(gemini, local, gpt, colab, auto, free, free_model, gemini+free 등)에 따라 적합한 어댑터 객체를 생성 및 제공합니다.
새로운 VLM 공급자가 추가되어도 레지스트리에 등록(register)하면 기존 코드를 수정할 필요가 없습니다.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Type

from .base import BaseVlmAdapter, VlmError
from .colab import ColabAiAdapter
from .gemini import GeminiAdapter
from .local import LocalVlmAdapter
from .gpt import GptCrawlerAdapter
from .google import GoogleCrawlerAdapter
from .auto import AutoFallbackAdapter
from .free_model import FreeModelAdapter
from .chain import ChainedVlmAdapter


class VlmFactory:
    """
    VLM 공급자 어댑터 팩토리 클래스 (Factory Method & Registry 패턴).
    """

    _registry: Dict[str, Type[BaseVlmAdapter]] = {
        "gemini": GeminiAdapter,
        "local": LocalVlmAdapter,
        "gpt": GptCrawlerAdapter,
        "google": GoogleCrawlerAdapter,
        "colab": ColabAiAdapter,
        "auto": AutoFallbackAdapter,
        "free_model": FreeModelAdapter,
        "free": FreeModelAdapter,
        "chain": ChainedVlmAdapter,
    }

    # 공급자/모델별 활성 인스턴스 캐시 (작동 모델 및 소진 상태를 연속 요청에서 유지)
    _instances: Dict[tuple, BaseVlmAdapter] = {}

    @classmethod
    def reset_instances(cls) -> None:
        """어댑터 인스턴스 캐시를 초기화합니다 (단위 테스트 및 리셋용)."""
        cls._instances.clear()

    @classmethod
    def register(cls, name: str, adapter_cls: Type[BaseVlmAdapter]) -> None:
        """
        새로운 VLM 공급자 어댑터를 팩토리 레지스트리에 등록합니다.
        
        Args:
            name: 공급자 식별 이름 (예: 'claude', 'ollama', 'free')
            adapter_cls: BaseVlmAdapter를 상속받은 어댑터 클래스
        """
        clean_name = name.lower().strip()
        cls._registry[clean_name] = adapter_cls

    @classmethod
    def list_providers(cls) -> List[str]:
        """현재 등록된 모든 VLM 공급자 이름 목록을 반환합니다."""
        return sorted(cls._registry.keys())

    @classmethod
    def create(cls, provider: Optional[str] = None, model: Optional[str] = None, reuse: bool = True) -> BaseVlmAdapter:
        """
        요청된 공급자 이름과 모델에 해당하는 VLM 어댑터 인스턴스를 반환합니다.
        
        - reuse=True(기본값): 기존에 생성되어 작동 중인 어댑터 인스턴스를 재사용하여
          성공한 활성 모델(current_active_model) 및 소진 상태(exhausted_models)를 이미지 간에 연속 유지합니다.
        - reuse=False: 새로운 인스턴스를 강제로 생성합니다.
        """
        from config import GEMINI_API_KEY, GEMINI_API_KEYS, VLM_PROVIDER

        raw_name = provider or VLM_PROVIDER
        if not raw_name:
            raw_name = "gemini" if (GEMINI_API_KEYS or GEMINI_API_KEY) else "local"

        name = raw_name.lower().strip()
        cache_key = (name, (model or "").strip().lower())

        if reuse and cache_key in cls._instances:
            return cls._instances[cache_key]

        # '+' 또는 ','가 포함된 복합 공급자(예: 'gemini+free', 'free,gemini')는 ChainedVlmAdapter로 생성
        if "+" in name or "," in name:
            instance = ChainedVlmAdapter(providers=name, model=model)
            if reuse:
                cls._instances[cache_key] = instance
            return instance

        adapter_cls = cls._registry.get(name)

        if not adapter_cls:
            available = ", ".join(cls.list_providers())
            raise VlmError(
                f"지원하지 않는 VLM 공급자입니다: '{name}'. "
                f"사용 가능한 공급자: [{available}]"
            )

        # 모델 파라미터를 지원하는 어댑터 클래스인지 확인 후 전달
        if issubclass(adapter_cls, (GeminiAdapter, FreeModelAdapter, ChainedVlmAdapter)):
            instance = adapter_cls(model=model) if model else adapter_cls()
        else:
            instance = adapter_cls()

        if reuse:
            cls._instances[cache_key] = instance
        return instance
