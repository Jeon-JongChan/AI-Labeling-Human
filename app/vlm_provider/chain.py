"""
통합 API 체인(Chained VLM) 공급자 어댑터 모듈.

Gemini API와 무료 VLM(FreeModel) 등 복수의 API 공급자를 체인으로 엮어
지정된 우선순위(예: gemini+free 또는 free+gemini)에 따라 순차 전환하며,
전체 소진 시 1시간 주기 Sleep 복구 루프를 지원합니다.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .base import BaseVlmAdapter, VlmAbortError, VlmError, VlmQuotaExceededError, strip_conversational_noise


class ChainedVlmAdapter(BaseVlmAdapter):
    """
    복수의 VLM 공급자 어댑터를 체인으로 연결하여 순차 실행 및 폴백을 수행하는 Composite 어댑터.

    특징:
    - 우선순위 지정: 'gemini+free', 'free,gemini' 등 설정 순서대로 순차 시도.
    - 실패 조건 제어 (VLM_CHAIN_FALLBACK_ON):
        * 'limit' (기본): 429 한도 초과/소진 시에만 다음 공급자로 전환.
        * 'all': 어떤 에러든 실패 발생 시 다음 공급자로 전환.
    - 전체 소진 시 Sleep: 체인 내 모든 공급자 소진 시 1시간 주기 대기 후 우선순위대로 복구 프로브.
    """

    def __init__(
        self,
        providers: Sequence[str] | str,
        model: Optional[str] = None,
        fallback_on: Optional[str] = None,
        sleep_interval: Optional[float] = None,
        adapters: Optional[List[BaseVlmAdapter]] = None,
    ):
        """
        체인 어댑터를 초기화합니다.

        Args:
            providers: 공급자 이름 목록 또는 구분자 문자열 (예: ['gemini', 'free'] 또는 'gemini+free')
            model: 모델 파라미터 (기본 'all', 또는 'gemini-1.5-flash+dots' 등 '+' 결합)
            fallback_on: 폴백 전환 조건 ('limit' 또는 'all', 미지정 시 환경변수 VLM_CHAIN_FALLBACK_ON)
            sleep_interval: 전체 소진 시 대기 주기(초) (미지정 시 환경변수 VLM_CHAIN_SLEEP_INTERVAL 또는 3600.0)
            adapters: 단위 테스트용 사전 주입 어댑터 인스턴스 목록
        """
        if isinstance(providers, str):
            clean_str = providers.replace(",", "+")
            self.provider_names = [p.strip().lower() for p in clean_str.split("+") if p.strip()]
        else:
            self.provider_names = [p.strip().lower() for p in providers if p.strip()]

        if not self.provider_names and not adapters:
            self.provider_names = ["gemini", "free"]

        self.model_arg = (model or "all").strip()
        env_fallback = os.getenv("VLM_CHAIN_FALLBACK_ON", "limit").strip().lower()
        self.fallback_on = (fallback_on or env_fallback or "limit").lower()
        if self.fallback_on not in ("limit", "all"):
            self.fallback_on = "limit"

        env_sleep = float(os.getenv("VLM_CHAIN_SLEEP_INTERVAL", "3600.0"))
        self.sleep_interval = float(sleep_interval) if sleep_interval is not None else env_sleep

        self._active_index: int = 0
        self.adapters: List[BaseVlmAdapter] = adapters or []

        # 어댑터가 주입되지 않았다면 각 공급자별 어댑터를 초기화
        if not self.adapters:
            self._init_sub_adapters()
        else:
            for ad in self.adapters:
                ad.allow_sleep = False

    @property
    def name(self) -> str:
        """어댑터 식별 명칭 (예: 'chain(gemini+free)')."""
        chain_repr = "+".join(self.provider_names)
        return f"chain({chain_repr})"

    def _init_sub_adapters(self) -> None:
        """공급자 명칭과 모델 매핑에 맞추어 서브 어댑터들을 생성."""
        # factory 순환 참조 방지를 위해 지연 임포트
        from .factory import VlmFactory

        # 모델 매핑 분리: 'flash+dots' 형태 지원
        models = self.model_arg.replace(",", "+").split("+") if "+" in self.model_arg or "," in self.model_arg else []

        self.adapters = []
        for idx, prov in enumerate(self.provider_names):
            sub_model = models[idx].strip() if idx < len(models) else self.model_arg
            # 서브 공급자 생성 시 체인이 아닌 단일 어댑터로 생성
            adapter = VlmFactory.create(provider=prov, model=sub_model)
            # 체인 내부의 서브 어댑터는 독자 슬립을 금지하고, 모든 모델 소진 시 즉시 VlmQuotaExceededError를 발생시켜 다음 체인 공급자로 넘어가게 함
            adapter.allow_sleep = False
            self.adapters.append(adapter)

    def _is_fallback_triggered(self, err: Exception) -> bool:
        """
        주어진 예외가 다음 공급자로 전환해야 하는 조건인지 검사합니다.

        - 'all': 어떤 에러든 실패 시 전환.
        - 'limit': 429 한도 초과(VlmQuotaExceededError 등)인 경우에만 전환.
        """
        if self.fallback_on == "all":
            return True

        # fallback_on == 'limit'
        if isinstance(err, VlmQuotaExceededError):
            return True

        err_msg = str(err).lower()
        limit_keywords = [
            "429", "quota", "rate limit", "resource exhausted", "exhausted", "한도",
            "비어 있", "empty", "모든 지원 모델", "소진", "실패", "fail", "timeout"
        ]
        return any(kw in err_msg for kw in limit_keywords)

    def _generate_caption(self, image_path: Path, prompt: str) -> str:
        """
        우선순위에 따라 어댑터를 순차 호출하며 실패 시 설정 조건에 따라 다음 공급자로 전환합니다.
        """
        if not self.adapters:
            raise VlmError("[Chain] 사용 가능한 서브 VLM 어댑터가 없습니다.")

        while True:
            total = len(self.adapters)
            start_index = self._active_index

            # 1. 현재 활성 공급자부터 마지막 공급자까지 순차 시도
            for i in range(start_index, total):
                self._active_index = i
                adapter = self.adapters[i]
                prov_name = self.provider_names[i] if i < len(self.provider_names) else adapter.name

                try:
                    raw_result = adapter._generate_caption(image_path, prompt)
                    # 서브 어댑터 반환값에서 사고 과정 및 대화형 사족을 즉시 정제
                    clean_res = self.clean_and_validate(raw_result)
                    if clean_res and clean_res.strip():
                        return clean_res.strip()
                    raise VlmError(f"공급자 '{prov_name}' 응답이 비어 있거나 유효하지 않습니다.")
                except Exception as err:
                    # 다음 공급자 전환 대상인지 판정
                    if self._is_fallback_triggered(err):
                        has_next = (i + 1 < total)
                        next_name = self.provider_names[i + 1] if has_next else "없음 (Sleep 모드 예정)"
                        print(
                            f"[VLM Chain] [전환] 공급자 '{prov_name}' 실패({err}). "
                            f"다음 공급자 '{next_name}'(으)로 자동 전환합니다. (조건: {self.fallback_on})",
                            flush=True,
                        )
                        continue
                    # fallback_on이 'limit'인데 일반 오류인 경우 즉시 에러 전파
                    raise

            # 2. 모든 공급자 소진 -> Sleep 모드 진입 및 복구 대기
            print(
                f"\n[VLM Chain] [전환] 모든 통합 공급자({total}개: {', '.join(self.provider_names)})가 소진되었습니다. "
                f"Sleep 모드로 진입합니다. ({self.sleep_interval / 3600.0:.1f}시간 주기마다 복구 여부 확인)",
                flush=True,
            )

            recovered_index = self._sleep_and_wait_recovery(image_path, prompt)
            self._active_index = recovered_index
            print(
                f"[VLM Chain] [성공] 공급자 '{self.provider_names[recovered_index]}' 복구 확인! 작업을 재진행합니다.\n",
                flush=True,
            )

    def _sleep_and_wait_recovery(
        self,
        image_path: Path,
        prompt: str,
        sleep_fn: Optional[Callable[[float], None]] = None,
        should_stop_fn: Optional[Callable[[], bool]] = None,
    ) -> int:
        """
        설정된 대기 주기(기본 1시간)만큼 Sleep한 뒤,
        지정된 우선순위 순서대로 복구 여부를 프로브하여 첫 번째로 사용 가능한 어댑터의 인덱스를 반환합니다.
        """
        actual_sleep = sleep_fn or time.sleep
        check_interval = 1.0  # 취소 감지 단위 (1초)

        while True:
            # 1시간 대기 (1초 단위 취소 감지)
            elapsed = 0.0
            while elapsed < self.sleep_interval:
                if should_stop_fn and should_stop_fn():
                    raise VlmAbortError("[Chain] 사용자 요청 또는 배치 중단으로 Sleep 대기를 종료합니다.")
                step = min(check_interval, self.sleep_interval - elapsed)
                actual_sleep(step)
                elapsed += step

            print(
                f"[VLM Chain] [확인] {self.sleep_interval / 3600.0:.1f}시간 경과: 공급자 복구 여부를 확인합니다...",
                flush=True,
            )

            # 우선순위 순서(0번부터)대로 복구 테스트
            for idx, adapter in enumerate(self.adapters):
                prov_name = self.provider_names[idx] if idx < len(self.provider_names) else adapter.name
                # 새로운 주기이므로 소진 목록 및 키 인덱스 완전 초기화
                if hasattr(adapter, "reset"):
                    adapter.reset()
                else:
                    if hasattr(adapter, "exhausted_models"):
                        adapter.exhausted_models.clear()
                        adapter.current_active_model = None
                    if hasattr(adapter, "current_key_index"):
                        adapter.current_key_index = 0
                try:
                    # 복구 여부 테스트 프로브
                    probe_res = adapter._generate_caption(image_path, prompt)
                    if probe_res and probe_res.strip():
                        return idx
                except Exception:
                    # 여전히 소진/실패 상태
                    pass

            print(
                f"[VLM Chain] [대기] 아직 복구된 공급자가 없습니다. 다음 {self.sleep_interval / 3600.0:.1f}시간 동안 다시 대기합니다.",
                flush=True,
            )

