"""
VLM 공급자 어댑터 베이스 모듈.

템플릿 메서드(Template Method) 및 어댑터(Adapter) / 전략(Strategy) 패턴의 추상 인터페이스를 제공합니다.
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from pathlib import Path

from config import VLM_PROMPT


class VlmError(RuntimeError):
    """VLM 기본 예외 클래스."""
    pass


class VlmQuotaExceededError(VlmError):
    """Gemini 무료 티어 일일 쿼터(429) 초과 시 발생."""
    pass


class VlmAbortError(VlmError):
    """재시도가 무의미하거나 위험한 오류(GPT 로그인 필요, 연속 실패 차단 등). 일괄 작업을 즉시 중단해야 함."""
    pass


class BaseVlmAdapter(ABC):
    """
    모든 VLM 공급자 어댑터의 공통 추상 기반 클래스.
    
    템플릿 메서드(caption)를 통해 파일 검증 및 전후처리를 일관되게 수행하며,
    실제 공급자별 연동 로직(_generate_caption)은 서브클래스에서 구현합니다.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """어댑터의 고유 식별 명칭."""
        pass

    def caption(self, image_path: Path, prompt: str | None = None) -> str:
        """
        [템플릿 메서드] 이미지 캡션 생성 공통 파이프라인.

        1. 이미지 파일 존재 여부 검증 (공통 선행 처리)
        2. 하위 어댑터의 실제 호출 로직 실행 (_generate_caption)
        3. 반환 텍스트 공백 제거 및 빈 문자열 검증 (공통 후행 처리)
        """
        if not image_path.is_file():
            raise VlmError(f"[{self.name}] 이미지 파일이 존재하지 않습니다: {image_path}")

        actual_prompt = prompt or VLM_PROMPT
        raw_result = self._generate_caption(image_path, actual_prompt)
        text = (raw_result or "").strip()

        if not text:
            raise VlmError(f"[{self.name}] 모델 응답 본문이 비어 있습니다.")

        return text

    @abstractmethod
    def _generate_caption(self, image_path: Path, prompt: str) -> str:
        """
        하위 어댑터에서 실제 모델/API 호출을 수행하는 추상 메서드.
        
        Args:
            image_path: 캡션을 추출할 이미지의 절대 경로
            prompt: VLM 모델에 전달할 프롬프트
        Returns:
            생성된 원본 텍스트
        """
        pass

    @staticmethod
    def encode_image_b64(image_path: Path) -> str:
        """
        이미지 파일을 base64 ascii 문자열로 변환하는 공통 유틸리티 메서드.
        """
        return base64.b64encode(image_path.read_bytes()).decode("ascii")

