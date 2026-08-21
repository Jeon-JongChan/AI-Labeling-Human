"""
라벨링 도구 웹 서버 — 파이썬 표준 http.server (외부 프레임워크 없음).

API:
  GET    /api/docs                       PDF 목록 + 라벨 집계
  POST   /api/docs/scan                  data/docs 재탐색
  POST   /api/docs/{id}/extract          추출 시작 (백그라운드, ?ocr=0 으로 OCR 생략)
  GET    /api/docs/{id}/progress         추출 진행률
  GET    /api/docs/{id}/items            항목 목록
  PATCH  /api/items/{id}                 라벨/수정 텍스트 저장
  POST   /api/items/{id}/caption         VLM으로 이미지 설명 생성 (VL 서버 필요)
  GET    /api/docs/{id}/export           내보내기 ?format=jsonl|html&scope=approved|all
정적:
  /            static/index.html
  /static/*    프론트엔드
  /images/*    크롭 이미지 (data/labeling/images)

실행: python tools/labeler/server.py  → http://localhost:8788
경로: tools/labeler/config.py 또는 LABELER_DOCS_DIR / LABELER_DATA_DIR
"""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))

from config import DOCS_DIR, HOST, IMAGES_DIR, LABEL_DIR, PORT, ensure_dirs  # noqa: E402
from db import LabelStore  # noqa: E402
from export import to_html, to_jsonl  # noqa: E402
from extract import discover_pdfs, extract_pdf  # noqa: E402

STATIC_DIR = TOOL_DIR / "static"

_DOC_ITEMS_RE = re.compile(r"^/api/docs/(\d+)/(items|extract|progress|export)$")
_ITEM_RE = re.compile(r"^/api/items/(\d+)$")
_ITEM_CAPTION_RE = re.compile(r"^/api/items/(\d+)/caption$")

# doc_id → {stage, done, total, running, error}
_progress: dict[int, dict] = {}
_progress_lock = threading.Lock()

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _set_progress(doc_id: int, **fields) -> None:
    with _progress_lock:
        cur = _progress.setdefault(
            doc_id, {"stage": "", "done": 0, "total": 0, "running": False, "error": None}
        )
        cur.update(fields)


def _get_progress(doc_id: int) -> dict:
    with _progress_lock:
        return dict(
            _progress.get(
                doc_id,
                {"stage": "", "done": 0, "total": 0, "running": False, "error": None},
            )
        )


def _run_extract(doc_id: int, rel_path: str, use_ocr: bool) -> None:
    pdf_path = DOCS_DIR / rel_path
    try:
        def on_progress(stage: str, done: int, total: int) -> None:
            _set_progress(doc_id, stage=stage, done=done, total=total)

        items = extract_pdf(
            pdf_path, doc_id, use_ocr=use_ocr, on_progress=on_progress
        )
        store = LabelStore()
        try:
            count = store.replace_items(doc_id, items)
        finally:
            store.close()
        _set_progress(
            doc_id, stage=f"완료 — {count}개 항목", running=False, error=None
        )
        print(f"[labeler] 추출 완료: {rel_path} → {count}개", flush=True)
    except Exception as exc:
        _set_progress(doc_id, running=False, error=f"{type(exc).__name__}: {exc}")
        print(f"[labeler] 추출 실패: {rel_path} — {exc}", flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # 요청 로그 축약
        pass

    # -- 공통 ----------------------------------------------------------------

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(
        self, body: bytes, content_type: str, download_name: str | None = None
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if download_name:
            quoted = urllib.parse.quote(download_name)
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{quoted}",
            )
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _serve_file(self, path: Path) -> None:
        if not path.is_file():
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        content_type = _MIME.get(path.suffix.lower(), "application/octet-stream")
        self._send_bytes(path.read_bytes(), content_type)

    # -- 라우팅 ---------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            return self._serve_file(STATIC_DIR / "index.html")
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            target = (STATIC_DIR / rel).resolve()
            if not str(target).startswith(str(STATIC_DIR.resolve())):
                return self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return self._serve_file(target)
        if path.startswith("/images/"):
            rel = path[len("/images/"):]
            target = (IMAGES_DIR / urllib.parse.unquote(rel)).resolve()
            if not str(target).startswith(str(IMAGES_DIR.resolve())):
                return self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return self._serve_file(target)

        if path == "/api/docs":
            store = LabelStore()
            try:
                return self._send_json(HTTPStatus.OK, {"docs": store.list_docs()})
            finally:
                store.close()

        m = _DOC_ITEMS_RE.match(path)
        if m:
            doc_id, action = int(m.group(1)), m.group(2)
            if action == "progress":
                return self._send_json(HTTPStatus.OK, _get_progress(doc_id))
            if action == "items":
                store = LabelStore()
                try:
                    doc = store.get_doc(doc_id)
                    if not doc:
                        return self._send_json(
                            HTTPStatus.NOT_FOUND, {"error": "doc not found"}
                        )
                    return self._send_json(
                        HTTPStatus.OK,
                        {"doc": doc, "items": store.list_items(doc_id)},
                    )
                finally:
                    store.close()
            if action == "export":
                return self._api_export(doc_id, query)

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/docs/scan":
            pdfs = discover_pdfs()
            store = LabelStore()
            try:
                store.sync_docs(pdfs)
                return self._send_json(
                    HTTPStatus.OK, {"docs": store.list_docs(), "found": len(pdfs)}
                )
            finally:
                store.close()

        m = _DOC_ITEMS_RE.match(path)
        if m and m.group(2) == "extract":
            doc_id = int(m.group(1))
            use_ocr = query.get("ocr", ["1"])[0] not in ("0", "false")
            store = LabelStore()
            try:
                doc = store.get_doc(doc_id)
            finally:
                store.close()
            if not doc:
                return self._send_json(HTTPStatus.NOT_FOUND, {"error": "doc not found"})
            if _get_progress(doc_id)["running"]:
                return self._send_json(
                    HTTPStatus.CONFLICT, {"error": "이미 추출 중입니다"}
                )
            if not (DOCS_DIR / doc["rel_path"]).is_file():
                return self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": f"PDF 파일 없음: {doc['rel_path']}"},
                )
            _set_progress(
                doc_id, stage="대기", done=0, total=0, running=True, error=None
            )
            threading.Thread(
                target=_run_extract,
                args=(doc_id, doc["rel_path"], use_ocr),
                daemon=True,
            ).start()
            return self._send_json(HTTPStatus.ACCEPTED, {"ok": True})

        m = _ITEM_CAPTION_RE.match(path)
        if m:
            return self._api_caption(int(m.group(1)))

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _api_caption(self, item_id: int) -> None:
        """이미지 항목을 VL 모델로 설명 생성 (동기 — VLM 응답까지 대기)."""
        from vlm import VlmError, caption_image

        store = LabelStore()
        try:
            item = store.get_item(item_id)
        finally:
            store.close()
        if not item:
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "item not found"})
        if not item.get("image_path"):
            return self._send_json(
                HTTPStatus.BAD_REQUEST, {"error": "이미지가 없는 항목입니다"}
            )
        try:
            caption = caption_image(item["image_path"])
        except VlmError as exc:
            return self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        except Exception as exc:
            return self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"{type(exc).__name__}: {exc}"},
            )
        self._send_json(HTTPStatus.OK, {"caption": caption})

    def do_PATCH(self) -> None:  # noqa: N802
        m = _ITEM_RE.match(urllib.parse.urlparse(self.path).path)
        if not m:
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        item_id = int(m.group(1))
        data = self._read_json()

        status = data.get("status")
        if status is not None and status not in ("pending", "approved", "rejected"):
            return self._send_json(HTTPStatus.BAD_REQUEST, {"error": "잘못된 status"})
        edited_text = data.get("edited_text")
        if status is None and edited_text is None:
            return self._send_json(HTTPStatus.BAD_REQUEST, {"error": "변경 내용 없음"})

        store = LabelStore()
        try:
            ok = store.update_item(item_id, status=status, edited_text=edited_text)
        finally:
            store.close()
        if not ok:
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "item not found"})
        self._send_json(HTTPStatus.OK, {"ok": True})

    # -- 내보내기 -------------------------------------------------------------

    def _api_export(self, doc_id: int, query: dict) -> None:
        fmt = query.get("format", ["jsonl"])[0]
        scope = query.get("scope", ["approved"])[0]
        if fmt not in ("jsonl", "html") or scope not in ("approved", "all"):
            return self._send_json(HTTPStatus.BAD_REQUEST, {"error": "잘못된 파라미터"})

        store = LabelStore()
        try:
            doc = store.get_doc(doc_id)
            if not doc:
                return self._send_json(HTTPStatus.NOT_FOUND, {"error": "doc not found"})
            items = store.list_items(doc_id)
        finally:
            store.close()

        stem = Path(doc["file_name"]).stem
        if fmt == "jsonl":
            body = to_jsonl(doc, items, scope).encode("utf-8")
            name = f"{stem}.labeled.{scope}.jsonl"
            return self._send_bytes(
                body, "application/x-ndjson; charset=utf-8", download_name=name
            )
        body = to_html(doc, items, scope).encode("utf-8")
        name = f"{stem}.labeled.{scope}.html"
        self._send_bytes(body, "text/html; charset=utf-8", download_name=name)


def main() -> None:
    ensure_dirs()
    # 시작 시 docs 자동 스캔
    pdfs = discover_pdfs()
    store = LabelStore()
    try:
        store.sync_docs(pdfs)
        n_docs = len(store.list_docs())
    finally:
        store.close()

    print(f"[labeler] DOCS_DIR = {DOCS_DIR}")
    print(f"[labeler] LABEL_DIR = {LABEL_DIR}")
    print(f"[labeler] PDF {len(pdfs)}개 탐색 (등록 {n_docs}개)")
    print(f"[labeler] http://localhost:{PORT}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[labeler] 종료")


if __name__ == "__main__":
    main()
