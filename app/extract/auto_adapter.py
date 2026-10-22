"""
OpenDataLoader 우선 시도 및 pypdfium2 자동 폴백(Auto Fallback) 추출 어댑터 모듈.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List

from .base import BaseDocExtractor
from .opendataloader_adapter import OpenDataLoaderExtractor
from .pypdfium_adapter import PyPdfiumExtractor


class AutoFallbackExtractor(BaseDocExtractor):
    """
    Java 환경 존재 시 OpenDataLoader를 우선 사용하고,
    미설치 또는 파싱 실패 시 pypdfium2 내장 엔진으로 자동 전환하는 복합 추출 어댑터.
    """

    def __init__(
        self,
        odl_extractor: OpenDataLoaderExtractor | None = None,
        pdfium_extractor: PyPdfiumExtractor | None = None,
    ):
        """서브 추출기 주입 또는 기본 생성."""
        self.odl_extractor = odl_extractor or OpenDataLoaderExtractor()
        self.pdfium_extractor = pdfium_extractor or PyPdfiumExtractor()

    @property
    def name(self) -> str:
        """엔진 식별 명칭."""
        return "auto"

    def _do_extract(
        self,
        pdf_path: Path,
        doc_id: int,
        doc_img_dir: Path,
        use_ocr: bool,
        report: Callable[[str, int, int], None],
    ) -> List[Dict[str, Any]]:
        """OpenDataLoader 시도 후 실패 시 pypdfium 폴백 실행."""
        if shutil.which("java"):
            try:
                return self.odl_extractor._do_extract(
                    pdf_path=pdf_path,
                    doc_id=doc_id,
                    doc_img_dir=doc_img_dir,
                    use_ocr=use_ocr,
                    report=report,
                )
            except Exception as exc:
                print(f"[doc_extract] OpenDataLoader 실패 ({exc}) -> pypdfium2 내장 파서로 자동 전환합니다.", flush=True)

        # Java 미설치이거나 OpenDataLoader 실행 실패 시 pypdfium 내장 파서 실행
        return self.pdfium_extractor._do_extract(
            pdf_path=pdf_path,
            doc_id=doc_id,
            doc_img_dir=doc_img_dir,
            use_ocr=use_ocr,
            report=report,
        )

