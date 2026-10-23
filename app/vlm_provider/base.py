"""
VLM 공급자 어댑터 베이스 모듈.

템플릿 메서드(Template Method) 및 어댑터(Adapter) / 전략(Strategy) 패턴의 추상 인터페이스를 제공합니다.
"""

from __future__ import annotations

import base64
import re
from abc import ABC, abstractmethod
from pathlib import Path

from config import VLM_PROMPT

RE_CLOSING_PATTERNS = [
    # 1. 챗봇 대화/안내/유도 멘트 (~말씀해 주세요, ~알려주세요, ~바랍니다, ~필요하시면 등)
    re.compile(
        r"(?:(?:필요|궁금|추가|자세한\s*설명|도움|언제든|수행할\s*작업|특정\s*작업|요청|원하시|의견|질문).*?"
        r"(?:말씀해\s*주세요|말씀\s*주세요|알려주세요|알려\s*주세요|질문해\s*주세요|바랍니다|요청해\s*주세요|말씀하세요|문의하세요|확인하세요|남겨주세요|부탁드립니다|가요|나요)"
        r"[\.!\s]*$)",
        re.I,
    ),
    # 2. 질문문으로 끝나는 맺음말
    re.compile(
        r"(?:(?:필요하신가요|진행해야\s*하나요|도움이\s*필요하신가요|궁금한\s*점|원하시나요|알려드릴까요|확인해\s*드릴까요)"
        r"[\.!\?\s]*$)",
        re.I,
    ),
    # 3. 영문 챗봇 맺음말
    re.compile(
        r"(?:(?:let\s+me\s+know|feel\s+free\s+to|hope\s+this\s+helps|if\s+you\s+need|any\s+questions).*"
        r"[\.!\?\s]*$)",
        re.I,
    ),
]

RE_OPENING_NOISE = re.compile(
    r"^(?:"
    r"(?:제시된|해당|첨부된|이)?\s*(?:이미지|사진|표|문서|파일)[^:\n]*?(?:내용은|텍스트는|정보는)?\s*(?:다음과\s*같습니다|아래와\s*같습니다|정리한\s*내용입니다)[\s:]*|"
    r"다음을\s*보냈습니다[^\n]*|"
    r"AI\s*모드\s*대화[^\n]*|"
    r"here\s+(?:is|are)\s+the\s+(?:extracted\s+text|data|content)[^:\n]*:?"
    r")$",
    re.IGNORECASE,
)


def is_conversational_sentence(s: str) -> bool:
    """문장 또는 라인이 챗봇형 대화 유도/사족 문구인지 검사."""
    s = s.strip()
    if not s:
        return False
    return any(p.search(s) for p in RE_CLOSING_PATTERNS)


def strip_conversational_noise(text: str) -> str:
    """VLM 모델이 생성한 텍스트에서 사고 과정(Thinking Process/<think>), 서두/말미의 챗봇형 사족 및 대화 문장을 안전하게 제거."""
    if not text:
        return ""

    # 0. 추론/사고 과정 태그 (<think>...</think>) 제거
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()

    # 0-1. 텍스트형 사고 과정 블록 (Thinking Process: ...) 처리
    if re.match(r"^\s*(?:Thinking Process|Thought|Reasoning Process|내부 생각 과정):", text, re.IGNORECASE):
        split_match = re.search(
            r"\n\n(?:Final (?:Answer|Output|Response)|최종 (?:답변|결과|출력)|Output|Response):\s*\n",
            text,
            re.IGNORECASE,
        )
        if split_match:
            text = text[split_match.end():].strip()
        else:
            # 최종 본문 없이 사고 과정만 출력되다 잘린 경우 빈 문자열로 반환하여 재시도/폴백 유도
            return ""

    lines = [line.rstrip() for line in text.splitlines()]

    # 1. 시작 부분 사족 라인 제거
    while lines and (not lines[0].strip() or RE_OPENING_NOISE.search(lines[0].strip())):
        lines.pop(0)

    # 2. 끝 부분 검사: 라인 전체가 사족이거나, 라인 내부 마지막 문장이 사족이면 잘라냄
    while lines:
        last_line = lines[-1].strip()
        if not last_line:
            lines.pop()
            continue

        # 마지막 라인을 문장 단위(. ? !)로 분리하여 끝부분의 사족 문장들을 순차적으로 자름
        sentences = re.split(r"(?<=[.?!])\s+", last_line)
        while sentences and is_conversational_sentence(sentences[-1]):
            sentences.pop()

        if not sentences:
            # 해당 라인의 모든 문장이 사족이었으면 라인 제거 후 윗라인 계속 검사
            lines.pop()
            continue

        # 사족 문장이 잘려나간 문장들로 마지막 라인 복원
        lines[-1] = " ".join(sentences).strip()
        break

    return "\n".join(lines).strip()


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
        text = strip_conversational_noise(raw_result or "")

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

