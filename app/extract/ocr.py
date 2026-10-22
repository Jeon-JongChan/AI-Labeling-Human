"""
EasyOCR 광학 문자 인식 모듈 (GPU 우선 가속 및 CPU 스레드 제어).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config import OCR_CPU_THREADS, OCR_GPU, OCR_LANGS

_ocr_reader: Any = None
_ocr_failed: str | None = None


def get_ocr_reader() -> Any:
    """EasyOCR Reader 인스턴스를 반환 (싱글톤 패턴, GPU 가속 우선 및 실패 시 CPU 폴백)."""
    global _ocr_reader, _ocr_failed
    if _ocr_reader is not None or _ocr_failed is not None:
        return _ocr_reader

    try:
        import easyocr
        import torch

        # CPU 사용률 100% 폭주 방지: 스레드 수 제한
        if OCR_CPU_THREADS > 0:
            try:
                torch.set_num_threads(OCR_CPU_THREADS)
            except Exception:
                pass

        langs = "+".join(OCR_LANGS)
        can_use_gpu = OCR_GPU and torch.cuda.is_available()

        if can_use_gpu:
            try:
                dev_name = torch.cuda.get_device_name(0)
                print(f"[doc_extract] EasyOCR GPU({dev_name}) 모드로 초기화 ({langs})...", flush=True)
                _ocr_reader = easyocr.Reader(list(OCR_LANGS), gpu=True, verbose=False)
                return _ocr_reader
            except Exception as gpu_exc:
                print(f"[doc_extract] EasyOCR GPU 초기화 실패 ({gpu_exc}) -> CPU 모드로 자동 전환합니다.", flush=True)

        gpu_hint = "CUDA 미지원 PyTorch" if not torch.cuda.is_available() else "설정 비활성화"
        print(f"[doc_extract] EasyOCR CPU 모드로 로딩 ({gpu_hint}, 최대 {OCR_CPU_THREADS}스레드, {langs})...", flush=True)
        _ocr_reader = easyocr.Reader(list(OCR_LANGS), gpu=False, verbose=False)
    except Exception as exc:
        _ocr_failed = str(exc)
        print(f"[doc_extract] EasyOCR 사용 불가 -> OCR 생략: {exc}", flush=True)

    return _ocr_reader


def ocr_image(image_path: Path) -> str:
    """이미지 파일에서 OCR 텍스트를 추출하여 반환."""
    reader = get_ocr_reader()
    if reader is None:
        return ""
    try:
        results = reader.readtext(str(image_path), detail=0, paragraph=True)
        return "\n".join(str(r).strip() for r in results if str(r).strip())
    except Exception as exc:
        print(f"[doc_extract] OCR 실패 ({image_path.name}): {exc}", flush=True)
        return ""

