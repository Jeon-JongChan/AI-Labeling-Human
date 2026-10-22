"""
PDF 페이지 렌더링 및 영역 크롭 모듈 (pypdfium2 기반).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

# 크롭 렌더링 배율 (1.0 = 72dpi, 2.0 = 144dpi)
RENDER_SCALE = 2.0
# 크롭 여백 (PDF pt)
CROP_PAD = 4.0


class PageRenderer:
    """PDF 페이지를 렌더링하고 지정된 BBox 영역을 크롭하여 PNG로 저장하는 렌더러."""

    def __init__(self, pdf_path: Path) -> None:
        """PDF 문서 로딩 및 페이지 캐시 초기화."""
        import pypdfium2 as pdfium

        self._pdf = pdfium.PdfDocument(str(pdf_path))
        self._cache: dict[int, Any] = {}
        self._sizes: dict[int, tuple[float, float]] = {}

    def page_count(self) -> int:
        """총 페이지 수 반환."""
        return len(self._pdf)

    def page_image(self, page_idx: int) -> Any:
        """지정 인덱스(0-based)의 페이지 이미지를 렌더링하고 캐싱하여 반환."""
        if page_idx not in self._cache:
            page = self._pdf[page_idx]
            self._sizes[page_idx] = page.get_size()
            bitmap = page.render(scale=RENDER_SCALE)
            self._cache[page_idx] = bitmap.to_pil()
        return self._cache[page_idx]

    def crop(self, page: int, bbox: List[float], out_path: Path) -> bool:
        """
        지정 페이지(1-based)의 BBox([x0, y0, x1, y1]) 영역을 크롭하여 저장.

        PDF 좌표(원점 좌하단)를 기본으로 변환하며, 높이가 비정상적으로 얇은 경우
        상단 원점 좌표계로 재해석하여 크롭을 재시도합니다.
        """
        page_idx = page - 1
        if page_idx < 0 or page_idx >= self.page_count():
            return False

        img = self.page_image(page_idx)
        _, page_h = self._sizes[page_idx]
        x0, y0, x1, y1 = bbox
        x0, x1 = min(x0, x1) - CROP_PAD, max(x0, x1) + CROP_PAD
        y0, y1 = min(y0, y1) - CROP_PAD, max(y0, y1) + CROP_PAD

        # 좌하단 원점 → 이미지(상단 원점) 좌표 변환
        left = x0 * RENDER_SCALE
        right = x1 * RENDER_SCALE
        top = (page_h - y1) * RENDER_SCALE
        bottom = (page_h - y0) * RENDER_SCALE

        left = max(0, min(left, img.width - 1))
        right = max(left + 1, min(right, img.width))
        top = max(0, min(top, img.height - 1))
        bottom = max(top + 1, min(bottom, img.height))

        # 너무 얇은 경우 상단 원점 좌표계로 재시도
        if bottom - top < 8:
            top = max(0, min(y0 * RENDER_SCALE, img.height - 1))
            bottom = max(top + 1, min(y1 * RENDER_SCALE, img.height))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.crop((int(left), int(top), int(right), int(bottom))).save(out_path)
        return True

    def close(self) -> None:
        """내부 PDF 리소스 해제."""
        try:
            self._pdf.close()
        except Exception:
            pass

