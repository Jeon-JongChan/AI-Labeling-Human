"""
문서 추출(Extract) 어댑터 패키지.

디자인 패턴:
- Adapter / Strategy: OpenDataLoader, PyPdfium 등의 상이한 PDF 추출 엔진을 BaseDocExtractor 인터페이스로 일원화
- Template Method: BaseDocExtractor.extract()를 통한 공통 생명주기 및 디렉터리 관리
- Factory Method: ExtractFactory를 통한 추출기 동적 생성 및 등록 지원
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .base import BaseDocExtractor
from .discovery import discover_pdfs
from .factory import ExtractFactory
from .opendataloader_adapter import OpenDataLoaderExtractor
from .pypdfium_adapter import PyPdfiumExtractor
from .auto_adapter import AutoFallbackExtractor
from .renderer import PageRenderer
from .ocr import get_ocr_reader, ocr_image


def doc_extract(
    pdf_path: Path,
    doc_id: int,
    *,
    engine: str | None = None,
    use_ocr: bool = True,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """
    [패키지 진입점] PDF 1개에서 텍스트, 표, 그림 요소를 추출합니다.
    """
    extractor = ExtractFactory.create(engine)
    return extractor.extract(
        pdf_path=pdf_path,
        doc_id=doc_id,
        use_ocr=use_ocr,
        on_progress=on_progress,
    )


def extract_pdf(
    pdf_path: Path,
    doc_id: int,
    *,
    use_ocr: bool = True,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """extract_pdf 레거시 호환 함수."""
    return doc_extract(
        pdf_path=pdf_path,
        doc_id=doc_id,
        use_ocr=use_ocr,
        on_progress=on_progress,
    )


# 하위 호환용 내부 별칭
_PageRenderer = PageRenderer
_get_ocr_reader = get_ocr_reader
_ocr_image = ocr_image

__all__ = [
    "doc_extract",
    "extract_pdf",
    "discover_pdfs",
    "ExtractFactory",
    "BaseDocExtractor",
    "OpenDataLoaderExtractor",
    "PyPdfiumExtractor",
    "AutoFallbackExtractor",
    "PageRenderer",
    "_PageRenderer",
    "_get_ocr_reader",
    "_ocr_image",
    "get_ocr_reader",
    "ocr_image",
]
