"""
라벨링 결과 내보내기 — JSONL(ingest용) / HTML(사람 확인용, 이미지 base64 내장).
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path
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
            "id": item["id"],
            "order_idx": item.get("order_idx"),
            "file_name": doc["file_name"],
            "rel_path": doc["rel_path"],
            "page": item["page"],
            "bbox": json.loads(item["bbox"]) if item.get("bbox") else None,
            "element_type": item["element_type"],
            "kind": item["kind"],
            "text": final_text(item),
            "ocr_text": (item.get("ocr_text") or "").strip() or None,
            "image_path": item.get("image_path"),
            "status": status_to_jsonl(item["status"]),
        }
        lines.append(json.dumps(record, ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")


_STATUS_ALIASES = {
    "approved": "approved",
    "적합": "approved",
    "ok": "approved",
    "true": "approved",
    "1": "approved",
    "rejected": "rejected",
    "부적합": "rejected",
    "no": "rejected",
    "false": "rejected",
    "0": "rejected",
    "pending": "pending",
    "미검토": "pending",
}


def status_to_jsonl(status: str) -> int | None:
    """JSONL status: 1=승인, 0=거부, 미검토는 null."""
    if status == "approved":
        return 1
    if status == "rejected":
        return 0
    return None


def normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "approved" if value else "rejected"
    if isinstance(value, (int, float)) and float(value) == int(value):
        iv = int(value)
        if iv == 1:
            return "approved"
        if iv == 0:
            return "rejected"
        return None
    raw = str(value).strip()
    if not raw:
        return None
    return _STATUS_ALIASES.get(raw) or _STATUS_ALIASES.get(raw.lower())


def parse_jsonl(text: str) -> List[Dict[str, Any]]:
    """JSONL 문자열 → record 리스트. 빈 줄은 건너뜀."""
    records: List[Dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{i}행 JSON 파싱 실패: {exc}") from exc
        if not isinstance(rec, dict):
            raise ValueError(f"{i}행: 객체(JSON object)가 아닙니다")
        records.append(rec)
    return records


def bbox_key(bbox: Any) -> str:
    if bbox is None or bbox == "":
        return ""
    if isinstance(bbox, str):
        try:
            bbox = json.loads(bbox)
        except json.JSONDecodeError:
            return bbox
    return json.dumps(bbox, ensure_ascii=False, separators=(",", ":"))


def item_fingerprint(item: Dict[str, Any]) -> tuple:
    return (
        item.get("page"),
        item.get("kind") or "",
        item.get("element_type") or "",
        bbox_key(item.get("bbox")),
        item.get("image_path") or "",
    )


def doc_name_matches(rec: Dict[str, Any], doc: Dict[str, Any]) -> bool:
    """JSONL의 file_name 또는 rel_path 가 현재 문서와 같으면 True."""
    rec_name = (rec.get("file_name") or "").strip()
    rec_rel = (rec.get("rel_path") or "").strip().replace("\\", "/")
    doc_name = (doc.get("file_name") or "").strip()
    doc_rel = (doc.get("rel_path") or "").strip().replace("\\", "/")
    if not rec_name and not rec_rel:
        return False
    if rec_name and rec_name == doc_name:
        return True
    if rec_rel and rec_rel == doc_rel:
        return True
    if rec_name and rec_name == Path(doc_rel).name:
        return True
    return False


def bbox_matches(rec: Dict[str, Any], item: Dict[str, Any]) -> bool:
    return bbox_key(rec.get("bbox")) == bbox_key(item.get("bbox"))


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
