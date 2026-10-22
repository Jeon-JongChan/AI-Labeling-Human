"""
문서 추출(doc_extract) 공식 진입점 및 파사드(Facade) 모듈.

extract 패키지의 어댑터(Adapter) 및 팩토리(Factory)를 직접 호출하는 단일 창구 역할을 수행합니다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from extract import (
    AutoFallbackExtractor,
    BaseDocExtractor,
    ExtractFactory,
    OpenDataLoaderExtractor,
    PageRenderer,
    PyPdfiumExtractor,
    _PageRenderer,
    _get_ocr_reader,
    _ocr_image,
    discover_pdfs,
    get_ocr_reader,
    ocr_image,
)


def doc_extract(
    pdf_path: Path,
    doc_id: int,
    *,
    engine: str | None = None,
    use_ocr: bool = True,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """
    [공식 진입점] PDF 1개에서 텍스트, 표, 그림 요소를 추출합니다.

    Args:
        pdf_path: 대상 PDF 파일 경로
        doc_id: 데이터베이스 내 문서 ID
        engine: 사용할 추출 엔진 ('opendataloader', 'pypdf', 'pypdfium', 'auto')
        use_ocr: 그림 요소에 대한 EasyOCR 텍스트 인식 수행 여부
        on_progress: 진행률 보고 콜백 함수
    Returns:
        추출된 항목(item dict) 리스트
    """
    extractor = ExtractFactory.create(engine)
    return extractor.extract(
        pdf_path=pdf_path,
        doc_id=doc_id,
        use_ocr=use_ocr,
        on_progress=on_progress,
    )


# 기존 코드 호환용 별칭 함수
def extract_pdf(
    pdf_path: Path,
    doc_id: int,
    *,
    use_ocr: bool = True,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """extract_pdf 레거시 호환 함수 -> doc_extract로 직접 위임."""
    return doc_extract(
        pdf_path=pdf_path,
        doc_id=doc_id,
        use_ocr=use_ocr,
        on_progress=on_progress,
    )


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
