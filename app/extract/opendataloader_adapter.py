"""
OpenDataLoader PDF 기반 구조화 문서 추출 어댑터 모듈.

Java JRE 17 및 OpenDataLoader PDF 라이브러리를 활용하여 문서 계층(헤더, 문단, 표, 그림)을 구조적으로 분석합니다.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List

from config import WORK_DIR
from .base import BaseDocExtractor
from .filter import cluster_and_merge_diagrams, filter_contained_sub_elements, is_fragmented_or_meaningless_image, parse_bbox_coords
from .ocr import ocr_image
from .renderer import PageRenderer

# 포터블 Java 자동 감지 및 환경변수 바인딩
_TOOL_DIR = Path(__file__).resolve().parent.parent.parent
_PORTABLE_JAVA_DIR = _TOOL_DIR / ".tools" / "java" if (_TOOL_DIR / ".tools" / "java").exists() else _TOOL_DIR / "java"
_PORTABLE_JAVA_BIN = _PORTABLE_JAVA_DIR / "bin" / ("java.exe" if sys.platform == "win32" else "java")
if _PORTABLE_JAVA_BIN.is_file():
    os.environ["JAVA_HOME"] = str(_PORTABLE_JAVA_DIR)
    if str(_PORTABLE_JAVA_BIN.parent) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{_PORTABLE_JAVA_BIN.parent};{os.environ.get('PATH', '')}"

TEXT_TYPES = frozenset(
    {"paragraph", "heading", "list", "list_item", "caption", "footnote"}
)
TABLE_TYPES = frozenset({"table"})
IMAGE_TYPES = frozenset({"image", "figure", "picture", "graphic", "chart", "formula"})


class OpenDataLoaderExtractor(BaseDocExtractor):
    """OpenDataLoader PDF 기반 구조 분석 및 추출 어댑터."""

    @property
    def name(self) -> str:
        """엔진 식별 명칭."""
        return "opendataloader"

    def _convert_pdf(self, pdf_path: Path, work_dir: Path) -> Dict[str, Any]:
        """OpenDataLoader를 실행하여 PDF를 JSON 구조 파일로 변환."""
        import opendataloader_pdf

        work_dir.mkdir(parents=True, exist_ok=True)
        opendataloader_pdf.convert(
            input_path=[str(pdf_path)],
            output_dir=str(work_dir),
            format="json",
            quiet=True,
        )
        matches = sorted(work_dir.glob(f"{pdf_path.stem}*.json"))
        if not matches:
            raise FileNotFoundError(f"OpenDataLoader JSON 출력 없음: {pdf_path.name}")
        with matches[0].open(encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _page_number(element: Dict[str, Any]) -> int | None:
        for key in ("page number", "page_number", "pageNumber", "page"):
            val = element.get(key)
            if isinstance(val, int):
                return val
            if isinstance(val, str) and val.isdigit():
                return int(val)
        return None

    @staticmethod
    def _bounding_box(element: Dict[str, Any]) -> List[float] | None:
        for key in ("bounding box", "bounding_box", "bbox"):
            val = element.get(key)
            if isinstance(val, list) and len(val) >= 4:
                try:
                    return [float(v) for v in val[:4]]
                except (TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def _element_text(element: Dict[str, Any]) -> str:
        content = element.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

        if element.get("type") in TABLE_TYPES:
            rows = element.get("rows") or element.get("kids") or []
            lines: List[str] = []
            for row in rows:
                if isinstance(row, dict):
                    cell = row.get("content") or row.get("text")
                    if isinstance(cell, str) and cell.strip():
                        lines.append(cell.strip())
                elif isinstance(row, list):
                    cells = [str(c).strip() for c in row if str(c).strip()]
                    if cells:
                        lines.append(" | ".join(cells))
            if lines:
                return "\n".join(lines)
        return ""

    def _iter_elements(self, node: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
        """트리 깊이 우선 순회 — 표/그림은 통째로 yield 하고 내부는 내려가지 않음."""
        etype = node.get("type")
        if etype in TABLE_TYPES or etype in IMAGE_TYPES:
            yield node
            return

        if etype in TEXT_TYPES and self._element_text(node):
            yield node

        kids = node.get("kids")
        if isinstance(kids, list):
            for kid in kids:
                if isinstance(kid, dict):
                    yield from self._iter_elements(kid)

    def _do_extract(
        self,
        pdf_path: Path,
        doc_id: int,
        doc_img_dir: Path,
        use_ocr: bool,
        report: Callable[[str, int, int], None],
    ) -> List[Dict[str, Any]]:
        """OpenDataLoader를 통한 구조 분석 및 영역 크롭."""
        if not shutil.which("java"):
            raise RuntimeError("Java(JRE 17) 환경이 감지되지 않아 OpenDataLoader를 실행할 수 없습니다.")

        report("PDF 구조 분석 (OpenDataLoader)", 0, 1)
        work_dir = WORK_DIR / pdf_path.stem
        shutil.rmtree(work_dir, ignore_errors=True)
        try:
            doc_json = self._convert_pdf(pdf_path, work_dir)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        report("PDF 구조 분석 (OpenDataLoader)", 1, 1)

        elements = list(self._iter_elements(doc_json))
        total = len(elements)

        renderer = PageRenderer(pdf_path)
        items: List[Dict[str, Any]] = []

        try:
            # 1. 1차 파싱 및 페이지별 시각 요소(image/table) 분류
            parsed_elements = []
            page_visuals: Dict[int, List[Dict[str, Any]]] = {}
            page_text_len: Dict[int, int] = {}

            for idx, element in enumerate(elements):
                etype = str(element.get("type") or "")
                page = self._page_number(element)
                bbox = self._bounding_box(element)
                text = self._element_text(element)

                if etype in TABLE_TYPES:
                    kind = "table"
                elif etype in IMAGE_TYPES:
                    kind = "image"
                else:
                    kind = "text"

                item_info = {
                    "orig_idx": idx,
                    "element": element,
                    "etype": etype,
                    "kind": kind,
                    "page": page,
                    "bbox": bbox,
                    "text": text,
                }
                parsed_elements.append(item_info)

                if page:
                    page_text_len[page] = page_text_len.get(page, 0) + len(text or "")
                    if kind in ("table", "image") and bbox:
                        page_visuals.setdefault(page, []).append(item_info)

            # 2. 페이지별 시각 요소 정제 및 다이어그램 군집화 통합
            page_final_visuals: Dict[int, List[Dict[str, Any]]] = {}
            for page, visuals in page_visuals.items():
                pw, ph = renderer.page_size(page)
                # 2-1. 단독 노이즈/조각/배경 필터링
                candidate_visuals = []
                for v in visuals:
                    if v["kind"] == "image":
                        coords = parse_bbox_coords(v["bbox"])
                        if not coords or is_fragmented_or_meaningless_image(coords, pw, ph):
                            continue
                    candidate_visuals.append(v)

                # 2-2. 상위 영역에 포함된 서브 컴포넌트(아이콘 등) 필터링
                filtered_visuals = filter_contained_sub_elements(candidate_visuals)

                # 2-3. 다이어그램 조각 군집화 통합 (슬라이드형 구성도 및 인접 도형 병합)
                is_slide = page_text_len.get(page, 0) < 120
                merged_visuals = cluster_and_merge_diagrams(
                    filtered_visuals, page_w=pw, page_h=ph, is_slide_page=is_slide
                )
                page_final_visuals[page] = merged_visuals

            # 3. 크롭 및 최종 항목 생성 (텍스트와 정제된 시각 요소 순차 결합)
            order_idx = 0
            emitted_visual_pages = set()

            for idx, info in enumerate(parsed_elements):
                kind = info["kind"]
                page = info["page"]
                text = info["text"]
                etype = info["etype"]

                # 텍스트 요소는 그대로 기록
                if kind == "text":
                    if not text:
                        continue
                    report("텍스트 수집", idx, total)
                    items.append(
                        {
                            "order_idx": order_idx,
                            "page": page,
                            "element_type": etype,
                            "kind": "text",
                            "text": text,
                            "ocr_text": "",
                            "image_path": None,
                            "bbox": json.dumps(info["bbox"]) if info["bbox"] else None,
                        }
                    )
                    order_idx += 1
                    continue

                # 시각 요소(table, image): 해당 페이지의 정제/통합 목록에서 아직 출력되지 않은 경우 일괄 처리
                if page and page not in emitted_visual_pages:
                    emitted_visual_pages.add(page)
                    v_list = page_final_visuals.get(page, [])
                    for v_item in v_list:
                        v_kind = v_item["kind"]
                        v_bbox = v_item["bbox"]
                        v_etype = v_item["etype"]
                        v_text = v_item.get("text", "")

                        stage = "표 크롭" if v_kind == "table" else ("그림 EasyOCR 판독 중" if use_ocr else "그림 크롭")
                        report(stage, idx, total)

                        image_rel = None
                        extracted_ocr = ""

                        if v_bbox:
                            fname = f"{order_idx:05d}_{v_kind}.png"
                            out_path = doc_img_dir / fname
                            try:
                                if renderer.crop(page, v_bbox, out_path):
                                    image_rel = f"{doc_id}/{fname}"
                            except Exception as exc:
                                print(f"[doc_extract] 크롭 실패 (p{page} {v_etype}): {exc}", flush=True)

                            if v_kind == "image" and image_rel and use_ocr:
                                extracted_ocr = ocr_image(out_path)

                        if not v_text and not extracted_ocr and not image_rel:
                            continue

                        items.append(
                            {
                                "order_idx": order_idx,
                                "page": page,
                                "element_type": v_etype,
                                "kind": v_kind,
                                "text": v_text,
                                "ocr_text": extracted_ocr,
                                "image_path": image_rel,
                                "bbox": json.dumps(v_bbox) if v_bbox else None,
                            }
                        )
                        order_idx += 1
        finally:
            renderer.close()

        return items
