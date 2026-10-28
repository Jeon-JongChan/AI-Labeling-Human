"""
Gemini 복수 API 키 파싱 및 키 로테이션 검증 스크립트.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# 프로젝트 루트 및 app 디렉터리 경로 등록
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "app"))

import requests

from app.config import parse_gemini_api_keys
from app.vlm_provider.base import VlmQuotaExceededError, VlmError
from app.vlm_provider.gemini import GeminiAdapter


def test_parse_gemini_api_keys():
    """다양한 형식의 GEMINI_API_KEY 입력 파싱 검증."""
    # 1. 쉼표 구분
    res1 = parse_gemini_api_keys("key1, key2,  key3")
    assert res1 == ["key1", "key2", "key3"], f"Expected ['key1', 'key2', 'key3'], got {res1}"

    # 2. JSON 배열
    res2 = parse_gemini_api_keys('["keyA", "keyB"]')
    assert res2 == ["keyA", "keyB"], f"Expected ['keyA', 'keyB'], got {res2}"

    # 3. 단일 키
    res3 = parse_gemini_api_keys("single_key_123")
    assert res3 == ["single_key_123"], f"Expected ['single_key_123'], got {res3}"

    # 4. 공백 및 세미콜론 및 중복
    res4 = parse_gemini_api_keys("key1; key2\nkey1 key3")
    assert res4 == ["key1", "key2", "key3"], f"Expected ['key1', 'key2', 'key3'], got {res4}"

    print("[PASS] parse_gemini_api_keys 검증 성공")


def test_adapter_key_switching():
    """GeminiAdapter 키 순차 전환 및 소진 상태 리셋 검증."""
    adapter = GeminiAdapter(api_key="key1, key2, key3", model="all", allow_sleep=False)
    assert adapter.api_keys == ["key1", "key2", "key3"]
    assert adapter.api_key == "key1"
    assert adapter.current_key_index == 0

    adapter.exhausted_models.add("model-1")
    adapter.current_active_model = "model-1"

    # 키 1 -> 키 2 전환
    has_next = adapter._switch_to_next_key(reason="한도 초과")
    assert has_next is True
    assert adapter.api_key == "key2"
    assert adapter.current_key_index == 1
    assert len(adapter.exhausted_models) == 0  # 소진 목록 리셋
    assert adapter.current_active_model is None

    # 키 2 -> 키 3 전환
    has_next = adapter._switch_to_next_key(reason="한도 초과")
    assert has_next is True
    assert adapter.api_key == "key3"
    assert adapter.current_key_index == 2

    # 키 3 -> 더 이상 없음
    has_next = adapter._switch_to_next_key(reason="한도 초과")
    assert has_next is False
    assert adapter.current_key_index == 2

    print("[PASS] adapter_key_switching 검증 성공")


def test_auth_error_immediate_switch():
    """HTTP 400/403 무효 키 감지 시 즉시 다음 키로 전환하는지 검증."""
    adapter = GeminiAdapter(api_key="bad_key, good_key", model="all", allow_sleep=False)
    adapter._supported_models_cache["bad_key"] = ["gemini-test-model"]
    adapter._supported_models_cache["good_key"] = ["gemini-test-model"]

    call_keys = []

    def mock_post(url, *args, **kwargs):
        resp = MagicMock()
        if "bad_key" in url:
            call_keys.append("bad_key")
            resp.status_code = 400
            resp.text = '{"error": {"message": "API_KEY_INVALID"}}'
            return resp
        elif "good_key" in url:
            call_keys.append("good_key")
            resp.status_code = 200
            resp.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": "테스트 결과입니다."}]}}]
            }
            return resp
        raise RuntimeError("Unexpected url")

    with patch("requests.post", side_effect=mock_post):
        with patch.object(adapter, "encode_image_b64", return_value="dummy"):
            with patch.object(adapter, "_wait_rpm_interval"):
                res = adapter._generate_caption_all(Path("dummy.png"), "prompt")
                assert res == "테스트 결과입니다."
                assert call_keys == ["bad_key", "good_key"]
                assert adapter.api_key == "good_key"

    print("[PASS] test_auth_error_immediate_switch 검증 성공")


def test_quota_exhausted_across_all_keys():
    """모든 키의 모든 모델이 429로 소진되었을 때 allow_sleep=False인 경우 VlmQuotaExceededError 발생 검증."""
    adapter = GeminiAdapter(api_key="key1, key2", model="all", allow_sleep=False)
    adapter._supported_models_cache["key1"] = ["m1", "m2"]
    adapter._supported_models_cache["key2"] = ["m1", "m2"]

    def mock_post(url, *args, **kwargs):
        resp = MagicMock()
        resp.status_code = 429
        resp.text = "daily limit quota exceeded"
        return resp

    with patch("requests.post", side_effect=mock_post):
        with patch.object(adapter, "encode_image_b64", return_value="dummy"):
            with patch.object(adapter, "_wait_rpm_interval"):
                try:
                    adapter._generate_caption_all(Path("dummy.png"), "prompt")
                    assert False, "VlmQuotaExceededError 가 발생해야 합니다."
                except VlmQuotaExceededError as e:
                    assert "모든 API 키" in str(e)

    print("[PASS] test_quota_exhausted_across_all_keys 검증 성공")


if __name__ == "__main__":
    test_parse_gemini_api_keys()
    test_adapter_key_switching()
    test_auth_error_immediate_switch()
    test_quota_exhausted_across_all_keys()
    print("\n>>> 모든 복수 키 로테이션 검증 통과! <<<")

