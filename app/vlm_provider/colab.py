"""
Google Colab 환경 내장 AI 연동 어댑터 모듈.
"""

from __future__ import annotations

from pathlib import Path
from .base import BaseVlmAdapter, VlmError


class ColabAiAdapter(BaseVlmAdapter):
    """Google Colab의 자체 AI (google.colab.ai) 인터페이스 어댑터."""

    @property
    def name(self) -> str:
        """어댑터 식별 명칭."""
        return "colab"

    def _generate_caption(self, image_path: Path, prompt: str) -> str:
        """google.colab.ai 모듈을 통한 이미지 설명 생성."""
        try:
            import google.colab.ai  # type: ignore
            if hasattr(google.colab.ai, "generate"):
                res = google.colab.ai.generate(prompt=prompt, image=str(image_path))
                if res and isinstance(res, str):
                    return res.strip()
            raise VlmError("google.colab.ai.generate 인터페이스를 찾을 수 없거나 결과가 올바르지 않습니다.")
        except ImportError as exc:
            raise VlmError("google.colab.ai 모듈을 불러올 수 없습니다 (코랩 환경이 아님).") from exc
        except Exception as exc:
            raise VlmError(f"Colab AI 호출 실패: {exc}") from exc

