"""
Gemini All 모드(순차 전환 및 1시간 Sleep 복구) 및 FreeModel VLM 어댑터 검증 테스트 스위트.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

TOOL_DIR = Path(__file__).resolve().parent.parent.parent
APP_DIR = TOOL_DIR / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# standard assert testing
import requests

from vlm_provider.base import VlmError, VlmQuotaExceededError
from vlm_provider.factory import VlmFactory
from vlm_provider.gemini import GeminiAdapter


def test_gemini_model_ranking() -> None:
    """Gemini 모델 목록이 고성능(Pro > Flash > Lite/8B, 최신 버전 우선) 순으로 올바르게 정렬되는지 검증."""
    adapter = GeminiAdapter(api_key="test-key", model="all")
    mock_models = [
        {"name": "models/gemini-1.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-1.5-flash-8b", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
    ]

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": mock_models}
        mock_get.return_value = mock_resp

        models = adapter.get_supported_models("test-key")
        # text-embedding 제외 확인
        assert "text-embedding-004" not in models
        # Pro 모델이 최우선인지 확인
        assert models[0] == "gemini-2.5-pro"
        # 3.8 Flash가 1.5 Flash보다 앞서는지 확인
        assert models.index("gemini-3.8-flash") < models.index("gemini-1.5-flash")
        # 1.5 Flash가 1.5 Flash-8B보다 앞서는지 확인
        assert models.index("gemini-1.5-flash") < models.index("gemini-1.5-flash-8b")
    print("[통과] Gemini 모델 우선순위 정렬 및 필터링 검증 완료")


def test_gemini_all_rotation_on_429(tmp_path: Path) -> None:
    """Gemini all 모드에서 한 모델이 429 한도 초과 시 다음 모델로 자동 전환되는지 검증."""
    test_img = tmp_path / "test.png"
    test_img.write_bytes(b"dummy_image_data")

    adapter = GeminiAdapter(api_key="test-key", model="all")
    adapter._supported_models = ["model-high", "model-mid", "model-low"]

    def mock_post_side_effect(url, *args, **kwargs):
        resp = MagicMock()
        if "model-high" in url:
            resp.status_code = 429
            resp.text = "Quota exceeded"
            return resp
        elif "model-mid" in url:
            resp.status_code = 200
            resp.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": "성공한 캡션"}]}}]
            }
            return resp
        resp.status_code = 500
        return resp

    with patch("requests.post", side_effect=mock_post_side_effect), \
         patch("time.sleep", return_value=None):
        result = adapter.caption(test_img)
        assert result == "성공한 캡션"
        assert adapter.current_active_model == "model-mid"
        assert "model-high" in adapter.exhausted_models
    print("[통과] Gemini all 모드 429 한도 초과 시 다음 모델 자동 전환 검증 완료")


def test_gemini_all_sleep_and_recovery(tmp_path: Path) -> None:
    """Gemini all 모드에서 모든 모델 소진 시 슬립 루프에 들어가고, 한도 복구 시 자동 재개되는지 검증."""
    test_img = tmp_path / "test.png"
    test_img.write_bytes(b"dummy_image_data")

    adapter = GeminiAdapter(api_key="test-key", model="all")
    adapter._supported_models = ["model-1", "model-2"]

    call_count = [0]
    sleep_calls = []

    def mock_post_side_effect(url, *args, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        # 처음 2번은 model-1, model-2 모두 429 소진
        if call_count[0] <= 4:  # 초기 시도 + 429 재시도 2회
            resp.status_code = 429
            resp.text = "Quota exceeded"
            return resp
        # 1시간 대기 후 프로브에서 model-1이 200으로 복구됨
        resp.status_code = 200
        resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "복구 후 생성된 캡션"}]}}]
        }
        return resp

    def fake_sleep(secs):
        sleep_calls.append(secs)

    with patch("requests.post", side_effect=mock_post_side_effect), \
         patch("time.sleep", side_effect=fake_sleep):
        # 1회 슬립 후 복구되도록 설정
        result = adapter._generate_caption_all(test_img, "test prompt", max_sleep_cycles=1)
        assert result == "복구 후 생성된 캡션"
        assert len(adapter.exhausted_models) == 0
    print("[통과] Gemini all 모드 전체 소진 시 슬립 및 복구 감지 정상 작동 완료")


def test_free_model_registration_and_rotation(tmp_path: Path) -> None:
    """FreeModel VLM 어댑터의 팩토리 등록 및 all 모드 순차 전환 동작 검증."""
    from vlm_provider.free_model import FreeModelAdapter

    # 1. 팩토리 등록 확인
    adapter = VlmFactory.create("free_model")
    assert isinstance(adapter, FreeModelAdapter)
    adapter2 = VlmFactory.create("free")
    assert isinstance(adapter2, FreeModelAdapter)

    test_img = tmp_path / "test.png"
    test_img.write_bytes(b"dummy_image_data")

    # 2. FreeModel 순차 폴백 테스트
    free_adapter = FreeModelAdapter(model="all")
    free_adapter._free_models = [
        "nvidia/nemotron-3.5-super-vl:free",
        "dots-studio/dots-3-note-preview:free",
    ]

    def mock_free_post(url, *args, **kwargs):
        payload = kwargs.get("json", {})
        m = payload.get("model")
        resp = MagicMock()
        if m == "nvidia/nemotron-3.5-super-vl:free":
            resp.status_code = 429
            resp.text = "Rate limited"
            return resp
        elif m == "dots-studio/dots-3-note-preview:free":
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [{"message": {"content": "무료 모델 캡션 결과"}}]
            }
            return resp
        resp.status_code = 404
        return resp

    with patch("requests.post", side_effect=mock_free_post), \
         patch("time.sleep", return_value=None):
        res = free_adapter.caption(test_img)
        assert res == "무료 모델 캡션 결과"
        assert free_adapter.current_active_model == "dots-studio/dots-3-note-preview:free"
    print("[통과] FreeModel VLM 팩토리 등록 및 all 순차 폴백 검증 완료")


def test_free_model_sleep_and_recovery(tmp_path: Path) -> None:
    """FreeModel all 모드에서 전체 소진 시 슬립 및 복구 감지 검증."""
    from vlm_provider.free_model import FreeModelAdapter

    test_img = tmp_path / "test.png"
    test_img.write_bytes(b"dummy_image_data")

    free_adapter = FreeModelAdapter(model="all")
    free_adapter._free_models = ["free-model-1", "free-model-2"]

    call_count = [0]

    def mock_free_post_sleep(url, *args, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        if call_count[0] <= 2:
            resp.status_code = 429
            resp.text = "Rate limited"
            return resp
        # 1시간 대기 후 프로브에서 복구
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "무료 모델 복구 후 캡션"}}]
        }
        return resp

    with patch("requests.post", side_effect=mock_free_post_sleep), \
         patch("time.sleep", return_value=None):
        res = free_adapter._generate_caption_all(test_img, "test prompt", max_sleep_cycles=1)
        assert res == "무료 모델 복구 후 캡션"
        assert len(free_adapter.exhausted_models) == 0
    print("[통과] FreeModel all 모드 전체 소진 시 슬립 및 복구 감지 검증 완료")


def main() -> None:
    """단독 실행 러너."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        test_gemini_model_ranking()
        test_gemini_all_rotation_on_429(p)
        test_gemini_all_sleep_and_recovery(p)
        test_free_model_registration_and_rotation(p)
        test_free_model_sleep_and_recovery(p)
    print("\n==================================================")
    print(" [성공] 모든 신규 기능(Gemini All & FreeModel) 테스트 통과!")
    print("==================================================")


if __name__ == "__main__":
    main()
