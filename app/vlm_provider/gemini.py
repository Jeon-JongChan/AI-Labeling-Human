"""
Google Gemini REST API 연동 어댑터 모듈.

복수 API 키(GEMINI_API_KEY/GEMINI_API_KEYS) 순차 로테이션 지원:
- 현재 API 키의 가용 모델이 모두 429 한도 초과되거나 만료/인증 실패(400/403) 시 다음 API 키로 자동 전환.
- 모든 API 키의 가용 모델이 소진될 경우 1시간 주기의 슬립(Sleep) 모드로 전환하여
  Key 1의 최우선 모델부터 복구 여부를 주기적으로 체크하고 자동으로 작업을 재개합니다.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import List, Optional, Set
import requests

from config import (
    GEMINI_API_KEY,
    GEMINI_API_KEYS,
    GEMINI_MODEL,
    GEMINI_SLEEP_INTERVAL,
    VLM_MAX_TOKENS,
    VLM_TEMPERATURE,
    VLM_TIMEOUT,
    parse_gemini_api_keys,
)
from .base import BaseVlmAdapter, VlmError, VlmQuotaExceededError


def _try_get_colab_key() -> str:
    """Colab 환경의 Secrets(userdata)에 GEMINI_API_KEY가 저장되어 있는지 확인."""
    try:
        from google.colab import userdata  # type: ignore
        return userdata.get("GEMINI_API_KEY") or ""
    except Exception:
        return ""


# 오프라인 또는 API 목록 조회 실패 시 사용하는 기본 정렬 모델 목록 (Flash-Lite -> Flash -> 기타 순)
DEFAULT_GEMINI_FALLBACK_MODELS: List[str] = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3-flash",
    "gemini-flash-latest",
]


def score_gemini_model(model_id: str) -> float:
    """
    Gemini 모델의 우선순위 점수를 산출합니다.
    - 무료 티어 실사용 쿼터 기준 우선순위:
      * Flash-Lite 계열: 500 RPD / 15 RPM (가장 넉넉함) -> Tier 10000
      * Flash 계열: 20 RPD / 5 RPM -> Tier 5000
      * Pro 계열: 무료 티어 0 RPD (결제 필요) -> Tier 1000 (최하위)
    - Version 가중치: 최신 버전 우선 (예: 3.5 -> 350, 3.1 -> 310)
    - 지원 종료 모델(2.5, 2.0 등) 감점 (-5000)
    """
    name_lower = model_id.lower()
    tier = 1000

    if "flash-8b" in name_lower or "flash-lite" in name_lower or "8b" in name_lower or "lite" in name_lower:
        tier = 10000
    elif "flash" in name_lower:
        tier = 5000
    elif "pro" in name_lower:
        tier = 1000

    match = re.search(r"(\d+(?:\.\d+)?)", name_lower)
    version = float(match.group(1)) if match else 1.0

    # 지원 종료/비추천 모델 감점 (구버전 2.5, 2.0, 1.5)
    penalty = 0.0
    if any(deprecated in name_lower for deprecated in ("2.5", "2.0", "1.5", "gemini-1.")):
        penalty = -5000.0

    return tier + (version * 100) + penalty


class GeminiAdapter(BaseVlmAdapter):
    """
    Google Gemini REST API를 활용한 VLM 어댑터.
    - 복수 API 키 순차 로테이션 지원:
      * 현재 활성 키의 모델들이 모두 소진되거나 만료/인증 실패(400/403) 시 다음 키로 자동 전환.
      * 모든 등록된 API 키의 모델이 소진되면 1시간 대기(Sleep) 모드 진입 또는 체인 위임.
    - model='all' 지정 시 가용 모델 순차 자동 폴백 및 1시간 Sleep 복구 모드 지원.
    """

    def __init__(
        self,
        api_key: str | List[str] | None = None,
        model: str | None = None,
        allow_sleep: bool = True,
    ):
        """
        Gemini 어댑터 초기화.

        Args:
            api_key: 단일 키, 복수 키 목록(List[str]), 또는 구분자 문자열 (미지정 시 config.GEMINI_API_KEYS)
            model: 기본 Gemini 모델명 또는 'all' (미지정 시 config.GEMINI_MODEL)
            allow_sleep: 단독 실행 시 모든 모델 소진 후 1시간 Sleep 여부 (체인 모드에서는 False)
        """
        keys: List[str] = []
        if isinstance(api_key, list):
            for k in api_key:
                keys.extend(parse_gemini_api_keys(str(k)))
        elif isinstance(api_key, str) and api_key.strip():
            keys = parse_gemini_api_keys(api_key)

        if not keys:
            colab_key = _try_get_colab_key()
            if colab_key:
                keys = parse_gemini_api_keys(colab_key)
            else:
                keys = list(GEMINI_API_KEYS) or ([GEMINI_API_KEY] if GEMINI_API_KEY else [])

        self.api_keys: List[str] = keys
        self.current_key_index: int = 0
        self.model = model or GEMINI_MODEL
        self.allow_sleep = allow_sleep
        self.exhausted_models: Set[str] = set()
        self.current_active_model: Optional[str] = None
        self._supported_models: Optional[List[str]] = None
        self._last_request_time: float = 0.0

    @property
    def api_key(self) -> str:
        """현재 활성화된 API 키 반환."""
        if 0 <= self.current_key_index < len(self.api_keys):
            return self.api_keys[self.current_key_index]
        return self.api_keys[0] if self.api_keys else ""

    @api_key.setter
    def api_key(self, value: str) -> None:
        """외부에서 api_key를 변경할 때 키 목록을 재설정."""
        if value:
            parsed = parse_gemini_api_keys(value)
            if parsed:
                self.api_keys = parsed
                self.current_key_index = 0

    def reset(self) -> None:
        """어댑터의 소진 상태 및 키 인덱스를 초기 상태(Key 1)로 완전 초기화합니다."""
        self.current_key_index = 0
        self.exhausted_models.clear()
        self.current_active_model = None

    def _switch_to_next_key(self, reason: str = "") -> bool:
        """
        다음 등록된 API 키로 전환합니다.

        전환 가능한 다음 키가 남아있으면 True, 모든 키가 소진되었으면 False를 반환합니다.
        """
        if self.current_key_index + 1 < len(self.api_keys):
            prev_idx = self.current_key_index
            self.current_key_index += 1
            self.exhausted_models.clear()
            self.current_active_model = None
            key_repr = f"Key {self.current_key_index + 1}/{len(self.api_keys)}"
            reason_msg = f" ({reason})" if reason else ""
            print(
                f"\n[Gemini Key 전환] 기존 API 키(Key {prev_idx + 1}) 소진/만료{reason_msg} -> "
                f"다음 API 키({key_repr})로 자동 전환합니다.",
                flush=True,
            )
            return True
        return False

    def _wait_rpm_interval(self, min_seconds: float = 4.0) -> None:
        """
        Gemini 무료 티어의 15 RPM(분당 15회 = 최소 4초 간격) 제한을 보장하기 위해 필요시 대기.
        단독 실행뿐 아니라 체인 모드 등 어떤 경로로 호출되어도 15 RPM을 초과하지 않도록 보장합니다.
        """
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < min_seconds:
            time.sleep(min_seconds - elapsed)
        self._last_request_time = time.time()

    @property
    def name(self) -> str:
        """어댑터 식별 명칭."""
        return "gemini"

    def get_supported_models(self, api_key: str | None = None) -> List[str]:
        """
        Gemini API에서 지원하는 모델 목록을 동적으로 가져와 고성능 우선순위로 정렬합니다.

        API 호출에 실패하거나 오프라인 환경인 경우 내장된 기본 모델 리스트를 반환합니다.
        """
        if self._supported_models:
            return list(self._supported_models)

        key = api_key or self.api_key
        if not key:
            return list(DEFAULT_GEMINI_FALLBACK_MODELS)

        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                raw_models = data.get("models") or []
                filtered: List[str] = []
                for m in raw_models:
                    m_name = m.get("name", "")
                    if m_name.startswith("models/"):
                        m_name = m_name[len("models/"):]

                    methods = m.get("supportedGenerationMethods") or []
                    if "generateContent" not in methods or "gemini" not in m_name.lower():
                        continue

                    if any(
                        bad in m_name.lower()
                        for bad in (
                            "embedding", "aqa", "imagen", "tts", "audio",
                            "transcribe", "whisper", "stt", "native", "robotics",
                        )
                    ):
                        continue

                    filtered.append(m_name)

                if filtered:
                    filtered.sort(key=score_gemini_model, reverse=True)
                    self._supported_models = filtered
                    return list(self._supported_models)
        except Exception:
            pass

        self._supported_models = list(DEFAULT_GEMINI_FALLBACK_MODELS)
        return list(self._supported_models)

    def _generate_caption(self, image_path: Path, prompt: str) -> str:
        """Gemini REST API로 이미지 캡션 요청 (all 모드 분기 처리)."""
        if str(self.model).strip().lower() == "all":
            return self._generate_caption_all(image_path, prompt)
        return self._generate_caption_single(image_path, prompt, self.model)

    def _generate_caption_single(self, image_path: Path, prompt: str, model_name: str) -> str:
        """단일 지정 모델로 캡션 생성 (키 소진 시 다음 키 전환 및 전체 소진 시 Sleep 대기 지원)."""
        return self._generate_caption_all(image_path, prompt, override_models=[model_name])

    def _generate_caption_all(
        self,
        image_path: Path,
        prompt: str,
        max_sleep_cycles: Optional[int] = None,
        override_models: Optional[List[str]] = None,
    ) -> str:
        """
        등록된 API 키들을 순차적으로 사용하며 가용 모델 목록을 탐색하여 캡션을 생성합니다.

        - 한 모델이 429 초과 시 동일 키의 다음 모델로 순차 전환.
        - 한 키의 모든 모델 소진(또는 400/403 무효 키) 시 다음 등록된 API 키로 자동 전환.
        - 모든 등록된 키의 모델이 소진되면 Sleep 모드로 진입하여 1시간 간격으로 복구를 체크하고 자동 재개.
        """
        if not self.api_keys:
            raise VlmError("GEMINI_API_KEY 가 설정되지 않았습니다.")

        b64 = self.encode_image_b64(image_path)
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "image/png", "data": b64}},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": VLM_TEMPERATURE,
                "maxOutputTokens": VLM_MAX_TOKENS,
            },
        }

        sleep_cycles = 0
        while True:
            key = self.api_key
            models = list(override_models) if override_models else self.get_supported_models(key)
            if not models:
                models = list(DEFAULT_GEMINI_FALLBACK_MODELS)

            # 1. 현재 키에서 소진되지 않은 가용 모델 선별
            available = [m for m in models if m not in self.exhausted_models]

            # 2. 현재 키의 모든 모델 소진 시 다음 API 키로 전환
            if not available:
                if self._switch_to_next_key(reason=f"Key {self.current_key_index + 1} 모든 모델 소진"):
                    continue

                # 모든 등록된 API 키의 모든 모델이 소진된 경우
                if not getattr(self, "allow_sleep", True) or (
                    max_sleep_cycles is not None and sleep_cycles >= max_sleep_cycles
                ):
                    raise VlmQuotaExceededError(
                        f"Gemini 모든 API 키({len(self.api_keys)}개)의 모든 지원 모델({len(models)}개) 일일 한도 소진"
                    )

                sleep_sec = GEMINI_SLEEP_INTERVAL
                hours = sleep_sec / 3600.0
                print(
                    f"\n[Gemini All] [전환] 모든 API 키({len(self.api_keys)}개)의 지원 모델({len(models)}개) 한도가 소진되었습니다. "
                    f"Sleep 모드로 진입합니다. ({hours:.1f}시간 주기마다 복구 여부 확인)",
                    flush=True,
                )

                self._interruptible_sleep(sleep_sec)
                sleep_cycles += 1

                print(f"[Gemini All] [확인] {hours:.1f}시간 경과: Key 1의 모델 한도 복구 여부를 확인합니다...", flush=True)

                # 복구 여부 프로브 (첫 번째 키 Key 1의 최상위 모델부터 시험 요청)
                first_key = self.api_keys[0]
                probe_models = list(override_models) if override_models else self.get_supported_models(first_key)
                probe_model = probe_models[0] if probe_models else DEFAULT_GEMINI_FALLBACK_MODELS[0]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{probe_model}:generateContent?key={first_key}"
                self._wait_rpm_interval(4.0)
                try:
                    resp = requests.post(
                        url,
                        headers={"Content-Type": "application/json"},
                        json=payload,
                        timeout=VLM_TIMEOUT,
                    )
                    if resp.status_code == 200:
                        print(
                            f"[Gemini All] [성공] Key 1의 모델 '{probe_model}' 한도가 복구되었습니다! 작업을 재진행합니다.",
                            flush=True,
                        )
                        self.reset()
                        self.current_active_model = probe_model
                        return self._extract_text_from_resp(resp)
                    elif resp.status_code == 429:
                        print(f"[Gemini All] [대기] 아직 한도가 복구되지 않았습니다(HTTP 429). 다음 주기까지 대기합니다.", flush=True)
                        continue
                except Exception as exc:
                    print(f"[Gemini All] 복구 확인 프로브 중 오류 발생: {exc}. 다음 주기에 재시도합니다.", flush=True)
                    continue

            # 3. 현재 활성 모델 또는 남은 가용 모델 중 최우선 모델 선택
            if self.current_active_model and self.current_active_model in available:
                cur_model = self.current_active_model
            else:
                cur_model = available[0]
                self.current_active_model = cur_model

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{cur_model}:generateContent?key={key}"

            self._wait_rpm_interval(4.0)
            try:
                resp = requests.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=VLM_TIMEOUT,
                )
            except requests.RequestException as exc:
                print(f"[Gemini All] 모델 '{cur_model}' 연결 실패: {exc}. 다음 모델로 시도합니다.", flush=True)
                self.exhausted_models.add(cur_model)
                continue

            # 4. API 키 자체의 인증/권한 에러(400/403) 체크
            is_auth_error = resp.status_code == 403 or (
                resp.status_code == 400
                and any(
                    kw in resp.text.lower()
                    for kw in ("api_key", "key_invalid", "permission", "blocked", "credential")
                )
            )
            if is_auth_error:
                print(
                    f"[Gemini All] 현재 API 키(Key {self.current_key_index + 1}) 인증 오류(HTTP {resp.status_code}: {resp.text[:120]}). "
                    f"해당 키를 제외하고 다음 키로 즉시 전환합니다.",
                    flush=True,
                )
                if self._switch_to_next_key(reason=f"인증 에러({resp.status_code})"):
                    continue
                if not getattr(self, "allow_sleep", True):
                    raise VlmQuotaExceededError("모든 Gemini API 키가 유효하지 않거나 인증에 실패했습니다.")
                raise VlmError("등록된 모든 Gemini API 키의 인증에 실패했습니다.")

            # 5. 429 한도 초과 시 처리
            if resp.status_code == 429:
                err_text = resp.text.lower()
                is_rpm_limit = any(kw in err_text for kw in ("minute", "rpm", "queries per minute", "rate limit"))
                is_rpd_limit = any(kw in err_text for kw in ("per day", "daily", "rpd", "queries per day"))

                # RPM 분당 제한: 10초 대기 후 재시도
                if is_rpm_limit and not is_rpd_limit:
                    print(f"[Gemini All] 모델 '{cur_model}' RPM(분당) 속도 제한 도달. 10초 대기 후 재시도합니다...", flush=True)
                    time.sleep(10)
                    self._wait_rpm_interval(4.0)
                    try:
                        resp = requests.post(
                            url, headers={"Content-Type": "application/json"}, json=payload, timeout=VLM_TIMEOUT
                        )
                        if resp.status_code == 200:
                            text = self._extract_text_from_resp(resp)
                            if text:
                                return text
                    except Exception:
                        pass

                # 재시도 후에도 429이거나 RPD 소진: 해당 모델 소진 처리
                if resp.status_code == 429:
                    self.exhausted_models.add(cur_model)
                    rem = [m for m in models if m not in self.exhausted_models]
                    next_str = rem[0] if rem else "없음 (다음 API 키 전환 예정)"
                    limit_type = "RPD(일일 한도)" if is_rpd_limit else "한도(429)"
                    print(
                        f"[Gemini All] [전환] Key {self.current_key_index + 1} 모델 '{cur_model}' {limit_type} 초과. "
                        f"다음 모델 '{next_str}'(으)로 전환합니다.",
                        flush=True,
                    )
                    if rem:
                        self.current_active_model = rem[0]
                    continue

            # 6. 이미지 모달리티 미지원(400) 또는 모델 미지원(404)/서비스 일시불가(503) 시 다음 모델 전환
            is_modality_error = (
                resp.status_code == 400
                and any(kw in resp.text.lower() for kw in ("modality", "image", "not enabled", "not supported"))
            )
            if resp.status_code in (404, 503) or is_modality_error:
                self.exhausted_models.add(cur_model)
                rem = [m for m in models if m not in self.exhausted_models]
                next_str = rem[0] if rem else "없음 (다음 API 키 전환 예정)"
                reason = "이미지 모달리티 미지원(400)" if is_modality_error else f"HTTP {resp.status_code}"
                print(
                    f"[Gemini All] [전환] 모델 '{cur_model}' {reason}. 다음 모델 '{next_str}'(으)로 전환합니다.",
                    flush=True,
                )
                if rem:
                    self.current_active_model = rem[0]
                continue

            if resp.status_code != 200:
                raise VlmError(f"Gemini {cur_model} HTTP {resp.status_code}: {resp.text[:300]}")

            try:
                raw_text = self._extract_text_from_resp(resp)
            except VlmError:
                raw_text = ""

            text = self.clean_and_validate(raw_text)
            if not text:
                print(
                    f"[Gemini All] [전환] 모델 '{cur_model}' 응답 본문이 비어 있거나 사고 과정만 출력 후 중단됨. "
                    f"다음 모델로 자동 전환합니다.",
                    flush=True,
                )
                self.exhausted_models.add(cur_model)
                rem = [m for m in models if m not in self.exhausted_models]
                if rem:
                    self.current_active_model = rem[0]
                continue

            return text

    @staticmethod
    def _extract_text_from_resp(resp: requests.Response) -> str:
        """Gemini JSON 응답 본문에서 생성 텍스트를 추출."""
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise VlmError(f"Gemini 응답에 candidates 없음: {data}")

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise VlmError("Gemini 응답 내용(parts)이 비어 있음")

        return parts[0].get("text", "").strip()

    @staticmethod
    def _interruptible_sleep(seconds: float, step: float = 1.0) -> None:
        """장시간 슬립 시 짧은 단위(1초)로 나누어 대기하여 즉각적인 반응성 확보."""
        elapsed = 0.0
        while elapsed < seconds:
            chunk = min(seconds - elapsed, step)
            time.sleep(chunk)
            elapsed += chunk
