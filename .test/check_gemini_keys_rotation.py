"""
Gemini 복수 API 키 파싱 및 키 로테이션 검증 스크립트.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "app"))

from app.config import parse_gemini_api_keys
from app.vlm_provider.base import VlmQuotaExceededError
from app.vlm_provider.gemini import GeminiAdapter


def test_parse_gemini_api_keys():
    """다양한 형식의 GEMINI_API_KEY 입력 파싱 검증."""
    assert parse_gemini_api_keys("key1, key2,  key3") == ["key1", "key2", "key3"]
    assert parse_gemini_api_keys('["keyA", "keyB"]') == ["keyA", "keyB"]
    assert parse_gemini_api_keys("['keyA', 'keyB']") == ["keyA", "keyB"]
    assert parse_gemini_api_keys("[key1, key2]") == ["key1", "key2"]
    assert parse_gemini_api_keys("single_key_123") == ["single_key_123"]
    assert parse_gemini_api_keys("key1; key2\nkey1 key3") == ["key1", "key2", "key3"]
    print("[PASS] parse_gemini_api_keys 검증 성공")


def test_adapter_key_switching():
    """GeminiAdapter 키 순차 전환 및 소진 상태 리셋 검증."""
    adapter = GeminiAdapter(api_key="key1, key2, key3", model="all", allow_sleep=False)
    assert adapter.api_keys == ["key1", "key2", "key3"]
    assert adapter.api_key == "key1"

    adapter.exhausted_models.add("model-1")
    adapter.current_active_model = "model-1"

    assert adapter._switch_to_next_key(reason="한도 초과") is True
    assert adapter.api_key == "key2"
    assert len(adapter.exhausted_models) == 0
    assert adapter.current_active_model is None

    assert adapter._switch_to_next_key(reason="한도 초과") is True
    assert adapter.api_key == "key3"
    assert adapter._switch_to_next_key(reason="한도 초과") is False

    adapter.reset()
    assert adapter.api_key == "key1"
    assert adapter.current_key_index == 0
    print("[PASS] adapter_key_switching & reset 검증 성공")


def test_auth_error_immediate_switch():
    """HTTP 400/403 무효 키 감지 시 즉시 다음 키로 전환하는지 검증."""
    adapter = GeminiAdapter(api_key="bad_key, good_key", model="all", allow_sleep=False)
    adapter._supported_models = ["gemini-test-model"]

    call_keys = []

    def mock_post(url, *args, **kwargs):
        resp = MagicMock()
        if "bad_key" in url:
            call_keys.append("bad_key")
            resp.status_code = 400
            resp.text = '{"error": {"message": "API_KEY_INVALID"}}'
            return resp
        if "good_key" in url:
            call_keys.append("good_key")
            resp.status_code = 200
            resp.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": "테스트 결과입니다."}]}}]
            }
            return resp
        raise RuntimeError("Unexpected url")

    with patch("requests.post", side_effect=mock_post), \
         patch.object(adapter, "encode_image_b64", return_value="dummy"), \
         patch.object(adapter, "_wait_rpm_interval"):
        res = adapter._generate_caption_all(Path("dummy.png"), "prompt")
        assert res == "테스트 결과입니다."
        assert call_keys == ["bad_key", "good_key"]
        assert adapter.api_key == "good_key"
    print("[PASS] test_auth_error_immediate_switch 검증 성공")


def test_quota_exhausted_across_all_keys():
    """모든 키의 모든 모델이 429로 소진되었을 때 allow_sleep=False인 경우 VlmQuotaExceededError 발생 검증."""
    adapter = GeminiAdapter(api_key="key1, key2", model="all", allow_sleep=False)
    adapter._supported_models = ["m1", "m2"]

    def mock_post(url, *args, **kwargs):
        resp = MagicMock()
        resp.status_code = 429
        resp.text = "daily limit quota exceeded"
        return resp

    with patch("requests.post", side_effect=mock_post), \
         patch.object(adapter, "encode_image_b64", return_value="dummy"), \
         patch.object(adapter, "_wait_rpm_interval"):
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
