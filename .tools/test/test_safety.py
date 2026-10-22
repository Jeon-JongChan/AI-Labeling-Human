"""
라벨러 연속 오류 중단 및 VlmAbortError 전파 로직 검증 스크립트.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
APP_DIR = PROJECT_ROOT / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import requests
import vlm
from vlm import VlmAbortError, VlmError
import server
import batch_process


def test_caption_gpt_errors():
    """GPT 크롤러 에러 시 VlmAbortError 전파 검증."""
    mock_path = MagicMock()
    mock_path.read_bytes.return_value = b"image_data"

    # 1. 401 Unauthorized -> VlmAbortError
    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"error": "ChatGPT 로그인 필요"}
        mock_post.return_value = mock_resp

        try:
            vlm._caption_gpt(mock_path)
            assert False, "401에서 VlmAbortError가 발생해야 함"
        except VlmAbortError as e:
            assert "401" in str(e)
            print("[통과] 401 -> VlmAbortError 정상 발생")

    # 2. 423 Locked (연속 실패 차단) -> VlmAbortError
    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 423
        mock_resp.json.return_value = {"error": "연속 3회 실패로 차단됨"}
        mock_post.return_value = mock_resp

        try:
            vlm._caption_gpt(mock_path)
            assert False, "423에서 VlmAbortError가 발생해야 함"
        except VlmAbortError as e:
            assert "423" in str(e)
            print("[통과] 423 -> VlmAbortError 정상 발생")

    # 3. ConnectionError -> VlmAbortError
    with patch("requests.post", side_effect=requests.ConnectionError("연결 거부")):
        try:
            vlm._caption_gpt(mock_path)
            assert False, "ConnectionError에서 VlmAbortError가 발생해야 함"
        except VlmAbortError as e:
            assert "연결할 수 없습니다" in str(e)
            print("[통과] ConnectionError -> VlmAbortError 정상 발생")


def test_server_batch_vlm_consecutive_errors():
    """웹 서버 배치 워커 연속 실패 시 자동 중단 검증."""
    mock_store = MagicMock()
    mock_store.list_items.return_value = [
        {"id": 1, "image_path": "dummy1.png", "edited_text": "", "order_idx": 1},
        {"id": 2, "image_path": "dummy2.png", "edited_text": "", "order_idx": 2},
        {"id": 3, "image_path": "dummy3.png", "edited_text": "", "order_idx": 3},
        {"id": 4, "image_path": "dummy4.png", "edited_text": "", "order_idx": 4},
    ]

    with patch("vlm.caption_image", side_effect=VlmError("일시적 네트워크 에러")):
        error_counter = [0]
        try:
            server._batch_vlm_for_doc(
                mock_store, 1, "test.pdf", 1, 1,
                error_counter=error_counter, max_errors=3
            )
            assert False, "연속 3회 실패 시 VlmAbortError 발생해야 함"
        except VlmAbortError as e:
            assert "연속 3회 실패" in str(e)
            assert error_counter[0] == 3
            print(f"[통과] server._batch_vlm_for_doc 연속 3회 에러 시 중단: {e}")


def test_batch_process_consecutive_errors():
    """CLI 일괄 처리기 연속 실패 시 자동 중단 검증."""
    mock_store = MagicMock()
    mock_store.list_items.return_value = [
        {"id": 1, "image_path": "dummy1.png", "edited_text": "", "order_idx": 1, "kind": "figure"},
        {"id": 2, "image_path": "dummy2.png", "edited_text": "", "order_idx": 2, "kind": "figure"},
        {"id": 3, "image_path": "dummy3.png", "edited_text": "", "order_idx": 3, "kind": "figure"},
    ]
    with patch("batch_process.caption_image", side_effect=VlmError("캡션 에러")):
        error_counter = [0]
        try:
            batch_process._process_vlm_for_doc(
                mock_store, 1, "test.pdf", "gpt",
                error_counter=error_counter, max_errors=3
            )
            assert False, "연속 3회 실패 시 VlmAbortError 발생해야 함"
        except VlmAbortError as e:
            assert "연속 3회 실패" in str(e)
            assert error_counter[0] == 3
            print(f"[통과] batch_process._process_vlm_for_doc 연속 3회 에러 시 중단: {e}")


if __name__ == "__main__":
    test_caption_gpt_errors()
    test_server_batch_vlm_consecutive_errors()
    test_batch_process_consecutive_errors()
    print("[통과] 모든 안전 중단(Safety Abort) 테스트 완료")

