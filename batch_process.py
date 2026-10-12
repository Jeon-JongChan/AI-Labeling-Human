"""
전체 PDF 일괄 추출 및 VLM 자동 라벨링 CLI.

UI에서 문서를 하나씩 누를 필요 없이, data/docs 내의 모든 PDF를 한 번에 추출하고
Google Gemini API(무료 티어) 또는 로컬 Qwen2.5-VL 모델로 그림/표 설명을 일괄 생성합니다.

사용법:
  # 1) 기본: GEMINI_API_KEY가 있으면 Gemini(무료 1500회) 우선, 없으면 로컬 VLM
  python tools/labeler/batch_process.py --vlm

  # 2) 공급자 명시:
  python tools/labeler/batch_process.py --vlm --provider gemini   # Gemini API 사용
  python tools/labeler/batch_process.py --vlm --provider local    # 로컬 Qwen2.5-VL 사용

  # 3) 기타 옵션:
  python tools/labeler/batch_process.py --no-ocr    # OCR 제외하고 구조 추출만
  python tools/labeler/batch_process.py --vlm --force     # 이미 추출된 문서도 강제 재추출
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import DOCS_DIR, GEMINI_API_KEY, VLM_PROVIDER, ensure_dirs  # noqa: E402
from db import LabelStore  # noqa: E402
from extract import discover_pdfs, extract_pdf  # noqa: E402
from vlm import caption_image, VlmError  # noqa: E402


def run_batch(
    *,
    use_ocr: bool = True,
    use_vlm: bool = False,
    vlm_provider: str | None = None,
    force: bool = False,
    target_doc: str | None = None,
) -> None:
    """모든 PDF를 순회하며 추출 및 라벨링을 일괄 실행합니다."""
    ensure_dirs()
    store = LabelStore()

    pdfs = discover_pdfs()
    if not pdfs:
        print(f"[batch] '{DOCS_DIR}' 경로에 PDF 파일이 없습니다.")
        return

    store.sync_docs(pdfs)
    docs = store.list_docs()

    if target_doc:
        docs = [
            d for d in docs
            if target_doc in (str(d["id"]), d["file_name"], d["rel_path"])
        ]
        if not docs:
            print(f"[batch] 지정한 문서를 찾을 수 없습니다: {target_doc}")
            return

    actual_provider = vlm_provider or VLM_PROVIDER
    total_docs = len(docs)
    print(f"\n=======================================================")
    print(f" [배치 시작] 총 {total_docs}개 문서 처리")
    print(f" - OCR: {'ON' if use_ocr else 'OFF'}")
    print(f" - VLM: {'ON (' + actual_provider + ')' if use_vlm else 'OFF'}")
    print(f" - 강제 재추출: {'ON' if force else 'OFF'}")
    print(f"=======================================================\n")

    success_count = 0
    start_total_time = time.time()

    # 1단계: 전체 문서 일괄 구조 추출
    print(f"[배치] === 1단계: 전체 {total_docs}개 문서 구조 추출 시작 ===")
    for idx, doc in enumerate(docs, start=1):
        doc_id = doc["id"]
        rel_path = doc["rel_path"]
        pdf_path = DOCS_DIR / rel_path

        if not pdf_path.is_file():
            print(f"[{idx}/{total_docs}] [파일 없음 스킵] {rel_path}")
            continue

        is_extracted = bool(doc.get("extracted_at"))
        if is_extracted and not force:
            print(f"[{idx}/{total_docs}] 이미 추출됨 (스킵): {rel_path}")
            success_count += 1
            continue

        print(f"[{idx}/{total_docs}] 추출 시작: {rel_path} ...", flush=True)
        t0 = time.time()

        try:
            items = extract_pdf(
                pdf_path=pdf_path,
                doc_id=doc_id,
                use_ocr=use_ocr,
                on_progress=lambda stage, done, total: None,
            )
            store.replace_items(doc_id, items)
            elapsed = time.time() - t0
            print(f"    [완료] 추출 완료 ({len(items)}개 요소, {elapsed:.1f}초 소요)")
            success_count += 1
        except Exception as exc:
            print(f"    [실패] 추출 실패 ({rel_path}): {exc}")

    # 2단계: 표/그림 대상 VLM 설명 일괄 생성
    if use_vlm:
        print(f"\n[배치] === 2단계: 표/그림 VLM 설명 일괄 생성 시작 ===")
        docs_for_vlm = store.list_docs()
        for idx, doc in enumerate(docs_for_vlm, start=1):
            _process_vlm_for_doc(store, doc["id"], doc["rel_path"], actual_provider)

    total_elapsed = time.time() - start_total_time
    print(f"\n=======================================================")
    print(f" [배치 완료] {success_count}/{total_docs}개 문서 성공 (총 {total_elapsed:.1f}초)")
    print(f"=======================================================\n")


def _process_vlm_for_doc(store: LabelStore, doc_id: int, rel_path: str, provider: str) -> None:
    """문서 내의 표/그림 항목에 대해 VLM 설명을 일괄 생성합니다."""
    items = store.list_items(doc_id)
    # 이미지 파일이 있고, 아직 VLM 설명(edited_text)이 없는 표/그림 대상
    targets = [
        it for it in items
        if it.get("image_path") and not (it.get("edited_text") or "").strip()
    ]

    if not targets:
        return

    print(f"    🤖 VLM 설명 생성 시작 ({provider}, 총 {len(targets)}개 표/그림) ...", flush=True)
    vlm_done = 0
    for it in targets:
        img_rel = it["image_path"]
        try:
            caption = caption_image(img_rel, provider=provider)
            if caption:
                store.update_item(it["id"], edited_text=caption)
                vlm_done += 1
                print(f"      - [{it['kind']}] 요소 #{it['order_idx']} 설명 생성 완료", flush=True)
            
            # Gemini 무료 티어의 15 RPM 속도 제한 준수 (요청 간 4초 대기)
            if provider.lower() == "gemini":
                time.sleep(4.0)

        except VlmError as err:
            print(f"      - [VLM 오류] (#{it['order_idx']}): {err}")
            print("        (VLM 서버 또는 API 키를 확인하세요. 이후 문서는 VLM을 건너뜁니다.)")
            break
        except Exception as exc:
            print(f"      - [VLM 생성 실패] (#{it['order_idx']}): {exc}")

    print(f"    [완료] VLM 설명 생성 완료 ({vlm_done}/{len(targets)}개 완료)")


def main() -> None:
    """CLI 인자 파싱 및 실행."""
    parser = argparse.ArgumentParser(description="PDF 일괄 추출 및 VLM 자동 라벨링")
    parser.add_argument("--no-ocr", action="store_true", help="EasyOCR 텍스트 추출 제외")
    parser.add_argument("--vlm", action="store_true", help="VLM 설명 자동 생성")
    parser.add_argument(
        "--provider",
        type=str,
        choices=["gemini", "local", "auto"],
        default=None,
        help="VLM 공급자 지정 (gemini: 무료 API, local: Qwen2.5-VL)",
    )
    parser.add_argument("--force", action="store_true", help="이미 추출된 문서도 강제 재추출")
    parser.add_argument("--doc", type=str, default=None, help="특정 문서(파일명 또는 ID)만 처리")

    args = parser.parse_args()
    provider_choice = None if args.provider == "auto" else args.provider

    run_batch(
        use_ocr=not args.no_ocr,
        use_vlm=args.vlm,
        vlm_provider=provider_choice,
        force=args.force,
        target_doc=args.doc,
    )


if __name__ == "__main__":
    main()
