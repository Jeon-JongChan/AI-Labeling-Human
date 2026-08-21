"""
PDF 요소 추출 — OpenDataLoader(구조) + pypdfium2(크롭 이미지) + EasyOCR(선택).

DOCS_DIR(config.py) 의 PDF 하나를 받아:
  1. OpenDataLoader 로 JSON 구조 추출
  2. 문단·제목 등 텍스트 요소 → kind="text"
  3. 표 → kind="table"  (셀 텍스트 + 영역 크롭 PNG)
  4. 그림 → kind="image" (영역 크롭 PNG + OCR 텍스트)
결과는 item dict 리스트로 반환하고, 이미지는 LABEL_DIR/images/<doc_id>/ 에 저장.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from config import DOCS_DIR, IMAGES_DIR, LABEL_DIR, OCR_GPU, OCR_LANGS, WORK_DIR

TEXT_TYPES = frozenset(
    {"paragraph", "heading", "list", "list_item", "caption", "footnote"}
)
TABLE_TYPES = frozenset({"table"})
IMAGE_TYPES = frozenset({"image", "figure", "picture", "graphic", "chart", "formula"})

# 크롭 렌더링 배율 (1.0 = 72dpi)
RENDER_SCALE = 2.0
# 크롭 여백 (PDF pt)
CROP_PAD = 4.0

_ocr_reader = None
_ocr_failed: str | None = None


# ---------------------------------------------------------------------------
# OpenDataLoader JSON
# ---------------------------------------------------------------------------

def _convert_pdf(pdf_path: Path, work_dir: Path) -> Dict[str, Any]:
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


def _page_number(element: Dict[str, Any]) -> int | None:
    for key in ("page number", "page_number", "pageNumber", "page"):
        val = element.get(key)
        if isinstance(val, int):
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
    return None


def _bounding_box(element: Dict[str, Any]) -> List[float] | None:
    for key in ("bounding box", "bounding_box", "bbox"):
        val = element.get(key)
        if isinstance(val, list) and len(val) >= 4:
            try:
                return [float(v) for v in val[:4]]
            except (TypeError, ValueError):
                return None
    return None


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


def _iter_elements(node: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """트리 깊이 우선 순회 — 표/그림은 통째로 yield 하고 내부는 내려가지 않음."""
    etype = node.get("type")
    if etype in TABLE_TYPES or etype in IMAGE_TYPES:
        yield node
        return

    if etype in TEXT_TYPES and _element_text(node):
        yield node

    kids = node.get("kids")
    if isinstance(kids, list):
        for kid in kids:
            if isinstance(kid, dict):
                yield from _iter_elements(kid)


# ---------------------------------------------------------------------------
# 크롭 이미지 (pypdfium2)
# ---------------------------------------------------------------------------

class _PageRenderer:
    """페이지당 1회만 렌더링해서 크롭을 반복 사용."""

    def __init__(self, pdf_path: Path) -> None:
        import pypdfium2 as pdfium

        self._pdf = pdfium.PdfDocument(str(pdf_path))
        self._cache: dict[int, Any] = {}
        self._sizes: dict[int, tuple[float, float]] = {}

    def page_count(self) -> int:
        return len(self._pdf)

    def _page_image(self, page_idx: int):
        if page_idx not in self._cache:
            page = self._pdf[page_idx]
            self._sizes[page_idx] = page.get_size()
            bitmap = page.render(scale=RENDER_SCALE)
            self._cache[page_idx] = bitmap.to_pil()
        return self._cache[page_idx]

    def crop(self, page: int, bbox: List[float], out_path: Path) -> bool:
        """
        page: 1-based, bbox: [x0, y0, x1, y1] (PDF pt).
        PDF 좌표(원점 좌하단)를 기본으로 하고, 결과가 비정상이면
        상단 원점 좌표계로 재해석합니다.
        """
        page_idx = page - 1
        if page_idx < 0 or page_idx >= self.page_count():
            return False

        img = self._page_image(page_idx)
        _, page_h = self._sizes[page_idx]
        x0, y0, x1, y1 = bbox
        x0, x1 = min(x0, x1) - CROP_PAD, max(x0, x1) + CROP_PAD
        y0, y1 = min(y0, y1) - CROP_PAD, max(y0, y1) + CROP_PAD

        # 좌하단 원점 → 이미지(상단 원점) 좌표
        left = x0 * RENDER_SCALE
        right = x1 * RENDER_SCALE
        top = (page_h - y1) * RENDER_SCALE
        bottom = (page_h - y0) * RENDER_SCALE

        left = max(0, min(left, img.width - 1))
        right = max(left + 1, min(right, img.width))
        top = max(0, min(top, img.height - 1))
        bottom = max(top + 1, min(bottom, img.height))

        if bottom - top < 8:  # 너무 얇으면 상단 원점 좌표계로 재시도
            top = max(0, min(y0 * RENDER_SCALE, img.height - 1))
            bottom = max(top + 1, min(y1 * RENDER_SCALE, img.height))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.crop((int(left), int(top), int(right), int(bottom))).save(out_path)
        return True

    def close(self) -> None:
        self._pdf.close()


# ---------------------------------------------------------------------------
# OCR (EasyOCR — 선택 의존성)
# ---------------------------------------------------------------------------

def _get_ocr_reader():
    global _ocr_reader, _ocr_failed
    if _ocr_reader is not None or _ocr_failed is not None:
        return _ocr_reader
    try:
        import easyocr

        langs = "+".join(OCR_LANGS)
        print(
            f"[labeler] EasyOCR 로딩 ({langs}, 첫 실행 시 모델 다운로드)...",
            flush=True,
        )
        _ocr_reader = easyocr.Reader(list(OCR_LANGS), gpu=OCR_GPU, verbose=False)
    except Exception as exc:
        _ocr_failed = str(exc)
        print(f"[labeler] EasyOCR 사용 불가 → OCR 생략: {exc}", flush=True)
    return _ocr_reader


def _ocr_image(image_path: Path) -> str:
    reader = _get_ocr_reader()
    if reader is None:
        return ""
    try:
        results = reader.readtext(str(image_path), detail=0, paragraph=True)
        return "\n".join(str(r).strip() for r in results if str(r).strip())
    except Exception as exc:
        print(f"[labeler] OCR 실패 ({image_path.name}): {exc}", flush=True)
        return ""


# ---------------------------------------------------------------------------
# 메인 추출
# ---------------------------------------------------------------------------

def extract_pdf(
    pdf_path: Path,
    doc_id: int,
    *,
    use_ocr: bool = True,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """
    PDF 1개를 추출해 item dict 리스트 반환.
    item: {order_idx, page, element_type, kind, text, ocr_text, image_path, bbox}
    image_path 는 IMAGES_DIR 기준 상대 경로 (예: "3/00012_table.png").
    """
    pdf_path = pdf_path.resolve()

    def report(stage: str, done: int, total: int) -> None:
        if on_progress:
            on_progress(stage, done, total)

    report("PDF 구조 분석 (OpenDataLoader)", 0, 1)
    work_dir = WORK_DIR / pdf_path.stem
    shutil.rmtree(work_dir, ignore_errors=True)
    try:
        doc_json = _convert_pdf(pdf_path, work_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    report("PDF 구조 분석 (OpenDataLoader)", 1, 1)

    elements = list(_iter_elements(doc_json))
    total = len(elements)

    # 기존 이미지 정리
    doc_img_dir = IMAGES_DIR / str(doc_id)
    shutil.rmtree(doc_img_dir, ignore_errors=True)

    renderer: _PageRenderer | None = None
    items: List[Dict[str, Any]] = []

    try:
        for idx, element in enumerate(elements):
            etype = str(element.get("type") or "")
            page = _page_number(element)
            bbox = _bounding_box(element)
            text = _element_text(element)

            if etype in TABLE_TYPES:
                kind = "table"
            elif etype in IMAGE_TYPES:
                kind = "image"
            else:
                kind = "text"

            stage = {"text": "텍스트 수집", "table": "표 크롭", "image": "그림 크롭·OCR"}[kind]
            report(stage, idx, total)

            image_rel: str | None = None
            ocr_text = ""

            if kind in ("table", "image") and page and bbox:
                if renderer is None:
                    renderer = _PageRenderer(pdf_path)
                fname = f"{idx:05d}_{kind}.png"
                out_path = doc_img_dir / fname
                try:
                    if renderer.crop(page, bbox, out_path):
                        image_rel = f"{doc_id}/{fname}"
                except Exception as exc:
                    print(f"[labeler] 크롭 실패 (p{page} {etype}): {exc}", flush=True)

                if kind == "image" and image_rel and use_ocr:
                    ocr_text = _ocr_image(out_path)

            # 텍스트도 OCR도 이미지도 없으면 스킵
            if not text and not ocr_text and not image_rel:
                continue

            items.append(
                {
                    "order_idx": idx,
                    "page": page,
                    "element_type": etype,
                    "kind": kind,
                    "text": text,
                    "ocr_text": ocr_text,
                    "image_path": image_rel,
                    "bbox": json.dumps(bbox) if bbox else None,
                }
            )
    finally:
        if renderer is not None:
            renderer.close()

    report("완료", total, total)
    return items


def discover_pdfs() -> List[Path]:
    """data/docs 이하 PDF 재귀 탐색 (_rejected 제외)."""
    if not DOCS_DIR.is_dir():
        return []
    found: List[Path] = []
    for path in DOCS_DIR.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        if path.name.startswith("."):
            continue
        rel_parts = path.relative_to(DOCS_DIR).parts
        if any(part in ("_rejected",) for part in rel_parts[:-1]):
            continue
        found.append(path)
    return sorted(found)
