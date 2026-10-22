"""
문서 추출기 팩토리(ExtractFactory) 모듈.

엔진 명칭(opendataloader, pypdf, pypdfium, auto)에 따라 적절한 추출기 어댑터를 생성하여 반환합니다.
동적 레지스트리(register)를 지원하여 새로운 추출 방식 추가 시 OCP를 준수합니다.
"""

from __future__ import annotations

import os
from typing import Dict, List, Type

from .base import BaseDocExtractor
from .opendataloader_adapter import OpenDataLoaderExtractor
from .pypdfium_adapter import PyPdfiumExtractor
from .auto_adapter import AutoFallbackExtractor


class ExtractFactory:
    """문서 추출기 팩토리 클래스 (Factory Method & Registry 패턴)."""

    _registry: Dict[str, Type[BaseDocExtractor]] = {
        "opendataloader": OpenDataLoaderExtractor,
        "odl": OpenDataLoaderExtractor,
        "pypdf": PyPdfiumExtractor,
        "pypdfium": PyPdfiumExtractor,
        "pdfium": PyPdfiumExtractor,
        "auto": AutoFallbackExtractor,
    }

    @classmethod
    def register(cls, name: str, extractor_cls: Type[BaseDocExtractor]) -> None:
        """
        새로운 문서 추출기 어댑터를 레지스트리에 등록합니다.
        
        Args:
            name: 엔진 식별 이름
            extractor_cls: BaseDocExtractor를 상속받은 어댑터 클래스
        """
        clean_name = name.lower().strip()
        cls._registry[clean_name] = extractor_cls

    @classmethod
    def list_engines(cls) -> List[str]:
        """등록된 추출 엔진 목록 반환."""
        return sorted(set(cls._registry.keys()))

    @classmethod
    def create(cls, engine: str | None = None) -> BaseDocExtractor:
        """
        지정된 엔진 이름에 맞는 추출기 어댑터 인스턴스를 생성합니다.
        
        Args:
            engine: 엔진 이름 (None 이거나 'auto'인 경우 AutoFallbackExtractor 생성)
        Returns:
            생성된 BaseDocExtractor 어댑터 인스턴스
        """
        name = (engine or os.getenv("EXTRACT_ENGINE", "auto")).lower().strip()
        extractor_cls = cls._registry.get(name)

        if not extractor_cls:
            available = ", ".join(cls.list_engines())
            raise ValueError(
                f"지원하지 않는 문서 추출 엔진입니다: '{name}'. "
                f"사용 가능한 엔진: [{available}]"
            )

        return extractor_cls()
