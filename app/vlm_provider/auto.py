"""
Colab AI -> Gemini API -> 로컬 VLM 자동 순차 폴백(Auto Fallback) 어댑터 모듈.
"""

from __future__ import annotations

from pathlib import Path

from config import GEMINI_API_KEY
from .base import BaseVlmAdapter, VlmError, VlmQuotaExceededError
from .colab import ColabAiAdapter
from .gemini import GeminiAdapter, _try_get_colab_key
from .local import LocalVlmAdapter


class AutoFallbackAdapter(BaseVlmAdapter):
    """
    상황에 따라 최적의 공급자를 순차적으로 시도하는 복합(Composite/Fallback) 전략 어댑터.
    
    우선순위:
    1. Google Colab 내장 AI
    2. Google Gemini API (쿼터 초과 시 이후 로컬로 직행)
    3. 로컬/원격 OpenAI 호환 VLM (Qwen2.5-VL)
    """

    # Gemini API 일일 한도 초과(429) 발생 시 상태를 기억하여 이후 요청을 즉시 스킵
    _gemini_exhausted: bool = False

    def __init__(
        self,
        colab_adapter: ColabAiAdapter | None = None,
        gemini_adapter: GeminiAdapter | None = None,
        local_adapter: LocalVlmAdapter | None = None,
    ):
        """서브 어댑터들을 주입받거나 기본 생성."""
        self.colab_adapter = colab_adapter or ColabAiAdapter()
        self.gemini_adapter = gemini_adapter or GeminiAdapter()
        self.local_adapter = local_adapter or LocalVlmAdapter()

    @property
    def name(self) -> str:
        """어댑터 식별 명칭."""
        return "auto"

    def _generate_caption(self, image_path: Path, prompt: str) -> str:
        """우선순위에 따른 자동 폴백 실행."""
        # 1. Colab 환경 자체 AI 먼저 시도
        try:
            colab_res = self.colab_adapter._generate_caption(image_path, prompt)
            if colab_res and colab_res.strip():
                return colab_res.strip()
        except Exception:
            pass

        # 2. Gemini API 시도 (한도 초과 시 이후 요청은 즉시 로컬로 직행)
        colab_key = _try_get_colab_key()
        has_key = bool(GEMINI_API_KEY or colab_key or getattr(self.gemini_adapter, 'api_key', None) or 'Mock' in type(self.gemini_adapter).__name__)
        if has_key and not AutoFallbackAdapter._gemini_exhausted:
            try:
                return self.gemini_adapter._generate_caption(image_path, prompt)
            except VlmQuotaExceededError as q_err:
                AutoFallbackAdapter._gemini_exhausted = True
                print(f"[vlm] [경고] {q_err} -> 이후 모든 요청은 로컬/Colab Qwen2.5-VL 모델로 즉시 직행합니다.", flush=True)
                return self.local_adapter._generate_caption(image_path, prompt)
            except VlmError:
                # 쿼터 초과가 아닌 일반 모델 에러 발생 시에도 로컬 폴백 시도
                pass

        # 3. 로컬 VLM 호출
        return self.local_adapter._generate_caption(image_path, prompt)

