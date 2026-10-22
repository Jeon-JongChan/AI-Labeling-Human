"""
pypdfium2 / pypdf 기반 내장 직접 추출 어댑터 모듈.

Java나 외부 도구 없이 Python 환경에서 경량으로 텍스트 및 이미지 객체를 크롭 추출합니다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List

from .base import BaseDocExtractor
from .ocr import ocr_image
from .renderer import RENDER_SCALE


class PyPdfiumExtractor(BaseDocExtractor):
    """pypdfium2 기반 내장 PDF 텍스트/이미지 추출 어댑터."""

    @property
    def name(self) -> str:
        """엔진 식별 명칭."""
        return "pypdfium"

    def _do_extract(
        self,
        pdf_path: Path,
        doc_id: int,
        doc_img_dir: Path,
        use_ocr: bool,
        report: Callable[[str, int, int], None],
    ) -> List[Dict[str, Any]]:
        """pypdfium2를 사용하여 페이지별 본문 텍스트 및 이미지 추출."""
        import pypdfium2 as pdfium

        report("PDF 파싱 중 (내장 pypdfium 엔진)", 0, 1)

        pdf = pdfium.PdfDocument(str(pdf_path))
        total_pages = len(pdf)
        items: List[Dict[str, Any]] = []
        order_idx = 0

        try:
            for page_idx in range(total_pages):
                page_num = page_idx + 1
                page = pdf[page_idx]

                report(f"페이지 {page_num}/{total_pages} 처리 중", page_num, total_pages)

                # 1. 본문 텍스트 추출
                textpage = page.get_textpage()
                page_raw_text = textpage.get_text_range().strip()

                if page_raw_text:
                    paragraphs = [p.strip() for p in re.split(r"[\r\n]{2,}", page_raw_text) if p.strip()]
                    if not paragraphs:
                        paragraphs = [page_raw_text]
                    for p_text in paragraphs:
                        items.append(
                            {
                                "order_idx": order_idx,
                                "page": page_num,
                                "element_type": "paragraph",
                                "kind": "text",
                                "text": p_text,
                                "ocr_text": "",
                                "image_path": None,
                                "bbox": None,
                            }
                        )
                        order_idx += 1

                # 2. 이미지 객체 탐색 및 크롭
                img_objs = [obj for obj in page.get_objects() if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE]
                page_size = page.get_size()
                page_h = page_size[1]
                cached_pil_page = None

                if img_objs:
                    for img_idx, img_obj in enumerate(img_objs):
                        try:
                            bounds = img_obj.get_bounds()
                            x0, y0, x1, y1 = bounds
                            if abs(x1 - x0) < 15 or abs(y1 - y0) < 15:
                                continue

                            if cached_pil_page is None:
                                cached_pil_page = page.render(scale=RENDER_SCALE).to_pil()

                            left = max(0, min(x0 * RENDER_SCALE, cached_pil_page.width - 1))
                            right = max(left + 1, min(x1 * RENDER_SCALE, cached_pil_page.width))
                            top = max(0, min((page_h - y1) * RENDER_SCALE, cached_pil_page.height - 1))
                            bottom = max(top + 1, min((page_h - y0) * RENDER_SCALE, cached_pil_page.height))

                            if right - left < 10 or bottom - top < 10:
                                continue

                            fname = f"{order_idx:05d}_image_p{page_num}_{img_idx}.png"
                            out_path = doc_img_dir / fname
                            crop_img = cached_pil_page.crop((int(left), int(top), int(right), int(bottom)))
                            crop_img.save(out_path)

                            extracted_ocr = ""
                            if use_ocr:
                                report("그림 EasyOCR 판독 중", page_num, total_pages)
                                extracted_ocr = ocr_image(out_path)

                            items.append(
                                {
                                    "order_idx": order_idx,
                                    "page": page_num,
                                    "element_type": "figure",
                                    "kind": "image",
                                    "text": "",
                                    "ocr_text": extracted_ocr,
                                    "image_path": f"{doc_id}/{fname}",
                                    "bbox": json.dumps([x0, y0, x1, y1]),
                                }
                            )
                            order_idx += 1
                        except Exception as exc:
                            print(f"[doc_extract] pypdfium2 이미지 크롭 실패 (p{page_num}): {exc}", flush=True)

                elif len(page_raw_text) < 50:
                    # 텍스트가 거의 없는 도면/표지/스캔 페이지의 경우 전체 페이지를 이미지로 렌더링
                    if cached_pil_page is None:
                        cached_pil_page = page.render(scale=RENDER_SCALE).to_pil()
                    fname = f"{order_idx:05d}_page_{page_num}.png"
                    out_path = doc_img_dir / fname
                    cached_pil_page.save(out_path)

                    extracted_ocr = ""
                    if use_ocr:
                        report("그림 EasyOCR 판독 중", page_num, total_pages)
                        extracted_ocr = ocr_image(out_path)

                    items.append(
                        {
                            "order_idx": order_idx,
                            "page": page_num,
                            "element_type": "scanned_page",
                            "kind": "image",
                            "text": "",
                            "ocr_text": extracted_ocr,
                            "image_path": f"{doc_id}/{fname}",
                            "bbox": None,
                        }
                    )
                    order_idx += 1
        finally:
            pdf.close()

        return items

