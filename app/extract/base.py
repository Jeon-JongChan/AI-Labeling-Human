"""
문서 추출기(DocExtractor) 베이스 추상 클래스 및 템플릿 메서드 정의 모듈.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import IMAGES_DIR


class BaseDocExtractor(ABC):
    """
    모든 PDF 추출 어댑터의 공통 추상 기반 클래스.
    
    템플릿 메서드(extract)를 통해 파일 검증, 이미지 디렉터리 초기화 등의
    공통 생명주기를 관리하고 하위 클래스에서 세부 파싱 로직을 구현합니다.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """추출 엔진의 고유 식별 명칭."""
        pass

    def extract(
        self,
        pdf_path: Path,
        doc_id: int,
        *,
        use_ocr: bool = True,
        on_progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        [템플릿 메서드] PDF 1개에서 텍스트/표/그림 요소를 추출하는 공통 파이프라인.

        1. PDF 파일 유효성 검증
        2. 해당 문서용 이미지 디렉터리(IMAGES_DIR/<doc_id>) 초기화
        3. 엔진별 세부 추출 로직(_do_extract) 수행
        4. 진행 보고(on_progress) 완료 알림
        """
        resolved_path = pdf_path.resolve()
        if not resolved_path.is_file():
            raise FileNotFoundError(f"PDF 파일이 존재하지 않습니다: {resolved_path}")

        # 문서 이미지 출력 디렉터리 초기화
        doc_img_dir = IMAGES_DIR / str(doc_id)
        shutil.rmtree(doc_img_dir, ignore_errors=True)
        doc_img_dir.mkdir(parents=True, exist_ok=True)

        def report(stage: str, done: int, total: int) -> None:
            if on_progress:
                on_progress(stage, done, total)

        items = self._do_extract(
            pdf_path=resolved_path,
            doc_id=doc_id,
            doc_img_dir=doc_img_dir,
            use_ocr=use_ocr,
            report=report,
        )

        report("완료", len(items), len(items))
        return items

    @abstractmethod
    def _do_extract(
        self,
        pdf_path: Path,
        doc_id: int,
        doc_img_dir: Path,
        use_ocr: bool,
        report: Callable[[str, int, int], None],
    ) -> List[Dict[str, Any]]:
        """하위 어댑터에서 실제 PDF 파싱 및 추출을 수행하는 추상 메서드."""
        pass

