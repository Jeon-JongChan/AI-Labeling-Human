"""
오픈소스 dsh-our-free-model 기반 무료 VLM(Vision-Language Model) 연동 어댑터 모듈.

별도의 유료 API Key나 복잡한 로그인/가입 없이, Kilo AI 퍼블릭 게이트웨이의
검증된 고성능 무료 비전 모델(Nemotron Super-VL, Dots-3 Note, OpenRouter Free 등)을
gemini all 과 동일한 방식으로 작동시킵니다:
1. 'all' 지정 시 가용 무료 VLM 목록을 수집하여 성능/컨텍스트 순으로 정렬 후 순차 사용.
2. 특정 모델의 한도(429) 또는 일시 장애(5xx) 발생 시 다음 가용 모델로 즉시 자동 전환.
3. 모든 무료 모델 소진 시 1시간 주기 Sleep 모드로 들어가 복구 여부를 확인하고 자동 재개.
4. 모델 및 환경 설정(FREE_MODEL_*)은 외부 config에 의존하지 않고 본 어댑터 모듈에 완전 귀속됩니다.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional, Set
import requests

from config import (
    VLM_MAX_TOKENS,
    VLM_TEMPERATURE,
    VLM_TIMEOUT,
)
from .base import BaseVlmAdapter, VlmError, VlmQuotaExceededError

# ---------------------------------------------------------------------------
# FreeModel 전용 환경 설정 (해당 어댑터에 완전 귀속)
# ---------------------------------------------------------------------------
FREE_MODEL_BASE_URL: str = os.getenv("FREE_MODEL_BASE_URL", "https://api.kilo.ai/api/gateway").strip().rstrip("/")
FREE_MODEL: str = os.getenv("FREE_MODEL", "all").strip()
FREE_MODEL_SLEEP_INTERVAL: float = float(os.getenv("FREE_MODEL_SLEEP_INTERVAL", "3600.0"))

# 사용자 편의를 위한 모델 별칭(Alias) 맵
MODEL_ALIASES: dict[str, str] = {
    "dots": "dots-studio/dots-3-note-preview:free",
    "nemotron": "nvidia/nemotron-3.5-super-vl:free",
    "omni": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "openrouter": "openrouter/free",
    "safety": "nvidia/nemotron-3.5-content-safety:free",
    "kilo": "kilo-auto/free",
    "beam": "reflection/beam-free",
    "flint": "recursive/flint-v1.0:free",
    "lfm": "liquid/lfm-2.5-2.6b:free",
    "laguna": "poolside/laguna-s-2.1:free",
    "cohere": "cohere/north-mini-code:free",
}

# 오프라인 및 목록 조회 실패 시 사용하는 기본 검증된 무료 VLM 모델 우선순위 목록
DEFAULT_FREE_VISION_MODELS: List[str] = [
    "dots-studio/dots-3-note-preview:free",               # 512K 컨텍스트, 정밀한 한국어 문서 및 표 마크다운 추출 1위
    "nvidia/nemotron-3.5-super-vl:free",                  # 1M 컨텍스트, 최고 성능 멀티모달 비전
    "openrouter/free",                                    # 200K 컨텍스트, 범용 고속 오픈 멀티모달 라우터
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", # 256K 컨텍스트, 옴니 비전 추론 모델
    "nvidia/nemotron-3.5-content-safety:free",            # 128K 컨텍스트, 비전 안전/분석 모델
]


def resolve_free_model_name(name: str) -> str:
    """별칭(dots, nemotron 등)을 실제 게이트웨이 모델 ID로 변환합니다."""
    clean = str(name).strip().lower()
    return MODEL_ALIASES.get(clean, name)


class FreeModelAdapter(BaseVlmAdapter):
    """
    dsh-our-free-model 기반의 완전 무료 VLM 어댑터.
    - API 키 불필요 (Zero Auth)
    - OpenAI 호환 게이트웨이(/chat/completions)를 통한 고화질 이미지 분석
    - all 모드 순차 전환 및 1시간 Sleep 자동 복구 루프 지원
    - 자체 어댑터 내 완전 귀속 설정 관리
    """

    def __init__(self, model: str | None = None, base_url: str | None = None):
        """
        FreeModel 어댑터 초기화.
        
        Args:
            model: 대상 무료 모델명 또는 'all' (기본: 환경변수 FREE_MODEL 또는 'all')
            base_url: 오픈소스 게이트웨이 URL (기본: 환경변수 FREE_MODEL_BASE_URL)
        """
        raw_model = model or FREE_MODEL or "all"
        self.model = resolve_free_model_name(raw_model)
        self.base_url = (base_url or FREE_MODEL_BASE_URL).rstrip("/")
        self.exhausted_models: Set[str] = set()
        self.current_active_model: Optional[str] = None
        self._free_models: Optional[List[str]] = None

    @property
    def name(self) -> str:
        """어댑터 식별 명칭."""
        return "free_model"

    def get_supported_models(self) -> List[str]:
        """
        게이트웨이에서 isFree: true 및 비전(Image)을 지원하는 모델을 동적으로 탐색하고 정렬합니다.
        
        조회 실패 시 내장된 기본 검증 모델 리스트를 반환합니다.
        """
        if self._free_models:
            return list(self._free_models)

        url = f"{self.base_url}/models"
        try:
            resp = requests.get(
                url,
                headers={"accept": "application/json", "user-agent": "dsh-our-free-model"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                raw_models = data.get("data") or []
                vision_models = []
                for m in raw_models:
                    if not m.get("isFree"):
                        continue
                    m_id = str(m.get("id", "")).strip()
                    if not m_id:
                        continue
                    
                    # 비전/이미지 지원 여부 확인
                    arch = m.get("architecture") or {}
                    modalities = arch.get("input_modalities") or []
                    is_vision = (
                        "image" in modalities
                        or "vl" in m_id.lower()
                        or "vision" in m_id.lower()
                        or m_id in DEFAULT_FREE_VISION_MODELS
                    )
                    
                    # 400 에러를 유발하는 엔드포인트 제외
                    if "kilo-auto" in m_id.lower():
                        continue

                    if is_vision:
                        ctx = m.get("context_length") or (m.get("top_provider") or {}).get("context_length") or 100000
                        vision_models.append((m_id, int(ctx)))

                if vision_models:
                    # 기본 검증 모델 우선순위 유지 및 컨텍스트 길이 역순 정렬
                    def rank_key(item: tuple[str, int]) -> tuple[int, int]:
                        mid, ctx = item
                        if mid in DEFAULT_FREE_VISION_MODELS:
                            return (100 - DEFAULT_FREE_VISION_MODELS.index(mid), ctx)
                        return (0, ctx)

                    vision_models.sort(key=rank_key, reverse=True)
                    self._free_models = [m[0] for m in vision_models]
                    return list(self._free_models)
        except Exception:
            pass

        self._free_models = list(DEFAULT_FREE_VISION_MODELS)
        return list(self._free_models)

    def _generate_caption(self, image_path: Path, prompt: str) -> str:
        """무료 VLM API로 이미지 캡션 요청 (all 모드 분기 처리)."""
        clean_model = resolve_free_model_name(str(self.model).strip().lower())
        if clean_model == "all":
            return self._generate_caption_all(image_path, prompt)
        return self._generate_caption_single(image_path, prompt, self.model)

    def _generate_caption_single(self, image_path: Path, prompt: str, model_name: str) -> str:
        """단일 지정 무료 모델로 캡션 생성."""
        target_model = resolve_free_model_name(model_name)
        b64 = self.encode_image_b64(image_path)
        url = f"{self.base_url}/chat/completions"
        effective_max_tokens = max(VLM_MAX_TOKENS, 1536)
        payload = {
            "model": target_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}"
                            },
                        },
                    ],
                }
            ],
            "temperature": VLM_TEMPERATURE,
            "max_tokens": effective_max_tokens,
        }

        try:
            resp = requests.post(
                url,
                headers={"content-type": "application/json", "user-agent": "dsh-our-free-model"},
                json=payload,
                timeout=VLM_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise VlmError(f"FreeModel 연결 실패: {exc}")

        if resp.status_code == 429:
            raise VlmQuotaExceededError(f"FreeModel '{target_model}' 일시적 한도 초과 (HTTP 429)")
        if resp.status_code != 200:
            raise VlmError(f"FreeModel {target_model} HTTP {resp.status_code}: {resp.text[:300]}")

        return self._extract_text_from_resp(resp)

    def _generate_caption_all(
        self,
        image_path: Path,
        prompt: str,
        max_sleep_cycles: Optional[int] = None,
    ) -> str:
        """
        [all 모드] 가용 무료 VLM 모델들을 순차적으로 순회하며 캡션을 생성합니다.
        
        - 한 모델이 429/5xx 장애 시 다음 무료 모델로 즉시 전환.
        - 전체 모델 소진 시 Sleep 모드로 진입하여 1시간 주기로 복구를 확인하고 자동 재개.
        """
        models = self.get_supported_models()
        if not models:
            raise VlmError("사용 가능한 무료 VLM 모델 목록을 찾을 수 없습니다.")

        b64 = self.encode_image_b64(image_path)
        effective_max_tokens = max(VLM_MAX_TOKENS, 1536)
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}"
                            },
                        },
                    ],
                }
            ],
            "temperature": VLM_TEMPERATURE,
            "max_tokens": effective_max_tokens,
        }
        url = f"{self.base_url}/chat/completions"

        sleep_cycles = 0
        while True:
            # 1. 소진되지 않은 가용 모델 선별
            available = [m for m in models if m not in self.exhausted_models]

            # 2. 모든 무료 모델 소진 시 Sleep 모드 진입
            if not available:
                if max_sleep_cycles is not None and sleep_cycles >= max_sleep_cycles:
                    raise VlmQuotaExceededError("모든 무료 VLM 모델 한도 소진 및 테스트 슬립 주기 도달")

                sleep_sec = FREE_MODEL_SLEEP_INTERVAL
                hours = sleep_sec / 3600.0
                print(
                    f"\n[FreeModel All] ⚠️ 모든 무료 VLM 모델({len(models)}개)의 한도가 소진되었거나 응답 불가 상태입니다. "
                    f"Sleep 모드로 진입합니다. ({hours:.1f}시간 주기마다 복구 여부 확인)",
                    flush=True,
                )

                self._interruptible_sleep(sleep_sec)
                sleep_cycles += 1

                print(f"[FreeModel All] 🔄 {hours:.1f}시간 경과: 무료 VLM 모델 복구 여부를 확인합니다...", flush=True)

                # 최상위 모델로 복구 테스트 프로브
                probe_model = models[0]
                probe_payload = dict(payload)
                probe_payload["model"] = probe_model

                try:
                    resp = requests.post(
                        url,
                        headers={"content-type": "application/json", "user-agent": "dsh-our-free-model"},
                        json=probe_payload,
                        timeout=VLM_TIMEOUT,
                    )
                    if resp.status_code == 200:
                        try:
                            text = self._extract_text_from_resp(resp)
                        except VlmError:
                            text = ""
                        if text:
                            print(f"[FreeModel All] ✅ 무료 모델 '{probe_model}' 복구 확인! 작업을 재진행합니다.", flush=True)
                            self.exhausted_models.clear()
                            self.current_active_model = probe_model
                            return text
                    elif resp.status_code in (429, 500, 502, 503):
                        print(f"[FreeModel All] ⏳ 아직 복구되지 않았습니다(HTTP {resp.status_code}). 다음 주기까지 대기합니다.", flush=True)
                        continue
                except Exception as exc:
                    print(f"[FreeModel All] 복구 프로브 오류: {exc}. 다음 주기에 재시도합니다.", flush=True)
                    continue

            # 3. 현재 활성 모델 또는 남은 가용 모델 중 최우선 모델 선택
            if self.current_active_model and self.current_active_model in available:
                cur_model = self.current_active_model
            else:
                cur_model = available[0]
                self.current_active_model = cur_model

            cur_payload = dict(payload)
            cur_payload["model"] = cur_model

            try:
                resp = requests.post(
                    url,
                    headers={"content-type": "application/json", "user-agent": "dsh-our-free-model"},
                    json=cur_payload,
                    timeout=VLM_TIMEOUT,
                )
            except requests.RequestException as exc:
                print(f"[FreeModel All] 모델 '{cur_model}' 연결 실패: {exc}. 다음 모델로 시도합니다.", flush=True)
                self.exhausted_models.add(cur_model)
                continue

            # 4. 429 한도 초과 또는 5xx 일시 장애 시 다음 모델로 전환
            if resp.status_code in (429, 500, 502, 503, 504, 404):
                self.exhausted_models.add(cur_model)
                rem = [m for m in models if m not in self.exhausted_models]
                next_str = rem[0] if rem else "없음 (Sleep 모드 예정)"
                print(
                    f"[FreeModel All] ⚠️ 모델 '{cur_model}' 호출 실패(HTTP {resp.status_code}). "
                    f"다음 모델 '{next_str}'(으)로 자동 전환합니다.",
                    flush=True,
                )
                if rem:
                    self.current_active_model = rem[0]
                continue

            if resp.status_code != 200:
                raise VlmError(f"FreeModel {cur_model} HTTP {resp.status_code}: {resp.text[:300]}")

            try:
                text = self._extract_text_from_resp(resp)
            except VlmError:
                text = ""

            if not text:
                print(f"[FreeModel All] ⚠️ 모델 '{cur_model}' 응답 내용이 비어 있음. 다음 모델로 전환합니다.", flush=True)
                self.exhausted_models.add(cur_model)
                rem = [m for m in models if m not in self.exhausted_models]
                if rem:
                    self.current_active_model = rem[0]
                continue

            return text

    @staticmethod
    def _extract_text_from_resp(resp: requests.Response) -> str:
        """OpenAI 호환 JSON 응답 본문에서 생성 텍스트를 추출."""
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise VlmError(f"FreeModel 응답에 choices 없음: {data}")

        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
        # 추론(reasoning) 모델의 경우 content가 비어 있으면 reasoning 본문 추출
        if not text and msg.get("reasoning"):
            text = msg.get("reasoning")
        if not text:
            raise VlmError("FreeModel 응답 content가 비어 있음")

        return str(text).strip()

    @staticmethod
    def _interruptible_sleep(seconds: float, step: float = 1.0) -> None:
        """장시간 슬립 시 짧은 단위(1초)로 나누어 대기하여 즉각적인 반응성 확보."""
        elapsed = 0.0
        while elapsed < seconds:
            chunk = min(seconds - elapsed, step)
            time.sleep(chunk)
            elapsed += chunk
