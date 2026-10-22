"""
라벨링 도구 웹 서버 — 파이썬 표준 http.server (외부 프레임워크 없음).

API:
  GET    /api/docs                       PDF 목록 + 라벨 집계
  POST   /api/docs/scan                  data/docs 재탐색
  POST   /api/docs/{id}/extract          추출 시작 (백그라운드, ?ocr=0 으로 OCR 생략)
  GET    /api/docs/{id}/progress         추출 진행률
  GET    /api/docs/{id}/items            항목 목록
  PATCH  /api/items/{id}                 라벨/수정 텍스트 저장
  POST   /api/docs/{id}/import           JSONL 재업로드 (status·text 반영)
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
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

APP_DIR = Path(__file__).resolve().parent
TOOL_DIR = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(TOOL_DIR))

from config import DOCS_DIR, HOST, IMAGES_DIR, LABEL_DIR, PORT, ensure_dirs  # noqa: E402
from db import LabelStore  # noqa: E402
from export import parse_jsonl, to_html, to_jsonl  # noqa: E402
from extract import discover_pdfs, extract_pdf  # noqa: E402

STATIC_DIR = APP_DIR / "static"

_DOC_ITEMS_RE = re.compile(r"^/api/docs/(\d+)/(items|extract|progress|export|import)$")
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



_batch_lock = threading.Lock()
_batch_state = {
    "running": False,
    "mode": "vlm",
    "stage": "",
    "current": 0,
    "total": 0,
    "current_doc": "",
    "error": None,
    "stop_requested": False,
}


def _batch_vlm_for_doc(
    store: LabelStore,
    doc_id: int,
    file_name: str,
    doc_idx: int,
    total_docs: int,
    error_counter: list[int] | None = None,
    max_errors: int = 3,
) -> None:
    from vlm import VlmAbortError, caption_image
    if error_counter is None:
        error_counter = [0]
    items = store.list_items(doc_id)
    targets = [it for it in items if it.get("image_path") and not (it.get("edited_text") or "").strip()]
    if not targets:
        return
    print(f"[batch] [{doc_idx}/{total_docs}] VLM 설명 생성 시작: {file_name} ({len(targets)}개 표/그림)", flush=True)
    for t_idx, it in enumerate(targets, start=1):
        with _batch_lock:
            if _batch_state["stop_requested"]:
                break
            _batch_state["stage"] = f"[{doc_idx}/{total_docs}] {file_name} VLM 설명 생성 중 ({t_idx}/{len(targets)})..."
        try:
            caption = caption_image(it["image_path"])
            if caption:
                store.update_item(it["id"], edited_text=caption)
                print(f"[batch] [{doc_idx}/{total_docs}] - 표/그림 #{it['order_idx']} 설명 완료", flush=True)
            error_counter[0] = 0  # 성공 시 리셋
            # Gemini RPM 준수를 위한 1초 대기
            time.sleep(1.0)
        except VlmAbortError:
            raise  # 크롤러 401/423 또는 치명적 에러: 전체 일괄 분석 즉시 중단
        except Exception as exc:
            error_counter[0] += 1
            print(f"[batch VLM] 실패 (#{it['order_idx']}): {exc} (연속 {error_counter[0]}/{max_errors}회)", flush=True)
            if error_counter[0] >= max_errors:
                raise VlmAbortError(
                    f"VLM 연속 {error_counter[0]}회 실패로 계정 보호 및 추가 오류 방지를 위해 작업을 자동 중단합니다: {exc}"
                )
            continue


def _run_batch_worker(use_ocr: bool = True, use_vlm: bool = True, force: bool = False) -> None:
    store = LabelStore()
    success_count = 0
    fail_count = 0
    try:
        pdfs = discover_pdfs()
        store.sync_docs(pdfs)
        docs = store.list_docs()
        total = len(docs)

        print(f"\n=======================================================", flush=True)
        print(f"[batch] 웹 요청으로 전체 일괄 분석 시작 (총 {total}개 문서)", flush=True)
        print(f"=======================================================\n", flush=True)

        with _batch_lock:
            _batch_state.update({
                "running": True,
                "mode": "vlm" if not use_ocr else "all",
                "current": 0,
                "total": total,
                "stage": f"총 {total}개 문서 분석 시작...",
                "error": None,
                "stop_requested": False,
            })

        # 1단계: 모든 문서의 텍스트·표·이미지 일괄 구조 추출 (미추출 문서 우선 해결)
        print(f"[batch] === 1단계: 전체 {total}개 문서 구조 추출 시작 ===", flush=True)
        for idx, doc in enumerate(docs, start=1):
            with _batch_lock:
                if _batch_state["stop_requested"]:
                    _batch_state["stage"] = "사용자에 의해 일괄 분석이 중단되었습니다."
                    print("[batch] 일괄 분석이 사용자에 의해 중단되었습니다.", flush=True)
                    break
                _batch_state.update({
                    "current": idx,
                    "current_doc": doc["file_name"],
                    "stage": f"[1단계: 구조 추출 {idx}/{total}] {doc['file_name']}",
                })

            doc_id = doc["id"]
            rel_path = doc["rel_path"]
            pdf_path = DOCS_DIR / rel_path

            if not pdf_path.is_file():
                continue

            if doc.get("extracted_at") and not force:
                success_count += 1
                continue

            print(f"[batch] [1단계 추출 {idx}/{total}] {doc['file_name']} ...", flush=True)
            def _on_sub_progress(stage: str, done: int, sub_total: int) -> None:
                with _batch_lock:
                    if sub_total > 0:
                        _batch_state["stage"] = f"[1단계: 추출 {idx}/{total}] {doc['file_name']} - {stage} ({done}/{sub_total})"
                    else:
                        _batch_state["stage"] = f"[1단계: 추출 {idx}/{total}] {doc['file_name']} - {stage}"

            try:
                items = extract_pdf(pdf_path, doc_id, use_ocr=use_ocr, on_progress=_on_sub_progress)
                store.replace_items(doc_id, items)
                success_count += 1
                print(f"[batch] [1단계 완료 {idx}/{total}] {doc['file_name']} ({len(items)}개 요소)", flush=True)
            except Exception as exc:
                fail_count += 1
                print(f"[batch] [1단계 실패 {idx}/{total}] {rel_path}: {exc}", flush=True)

        # 2단계: 표/그림 VLM 설명 일괄 생성 (추출 완료 후 순차 처리)
        if use_vlm and not _batch_state["stop_requested"]:
            print(f"\n[batch] === 2단계: 표/그림 VLM 설명 자동 생성 시작 ===", flush=True)
            docs_for_vlm = store.list_docs()
            vlm_error_counter = [0]
            for idx, doc in enumerate(docs_for_vlm, start=1):
                with _batch_lock:
                    if _batch_state["stop_requested"]:
                        break
                    _batch_state.update({
                        "current": idx,
                        "current_doc": doc["file_name"],
                        "stage": f"[2단계: VLM 설명 {idx}/{total}] {doc['file_name']}",
                    })
                _batch_vlm_for_doc(store, doc["id"], doc["file_name"], idx, total, error_counter=vlm_error_counter)

        with _batch_lock:
            if not _batch_state["stop_requested"]:
                _batch_state.update({"stage": f"모든 문서 일괄 분석 완료! (성공: {success_count}개, 실패: {fail_count}개)", "running": False})
                print(f"\n[batch] 모든 문서 일괄 분석 완료! (성공: {success_count}개, 실패: {fail_count}개)\n", flush=True)
            else:
                _batch_state["running"] = False
    except Exception as exc:
        with _batch_lock:
            _batch_state.update({"stage": f"오류 발생으로 중단됨: {exc}", "error": str(exc), "running": False})
            print(f"[batch] 치명적 오류 또는 작업 중단: {exc}", flush=True)
    finally:
        store.close()


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

        if path == "/api/batch/status":
            with _batch_lock:
                return self._send_json(HTTPStatus.OK, dict(_batch_state))

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

        if path == "/api/batch/start":
            with _batch_lock:
                if _batch_state["running"]:
                    return self._send_json(HTTPStatus.CONFLICT, {"error": "이미 일괄 분석이 진행 중입니다."})
            force = query.get("force", ["0"])[0] in ("1", "true")
            use_ocr = query.get("ocr", ["1"])[0] not in ("0", "false")
            use_vlm = query.get("vlm", ["1"])[0] not in ("0", "false")
            threading.Thread(
                target=_run_batch_worker,
                kwargs={"use_ocr": use_ocr, "use_vlm": use_vlm, "force": force},
                daemon=True,
            ).start()
            return self._send_json(HTTPStatus.ACCEPTED, {"ok": True})

        if path == "/api/batch/stop":
            with _batch_lock:
                _batch_state["stop_requested"] = True
            return self._send_json(HTTPStatus.OK, {"ok": True})

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

        if m and m.group(2) == "import":
            return self._api_import(int(m.group(1)))

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
            default_images = "1" if scope == "approved" else "0"
            include_images = query.get("images", [default_images])[0] not in (
                "0",
                "false",
            )
            body = to_jsonl(
                doc, items, scope, include_images=include_images
            ).encode("utf-8")
            name = f"{stem}.labeled.{scope}.jsonl"
            return self._send_bytes(
                body, "application/x-ndjson; charset=utf-8", download_name=name
            )
        body = to_html(doc, items, scope).encode("utf-8")
        name = f"{stem}.labeled.{scope}.html"
        self._send_bytes(body, "text/html; charset=utf-8", download_name=name)

    def _api_import(self, doc_id: int) -> None:
        data = self._read_json()
        raw = data.get("jsonl")
        if not isinstance(raw, str) or not raw.strip():
            return self._send_json(
                HTTPStatus.BAD_REQUEST, {"error": "jsonl 본문이 없습니다"}
            )
        try:
            records = parse_jsonl(raw)
        except ValueError as exc:
            return self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        if not records:
            return self._send_json(
                HTTPStatus.BAD_REQUEST, {"error": "JSONL에 항목이 없습니다"}
            )

        store = LabelStore()
        try:
            doc = store.get_doc(doc_id)
            if not doc:
                return self._send_json(HTTPStatus.NOT_FOUND, {"error": "doc not found"})
            if not store.list_items(doc_id):
                return self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "아직 추출된 항목이 없습니다. 먼저 추출하세요."},
                )
            result = store.import_jsonl_records(doc_id, records)
        finally:
            store.close()
        self._send_json(HTTPStatus.OK, result)


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
