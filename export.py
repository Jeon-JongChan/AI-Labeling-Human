"""
라벨링 결과 내보내기 — JSONL(ingest용) / HTML(사람 확인용, 이미지 base64 내장).
"""

from __future__ import annotations

import base64
import html
import json
from typing import Any, Dict, List

from config import IMAGES_DIR

_STATUS_KO = {"approved": "적합", "rejected": "부적합", "pending": "미검토"}


def final_text(item: Dict[str, Any]) -> str:
    """사람 수정본 > 추출 텍스트 > OCR 텍스트 순."""
    edited = (item.get("edited_text") or "").strip()
    if edited:
        return edited
    text = (item.get("text") or "").strip()
    if text:
        return text
    return (item.get("ocr_text") or "").strip()


def to_jsonl(doc: Dict[str, Any], items: List[Dict[str, Any]], scope: str) -> str:
    """scope: approved(적합만) | all(전체, status 포함)."""
    lines: List[str] = []
    for item in items:
        if scope == "approved" and item["status"] != "approved":
            continue
        record = {
            "file_name": doc["file_name"],
            "rel_path": doc["rel_path"],
            "page": item["page"],
            "bbox": json.loads(item["bbox"]) if item.get("bbox") else None,
            "element_type": item["element_type"],
            "kind": item["kind"],
            "text": final_text(item),
            "ocr_text": (item.get("ocr_text") or "").strip() or None,
            "image_path": item.get("image_path"),
            "status": item["status"],
        }
        lines.append(json.dumps(record, ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")


def _image_data_uri(rel_path: str) -> str | None:
    path = IMAGES_DIR / rel_path
    if not path.is_file():
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def to_html(doc: Dict[str, Any], items: List[Dict[str, Any]], scope: str) -> str:
    rows: List[str] = []
    for item in items:
        if scope == "approved" and item["status"] != "approved":
            continue
        status = item["status"]
        badge = _STATUS_KO.get(status, status)
        text = final_text(item)
        img_html = ""
        if item.get("image_path"):
            uri = _image_data_uri(item["image_path"])
            if uri:
                img_html = f'<img src="{uri}" alt="crop" />'
        ocr = (item.get("ocr_text") or "").strip()
        ocr_html = (
            f'<div class="ocr"><b>OCR</b><pre>{html.escape(ocr)}</pre></div>'
            if ocr and ocr != text
            else ""
        )
        rows.append(
            f"""
  <section class="item {status}">
    <header>
      <span class="badge {status}">{badge}</span>
      <span class="meta">p.{item['page'] or '?'} · {html.escape(item['kind'])} · {html.escape(item['element_type'] or '')}</span>
    </header>
    {img_html}
    <pre class="text">{html.escape(text)}</pre>
    {ocr_html}
  </section>"""
        )

    title = html.escape(doc["file_name"])
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>라벨링 결과 — {title}</title>
<style>
  body {{ font-family: 'Malgun Gothic', sans-serif; background: #f5f5f7; margin: 0; padding: 2rem; }}
  h1 {{ font-size: 1.2rem; }}
  .item {{ background: #fff; border: 1px solid #ddd; border-left: 6px solid #bbb;
           border-radius: 8px; padding: 1rem; margin-bottom: 1rem; max-width: 900px; }}
  .item.approved {{ border-left-color: #2e9e5b; }}
  .item.rejected {{ border-left-color: #d64545; opacity: 0.65; }}
  .badge {{ font-size: 0.75rem; padding: 0.15rem 0.5rem; border-radius: 999px; color: #fff; background: #999; }}
  .badge.approved {{ background: #2e9e5b; }}
  .badge.rejected {{ background: #d64545; }}
  .meta {{ color: #777; font-size: 0.8rem; margin-left: 0.5rem; }}
  img {{ max-width: 100%; border: 1px solid #eee; margin: 0.5rem 0; }}
  pre {{ white-space: pre-wrap; word-break: break-word; font-family: inherit; margin: 0.5rem 0 0; }}
  .ocr {{ background: #fafafa; border: 1px dashed #ccc; padding: 0.5rem; margin-top: 0.5rem; font-size: 0.85rem; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p>범위: {'적합만' if scope == 'approved' else '전체'} · 항목 {len(rows)}개</p>
{''.join(rows)}
</body>
</html>"""
