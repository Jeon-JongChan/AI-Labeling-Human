"""
VLM(비전-언어 모델) 캡셔닝 — Google Colab AI / Gemini API / 로컬 Qwen2.5-VL.

우선순위:
1) Colab AI / Colab Secrets (구글 코랩 환경일 때 우선 사용)
2) Google Gemini API (GEMINI_API_KEY 기반, 무료 1500회)
3) 로컬/Colab Qwen2.5-VL (위 클라우드 할당량 소진 시 자동 전환)
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
import time
import requests

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    IMAGES_DIR,
    VLM_API_KEY,
    VLM_BASE_URL,
    VLM_MAX_TOKENS,
    VLM_MODEL,
    VLM_PROMPT,
    VLM_PROVIDER,
    VLM_TEMPERATURE,
    VLM_TIMEOUT,
    vlm_server_cmd_hint,
)


class VlmError(RuntimeError):
    pass


class VlmQuotaExceededError(VlmError):
    """Gemini 무료 티어 일일 쿼터(429) 초과 시 발생."""
    pass


def _try_get_colab_key() -> str:
    """Colab Secrets(userdata)에 등록된 GEMINI_API_KEY가 있는지 확인."""
    try:
        from google.colab import userdata  # type: ignore
        return userdata.get("GEMINI_API_KEY") or ""
    except Exception:
        return ""


def _caption_colab_ai(path: Path) -> str | None:
    """google.colab.ai 모듈이 사용 가능한 경우 이를 우선 호출."""
    try:
        import google.colab.ai  # type: ignore
        # google.colab.ai 인터페이스가 있으면 실행
        if hasattr(google.colab.ai, "generate"):
            res = google.colab.ai.generate(prompt=VLM_PROMPT, image=str(path))
            if res and isinstance(res, str):
                return res.strip()
    except Exception:
        pass
    return None


def _caption_gemini(path: Path, api_key: str | None = None) -> str:
    """Google Gemini REST API로 이미지 설명 생성 (무료 티어 하루 1500회)."""
    key = api_key or GEMINI_API_KEY or _try_get_colab_key()
    if not key:
        raise VlmError("GEMINI_API_KEY 가 설정되지 않았습니다.")

    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    candidate_models = [GEMINI_MODEL, "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-flash-latest"]
    seen = set()
    models_to_try = [m for m in candidate_models if m and not (m in seen or seen.add(m))]

    last_error = None
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": VLM_PROMPT},
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
            # 15 RPM 한도 초과 시 5초 대기 후 1회 재시도
            time.sleep(5)
            try:
                resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=VLM_TIMEOUT)
            except Exception:
                pass
            if resp.status_code == 429:
                raise VlmQuotaExceededError(
                    "Gemini API 사용량 한도 초과 (HTTP 429: Rate Limit / Quota Exceeded)"
                )
        if resp.status_code in (404, 503):
            last_error = f"Gemini {model_name} HTTP {resp.status_code}: {resp.text[:200]}"
            continue
        if resp.status_code != 200:
            raise VlmError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise VlmError(f"Gemini 응답에 candidates 없음: {data}")

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise VlmError("Gemini 응답 내용(parts)이 비어 있음")

        return parts[0].get("text", "").strip()

    raise VlmError(f"Gemini 모델 호출 실패 (모든 모델 시도함): {last_error}")


def _caption_local(path: Path) -> str:
    """로컬 VLM(OpenAI 호환 포트 8090 / Qwen2.5-VL / llama-server) 호출."""
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    payload: dict = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VLM_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
        "temperature": VLM_TEMPERATURE,
        "max_tokens": VLM_MAX_TOKENS,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
        "enable_thinking": False,
    }
    if VLM_MODEL:
        payload["model"] = VLM_MODEL

    try:
        resp = requests.post(
            f"{VLM_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {VLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=VLM_TIMEOUT,
        )
    except requests.ConnectionError as exc:
        raise VlmError(
            f"로컬 VLM 서버({VLM_BASE_URL})에 연결할 수 없습니다. "
            "labeler.bat|sh 또는 tools/labeler/vlm_server.py 기동 필요: "
            f"{vlm_server_cmd_hint()}"
        ) from exc
    except requests.Timeout as exc:
        raise VlmError(f"로컬 VLM 응답 시간 초과 ({VLM_TIMEOUT:.0f}초)") from exc

    if resp.status_code != 200:
        raise VlmError(f"로컬 VLM HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise VlmError("로컬 VLM 응답에 choices 없음")

    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        content = (message.get("reasoning_content") or "").strip()
    if not content:
        raise VlmError("로컬 VLM 응답 본문이 비어 있음")
    return content


def caption_image(image_rel_path: str, provider: str | None = None) -> str:
    """
    크롭 이미지(IMAGES_DIR 상대 경로)를 VLM으로 설명.
    우선순위: Colab AI -> Gemini API -> 로컬 VLM
    """
    path = IMAGES_DIR / image_rel_path
    if not path.is_file():
        raise VlmError(f"이미지 파일 없음: {image_rel_path}")

    current_provider = (provider or VLM_PROVIDER).lower()

    if current_provider in ("gemini", "colab", "auto"):
        # 1. Colab 환경 자체 AI 먼저 시도
        colab_res = _caption_colab_ai(path)
        if colab_res:
            return colab_res

        # 2. Gemini API 시도
        colab_key = _try_get_colab_key()
        has_key = bool(GEMINI_API_KEY or colab_key)
        if has_key:
            try:
                return _caption_gemini(path, api_key=colab_key or None)
            except VlmQuotaExceededError as q_err:
                print(f"[vlm] [경고] {q_err} -> 로컬/Colab Qwen2.5-VL 모델로 자동 전환합니다.", flush=True)
                return _caption_local(path)

    # 3. 로컬 VLM 호출
    return _caption_local(path)
