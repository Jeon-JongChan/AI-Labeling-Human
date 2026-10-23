"""
Google Gemini REST API 연동 어댑터 모듈.

모델별 한도가 다를 때 model='all'을 지정하면 지원 모델 목록을 조회하여
고성능 순서(Pro > Flash > Lite/8B, 최신 버전 우선)대로 순차적으로 사용하며,
모든 모델의 한도가 소진될 경우 1시간 주기의 슬립(Sleep) 모드로 전환하여
한도 복구 여부를 주기적으로 체크하고 자동으로 작업을 재개합니다.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import List, Optional, Set
import requests

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_SLEEP_INTERVAL,
    VLM_MAX_TOKENS,
    VLM_TEMPERATURE,
    VLM_TIMEOUT,
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
    - 무료 일일 1500회 티어 지원.
    - model='all' 지정 시 가용 모델 순차 자동 폴백 및 1시간 Sleep 복구 모드 지원.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None, allow_sleep: bool = True):
        """
        Gemini 어댑터 초기화.
        
        Args:
            api_key: Gemini API 키 (미지정 시 환경변수 또는 Colab Secrets 참조)
            model: 기본 Gemini 모델명 또는 'all' (미지정 시 config.GEMINI_MODEL)
            allow_sleep: 단독 실행 시 모든 모델 소진 후 1시간 Sleep 여부 (체인 모드에서는 False)
        """
        self.api_key = api_key or GEMINI_API_KEY or _try_get_colab_key()
        self.model = model or GEMINI_MODEL
        self.allow_sleep = allow_sleep
        self.exhausted_models: Set[str] = set()
        self.current_active_model: Optional[str] = None
        self._supported_models: Optional[List[str]] = None
        self._last_request_time: float = 0.0

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

        key = api_key or self.api_key or GEMINI_API_KEY or _try_get_colab_key()
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
                    
                    # 텍스트/비전 생성 가능한 모델만 선별
                    methods = m.get("supportedGenerationMethods") or []
                    if "generateContent" not in methods:
                        continue
                    if "gemini" not in m_name.lower():
                        continue
                    # 텍스트/비전이 아닌 음성/오디오/임베딩/로보틱스 전용 모델 제외
                    if any(bad in m_name.lower() for bad in ("embedding", "aqa", "imagen", "tts", "audio", "transcribe", "whisper", "stt", "native", "robotics")):
                        continue
                    
                    filtered.append(m_name)

                if filtered:
                    # 고성능 순서대로 정렬 (Pro -> Flash -> Lite/8B, 버전 역순)
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
        """단일 지정 모델(또는 레거시 후보 모델)로 캡션 생성."""
        key = self.api_key or GEMINI_API_KEY or _try_get_colab_key()
        if not key:
            raise VlmError("GEMINI_API_KEY 가 설정되지 않았습니다.")

        b64 = self.encode_image_b64(image_path)
        candidate_models = [
            model_name,
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3.5-flash",
            "gemini-flash-latest",
        ]
        seen = set()
        models_to_try = [m for m in candidate_models if m and not (m in seen or seen.add(m))]

        last_error = None
        for cur_model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{cur_model}:generateContent?key={key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": "image/png",
                                    "data": b64,
                                }
                            },
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": VLM_TEMPERATURE,
                    "maxOutputTokens": VLM_MAX_TOKENS,
                },
            }

            self._wait_rpm_interval(4.0)
            try:
                resp = requests.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=VLM_TIMEOUT,
                )
            except requests.RequestException as exc:
                last_error = f"Gemini API 연결 실패: {exc}"
                continue

            if resp.status_code == 429:
                err_text = resp.text.lower()
                is_rpm_limit = any(kw in err_text for kw in ("minute", "rpm", "queries per minute", "rate limit"))
                is_rpd_limit = any(kw in err_text for kw in ("per day", "daily", "rpd", "queries per day"))
                if is_rpm_limit and not is_rpd_limit:
                    time.sleep(10)
                    self._wait_rpm_interval(4.0)
                    try:
                        resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=VLM_TIMEOUT)
                    except Exception:
                        pass
                if resp.status_code == 429:
                    raise VlmQuotaExceededError(
                        f"Gemini API 모델 '{cur_model}' 사용량 한도 초과 (HTTP 429: Rate Limit / Quota Exceeded)"
                    )
            if resp.status_code in (404, 503):
                last_error = f"Gemini {cur_model} HTTP {resp.status_code}: {resp.text[:200]}"
                continue
            if resp.status_code != 200:
                raise VlmError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")

            return self._extract_text_from_resp(resp)

        raise VlmError(f"Gemini 모델 호출 실패 (시도한 모델: {models_to_try}): {last_error}")

    def _generate_caption_all(
        self,
        image_path: Path,
        prompt: str,
        max_sleep_cycles: Optional[int] = None,
    ) -> str:
        """
        [all 모드] 가용 모델 목록을 순차적으로 탐색하여 캡션을 생성합니다.
        
        - 한 모델이 429 한도 초과 시 다음 모델로 자동 전환.
        - 모든 모델이 소진되면 Sleep 모드로 진입하여 1시간 간격으로 복구를 체크하고 자동 재개.
        """
        key = self.api_key or GEMINI_API_KEY or _try_get_colab_key()
        if not key:
            raise VlmError("GEMINI_API_KEY 가 설정되지 않았습니다.")

        models = self.get_supported_models(key)
        if not models:
            raise VlmError("사용 가능한 Gemini 모델 목록을 찾을 수 없습니다.")

        b64 = self.encode_image_b64(image_path)
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": b64,
                            }
                        },
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
            # 1. 소진되지 않은 가용 모델 선별
            available = [m for m in models if m not in self.exhausted_models]

            # 2. 모든 모델 소진 시 Sleep 모드 진입 (단, 체인에 속해 있을 경우 즉시 다음 공급자로 전환)
            if not available:
                if not getattr(self, "allow_sleep", True) or (max_sleep_cycles is not None and sleep_cycles >= max_sleep_cycles):
                    raise VlmQuotaExceededError(f"Gemini 모든 지원 모델({len(models)}개) 일일 한도 소진")

                sleep_sec = GEMINI_SLEEP_INTERVAL
                hours = sleep_sec / 3600.0
                print(
                    f"\n[Gemini All] [전환] 모든 지원 모델({len(models)}개)의 한도가 소진되었습니다. "
                    f"Sleep 모드로 진입합니다. ({hours:.1f}시간 주기마다 복구 여부 확인)",
                    flush=True,
                )

                # 슬립 간격 동안 대기
                self._interruptible_sleep(sleep_sec)
                sleep_cycles += 1

                print(f"[Gemini All] [확인] {hours:.1f}시간 경과: 모델 한도 복구 여부를 확인합니다...", flush=True)

                # 복구 여부 프로브 (최상위 모델부터 시험 요청)
                probe_model = models[0]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{probe_model}:generateContent?key={key}"
                self._wait_rpm_interval(4.0)
                try:
                    resp = requests.post(
                        url,
                        headers={"Content-Type": "application/json"},
                        json=payload,
                        timeout=VLM_TIMEOUT,
                    )
                    if resp.status_code == 200:
                        print(f"[Gemini All] [성공] 모델 '{probe_model}'의 한도가 복구되었습니다! 작업을 재진행합니다.", flush=True)
                        self.exhausted_models.clear()
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

            # 4. 429 한도 초과 시 처리
            if resp.status_code == 429:
                err_text = resp.text.lower()
                is_rpm_limit = any(kw in err_text for kw in ("minute", "rpm", "queries per minute", "rate limit"))
                is_rpd_limit = any(kw in err_text for kw in ("per day", "daily", "rpd", "queries per day"))

                # 1) RPM 분당 일시 한도인 경우: 10초 대기 후 재시도 (모델을 일일 소진 처리하지 않음)
                if is_rpm_limit and not is_rpd_limit:
                    print(f"[Gemini All] 모델 '{cur_model}' RPM(분당) 속도 제한 도달. 10초 대기 후 재시도합니다...", flush=True)
                    time.sleep(10)
                    self._wait_rpm_interval(4.0)
                    try:
                        resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=VLM_TIMEOUT)
                        if resp.status_code == 200:
                            text = self._extract_text_from_resp(resp)
                            if text:
                                return text
                    except Exception:
                        pass

                # 2) 재시도 후에도 429이거나 일일 쿼터(RPD) 소진인 경우: 해당 모델 소진 처리 및 다음 모델 전환
                if resp.status_code == 429:
                    self.exhausted_models.add(cur_model)
                    rem = [m for m in models if m not in self.exhausted_models]
                    next_str = rem[0] if rem else "없음 (Sleep 모드 예정)"
                    limit_type = "RPD(일일 한도)" if is_rpd_limit else "한도(429)"
                    print(
                        f"[Gemini All] [전환] 모델 '{cur_model}' {limit_type} 초과. "
                        f"다음 모델 '{next_str}'(으)로 자동 전환합니다.",
                        flush=True,
                    )
                    if rem:
                        self.current_active_model = rem[0]
                    continue

            # 5. 이미지 모달리티 미지원(HTTP 400) 또는 모델 미지원(404) 또는 서비스 일시불가(503) 시 다음 모델로 전환
            is_modality_error = (
                resp.status_code == 400
                and any(kw in resp.text.lower() for kw in ("modality", "image", "not enabled", "not supported"))
            )
            if resp.status_code in (404, 503) or is_modality_error:
                self.exhausted_models.add(cur_model)
                rem = [m for m in models if m not in self.exhausted_models]
                next_str = rem[0] if rem else "없음 (Sleep 모드 예정)"
                reason = "이미지 모달리티 미지원(400)" if is_modality_error else f"HTTP {resp.status_code}"
                print(
                    f"[Gemini All] [전환] 모델 '{cur_model}' {reason}. 다음 모델 '{next_str}'(으)로 자동 전환합니다.",
                    flush=True,
                )
                if rem:
                    self.current_active_model = rem[0]
                continue

            if resp.status_code != 200:
                raise VlmError(f"Gemini {cur_model} HTTP {resp.status_code}: {resp.text[:300]}")

            try:
                text = self._extract_text_from_resp(resp)
            except VlmError:
                text = ""

            if not text:
                print(f"[Gemini All] [전환] 모델 '{cur_model}' 응답 내용이 비어 있음. 다음 모델로 전환합니다.", flush=True)
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
