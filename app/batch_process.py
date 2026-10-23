"""
전체 PDF 일괄 추출 및 VLM 자동 라벨링 CLI.

UI에서 문서를 하나씩 누를 필요 없이, data/docs 내의 모든 PDF를 한 번에 추출하고
Google Gemini API(무료 티어), dsh-our-free-model 기반 무료 VLM, 또는 로컬 Qwen2.5-VL 모델로
그림/표 설명을 일괄 생성합니다.

사용법:
  # 1) 기본: GEMINI_API_KEY가 있으면 Gemini(무료 1500회) 우선, 없으면 로컬 VLM
  python tools/labeler/batch_process.py --vlm

  # 2) 공급자 및 모델 지정:
  python tools/labeler/batch_process.py --vlm --provider gemini --model all    # Gemini 지원 모델 전부 순차 사용 + 1시간 Sleep 복구
  python tools/labeler/batch_process.py --vlm --provider free --model all      # 완전 무료 VLM 게이트웨이 순차 사용 + 1시간 Sleep 복구
  python tools/labeler/batch_process.py --vlm --provider local                 # 로컬 Qwen2.5-VL 사용

  # 3) 기타 옵션:
  python tools/labeler/batch_process.py --no-ocr    # OCR 제외하고 구조 추출만
  python tools/labeler/batch_process.py --vlm --force     # 이미 추출된 문서도 강제 재추출
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
TOOL_DIR = APP_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
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
from vlm import caption_image, VlmAbortError, VlmError  # noqa: E402


def run_batch(
    *,
    use_ocr: bool = True,
    use_vlm: bool = False,
    vlm_provider: str | None = None,
    vlm_model: str | None = None,
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
    model_str = f" / {vlm_model}" if vlm_model else ""
    total_docs = len(docs)
    print(f"\n=======================================================")
    print(f" [배치 시작] 총 {total_docs}개 문서 처리")
    print(f" - OCR: {'ON' if use_ocr else 'OFF'}")
    print(f" - VLM: {'ON (' + actual_provider + model_str + ')' if use_vlm else 'OFF'}")
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
        if actual_provider.lower() in ("gpt", "google"):
            try:
                import requests
                from config import GOOGLE_CRAWLER_URL, GPT_CRAWLER_URL
                url = GOOGLE_CRAWLER_URL if actual_provider.lower() == "google" else GPT_CRAWLER_URL
                requests.get(f"{url.rstrip('/')}/reset", timeout=3)
            except Exception:
                pass
        docs_for_vlm = store.list_docs()
        vlm_error_counter = [0]
        for idx, doc in enumerate(docs_for_vlm, start=1):
            _process_vlm_for_doc(
                store,
                doc["id"],
                doc["rel_path"],
                actual_provider,
                model=vlm_model,
                error_counter=vlm_error_counter,
            )

    total_elapsed = time.time() - start_total_time
    print(f"\n=======================================================")
    print(f" [배치 완료] {success_count}/{total_docs}개 문서 성공 (총 {total_elapsed:.1f}초)")
    print(f"=======================================================\n")


def _process_vlm_for_doc(
    store: LabelStore,
    doc_id: int,
    rel_path: str,
    provider: str,
    model: str | None = None,
    error_counter: list[int] | None = None,
    max_errors: int = 3,
) -> None:
    """문서 내의 표/그림 항목에 대해 VLM 설명을 일괄 생성합니다."""
    if error_counter is None:
        error_counter = [0]
    items = store.list_items(doc_id)
    # 이미지 파일이 있고, 아직 VLM 설명(edited_text)이 없는 표/그림 대상
    targets = [
        it for it in items
        if it.get("image_path") and not (it.get("edited_text") or "").strip()
    ]

    if not targets:
        return

    model_display = f" (모델: {model})" if model else ""
    doc_name = Path(rel_path).name
    total_targets = len(targets)
    print(f"    🤖 [{doc_name}] VLM 설명 생성 시작 ({provider}{model_display}, 총 {total_targets}개 표/그림) ...", flush=True)
    vlm_done = 0
    for it_idx, it in enumerate(targets, start=1):
        img_rel = it["image_path"]
        try:
            caption = caption_image(img_rel, provider=provider, model=model)
            if caption:
                store.update_item(it["id"], edited_text=caption)
                vlm_done += 1
                one_line = caption.strip().replace("\r", "").replace("\n", " ")
                preview = (one_line[:70] + "...") if len(one_line) > 70 else one_line
                print(f"      - [{doc_name}] [{it_idx}/{total_targets}] {it['kind']} #{it['order_idx']} 설명 완료: \"{preview}\"", flush=True)
            error_counter[0] = 0  # 성공 시 리셋
            
            # Gemini 무료 티어의 15 RPM 속도 제한 준수 (요청 간 4초 대기)
            if provider.lower() == "gemini":
                time.sleep(4.0)

        except VlmAbortError:
            raise  # GPT 로그인 필요/연속 실패: 모든 문서 처리 즉시 중단
        except (VlmError, Exception) as exc:
            error_counter[0] += 1
            print(f"      - [VLM 실패] (#{it['order_idx']}): {exc} (연속 {error_counter[0]}/{max_errors}회)")
            if error_counter[0] >= max_errors:
                raise VlmAbortError(
                    f"VLM 연속 {error_counter[0]}회 실패로 계정 보호 및 추가 오류 방지를 위해 작업을 자동 중단합니다: {exc}"
                )
            continue

    print(f"    [완료] VLM 설명 생성 완료 ({vlm_done}/{len(targets)}개 완료)")


def main() -> None:
    """CLI 인자 파싱 및 실행."""
    parser = argparse.ArgumentParser(description="PDF 일괄 추출 및 VLM 자동 라벨링")
    parser.add_argument("--no-ocr", action="store_true", help="EasyOCR 텍스트 추출 제외")
    parser.add_argument("--vlm", action="store_true", help="VLM 설명 자동 생성")
    parser.add_argument(
        "--provider",
        type=str,
        choices=["gemini", "free", "free_model", "local", "gpt", "google", "colab", "auto"],
        default=None,
        help="VLM 공급자 지정 (gemini: Google Gemini API, free/free_model: 무료 VLM 게이트웨이, local: Qwen2.5-VL, gpt: ChatGPT 웹 크롤러, google: Google AI 모드 크롤러)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="VLM 모델 지정 ('all' 지정 시 지원 모델 목록 순차 폴백 및 1시간 Sleep 복구 활성화)",
    )
    parser.add_argument("--force", action="store_true", help="이미 추출된 문서도 강제 재추출")
    parser.add_argument("--doc", type=str, default=None, help="특정 문서(파일명 또는 ID)만 처리")

    args = parser.parse_args()
    provider_choice = None if args.provider == "auto" else args.provider

    try:
        run_batch(
            use_ocr=not args.no_ocr,
            use_vlm=args.vlm,
            vlm_provider=provider_choice,
            vlm_model=args.model,
            force=args.force,
            target_doc=args.doc,
        )
    except VlmAbortError as err:
        print(f"[중단] {err}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
