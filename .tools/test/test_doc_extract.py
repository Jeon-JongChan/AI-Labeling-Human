"""
문서 추출(doc_extract / extract 패키지) 디자인 패턴 및 어댑터 단위 테스트.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
APP_DIR = PROJECT_ROOT / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from extract import (
    AutoFallbackExtractor,
    BaseDocExtractor,
    ExtractFactory,
    OpenDataLoaderExtractor,
    PyPdfiumExtractor,
    discover_pdfs,
)
import doc_extract
import extract


class DummyDocExtractor(BaseDocExtractor):
    """테스트용 모의 문서 추출 어댑터."""

    @property
    def name(self) -> str:
        return "dummy_engine"

    def _do_extract(
        self,
        pdf_path: Path,
        doc_id: int,
        doc_img_dir: Path,
        use_ocr: bool,
        report: Callable[[str, int, int], None],
    ) -> List[Dict[str, Any]]:
        report("모의 추출 중", 1, 1)
        return [
            {
                "order_idx": 0,
                "page": 1,
                "element_type": "paragraph",
                "kind": "text",
                "text": f"extracted from {pdf_path.name}",
                "ocr_text": "",
                "image_path": None,
                "bbox": None,
            }
        ]


def test_extract_factory():
    """ExtractFactory 어댑터 생성 및 등록 검증."""
    # 1. 기본 2가지 방식 + auto 생성 검증
    pypdf = ExtractFactory.create("pypdf")
    assert isinstance(pypdf, PyPdfiumExtractor)
    pypdfium = ExtractFactory.create("pypdfium")
    assert isinstance(pypdfium, PyPdfiumExtractor)

    odl = ExtractFactory.create("opendataloader")
    assert isinstance(odl, OpenDataLoaderExtractor)
    odl_short = ExtractFactory.create("odl")
    assert isinstance(odl_short, OpenDataLoaderExtractor)

    auto = ExtractFactory.create("auto")
    assert isinstance(auto, AutoFallbackExtractor)
    print("[통과] ExtractFactory: 기본 엔진(pypdf, opendataloader, auto) 정상 생성")

    # 2. 미지원 엔진 요청 시 예외
    try:
        ExtractFactory.create("unknown_engine_123")
        assert False, "미지원 엔진 요청 시 ValueError가 발생해야 함"
    except ValueError as e:
        assert "지원하지 않는 문서 추출 엔진" in str(e)
        print("[통과] ExtractFactory: 미지원 엔진 예외 처리")

    # 3. 신규 엔진 동적 등록 (OCP 확장성 검증)
    ExtractFactory.register("dummy_engine", DummyDocExtractor)
    dummy_inst = ExtractFactory.create("dummy_engine")
    assert isinstance(dummy_inst, DummyDocExtractor)
    assert "dummy_engine" in ExtractFactory.list_engines()
    print("[통과] ExtractFactory: 신규 어댑터 동적 등록 및 생성 (OCP 확장성 검증)")


def test_template_method():
    """BaseDocExtractor 템플릿 메서드 검증."""
    extractor = DummyDocExtractor()

    # 1. 존재하지 않는 파일 검증 (선행 처리)
    non_existent = Path("non_existent_doc.pdf")
    try:
        extractor.extract(non_existent, doc_id=999)
        assert False, "존재하지 않는 파일에 대해 FileNotFoundError가 발생해야 함"
    except FileNotFoundError as e:
        assert "PDF 파일이 존재하지 않습니다" in str(e)
        print("[통과] BaseDocExtractor: 존재하지 않는 파일 검증")

    # 2. 실제 파일에 대한 파이프라인 및 콜백 동작 검증
    temp_pdf = HERE / "dummy_doc.pdf"
    temp_pdf.write_bytes(b"%PDF-1.4 dummy pdf header")
    progress_calls = []

    def on_prog(stage, done, total):
        progress_calls.append((stage, done, total))

    try:
        items = extractor.extract(temp_pdf, doc_id=888, on_progress=on_prog)
        assert len(items) == 1
        assert items[0]["text"] == "extracted from dummy_doc.pdf"
        assert len(progress_calls) >= 2
        assert progress_calls[-1] == ("완료", 1, 1)
        print("[통과] BaseDocExtractor: 템플릿 메서드 파이프라인 및 콜백 완료")
    finally:
        if temp_pdf.exists():
            temp_pdf.unlink()


def test_facade_and_legacy_compat():
    """DocExtractFacade 및 doc_extract / extract 패키지 호환성 검증."""
    temp_pdf = HERE / "facade_doc.pdf"
    temp_pdf.write_bytes(b"%PDF-1.4 dummy pdf header")

    try:
        ExtractFactory.register("dummy_engine", DummyDocExtractor)

        # 1. doc_extract 모듈 호출 검증 (지정 엔진)
        items1 = doc_extract.doc_extract(temp_pdf, doc_id=777, engine="dummy_engine")
        assert len(items1) == 1
        assert items1[0]["text"] == "extracted from facade_doc.pdf"
        print("[통과] doc_extract.doc_extract 파사드 함수 정상 작동")

        # 2. extract_pdf 레거시 호환 호출 검증 (mock 또는 dummy_engine)
        with patch.object(AutoFallbackExtractor, "_do_extract", return_value=[{"text": "mock_extracted"}]):
            items2 = extract.extract_pdf(temp_pdf, doc_id=777)
            assert len(items2) == 1
            assert items2[0]["text"] == "mock_extracted"
            print("[통과] extract.extract_pdf 레거시 호환 함수 정상 작동")
    finally:
        if temp_pdf.exists():
            temp_pdf.unlink()


def test_discovery():
    """discover_pdfs 함수 동작 검증."""
    res = doc_extract.discover_pdfs()
    assert isinstance(res, list)
    print(f"[통과] discover_pdfs 정상 작동 (탐색된 PDF: {len(res)}개)")


if __name__ == "__main__":
    test_extract_factory()
    test_template_method()
    test_facade_and_legacy_compat()
    test_discovery()
    print("[통과] 모든 문서 추출(doc_extract) 디자인 패턴 테스트 완료")
