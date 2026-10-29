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
from .filter import cluster_and_merge_diagrams, filter_contained_sub_elements, get_slide_diagram_union_box, is_diagram_slide_page, is_fragmented_or_meaningless_image, parse_bbox_coords
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
            # 1. 1차 파싱 및 페이지별 요소 그룹핑
            parsed_elements = []
            page_elements: Dict[int, List[Dict[str, Any]]] = {}

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
                    page_elements.setdefault(page, []).append(item_info)

            # 2. 페이지별 다이어그램 분석 및 항목 생성
            order_idx = 0
            sorted_pages = sorted(page_elements.keys())

            for page in sorted_pages:
                p_items = page_elements[page]
                pw, ph = renderer.page_size(page)

                # 2-A. 슬라이드형 구성도/다이어그램 페이지인 경우: 상단 헤더 보존 + 전체 통합 다이어그램 1개 생성
                if is_diagram_slide_page(p_items, pw, ph):
                    ubox, headers = get_slide_diagram_union_box(p_items, pw, ph)

                    # 슬라이드 헤더 텍스트 등록
                    for h in headers:
                        h_text = (h.get("text") or "").strip()
                        if h_text:
                            items.append(
                                {
                                    "order_idx": order_idx,
                                    "page": page,
                                    "element_type": h.get("etype", "heading"),
                                    "kind": "text",
                                    "text": h_text,
                                    "ocr_text": "",
                                    "image_path": None,
                                    "bbox": json.dumps(h.get("bbox")) if h.get("bbox") else None,
                                }
                            )
                            order_idx += 1

                    # 온전한 단일 아키텍처 다이어그램 크롭 및 등록
                    if ubox:
                        fname = f"{order_idx:05d}_image.png"
                        out_path = doc_img_dir / fname
                        image_rel = None
                        try:
                            if renderer.crop(page, list(ubox), out_path):
                                image_rel = f"{doc_id}/{fname}"
                        except Exception as exc:
                            print(f"[doc_extract] 슬라이드 다이어그램 크롭 실패 (p{page}): {exc}", flush=True)

                        extracted_ocr = ""
                        if image_rel and use_ocr:
                            extracted_ocr = ocr_image(out_path)

                        if image_rel:
                            items.append(
                                {
                                    "order_idx": order_idx,
                                    "page": page,
                                    "element_type": "diagram",
                                    "kind": "image",
                                    "text": "",
                                    "ocr_text": extracted_ocr,
                                    "image_path": image_rel,
                                    "bbox": json.dumps(list(ubox)),
                                }
                            )
                            order_idx += 1
                    continue

                # 2-B. 일반 페이지인 경우: 텍스트 보존 + 시각 요소 정제 및 거리 기반 군집화
                p_visuals = [el for el in p_items if el["kind"] in ("table", "image") and el.get("bbox")]
                candidate_visuals = []
                for v in p_visuals:
                    coords = parse_bbox_coords(v["bbox"])
                    if v["kind"] == "image":
                        if not coords or is_fragmented_or_meaningless_image(coords, pw, ph):
                            continue
                    elif v["kind"] == "table" and coords:
                        # 30pt 미만의 초소형 표 조각(아이콘 오인식) 제외
                        if abs(coords[2] - coords[0]) < 30.0 and abs(coords[3] - coords[1]) < 30.0:
                            continue
                    candidate_visuals.append(v)

                filtered_visuals = filter_contained_sub_elements(candidate_visuals)
                merged_visuals = cluster_and_merge_diagrams(
                    filtered_visuals, page_w=pw, page_h=ph, is_slide_page=False
                )

                # 일반 텍스트 등록
                for el in p_items:
                    if el["kind"] == "text":
                        t = (el.get("text") or "").strip()
                        if not t:
                            continue
                        items.append(
                            {
                                "order_idx": order_idx,
                                "page": page,
                                "element_type": el.get("etype", "paragraph"),
                                "kind": "text",
                                "text": t,
                                "ocr_text": "",
                                "image_path": None,
                                "bbox": json.dumps(el.get("bbox")) if el.get("bbox") else None,
                            }
                        )
                        order_idx += 1

                # 정제된 시각 요소 크롭 및 등록
                for v_item in merged_visuals:
                    v_kind = v_item["kind"]
                    v_bbox = v_item["bbox"]
                    v_etype = v_item["etype"]
                    v_text = v_item.get("text", "")

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
