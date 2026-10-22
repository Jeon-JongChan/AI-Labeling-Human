"""
vlm_provider 패키지 및 디자인 패턴(어댑터, 템플릿 메서드, 팩토리, 파사드) 단위 테스트.
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

from vlm_provider import (
    BaseVlmAdapter,
    ColabAiAdapter,
    GeminiAdapter,
    GptCrawlerAdapter,
    LocalVlmAdapter,
    AutoFallbackAdapter,
    VlmError,
    VlmAbortError,
    VlmFactory,
)
import vlm


class DummyAdapter(BaseVlmAdapter):
    """테스트용 가짜 어댑터."""

    @property
    def name(self) -> str:
        return "dummy"

    def _generate_caption(self, image_path: Path, prompt: str) -> str:
        return f"captioned by dummy with prompt: {prompt}"


def test_template_method():
    """템플릿 메서드 패턴: 파일 검증, 전후처리 동작 검증."""
    adapter = DummyAdapter()

    # 1. 존재하지 않는 파일 검증 (선행 처리)
    non_existent = Path("non_existent_file.png")
    try:
        adapter.caption(non_existent)
        assert False, "존재하지 않는 파일에 대해 VlmError가 발생해야 함"
    except VlmError as e:
        assert "이미지 파일이 존재하지 않습니다" in str(e)
        print("[통과] 템플릿 메서드: 존재하지 않는 파일 검증")

    # 2. 임시 파일 생성 후 정상 처리 검증
    temp_file = HERE / "dummy_test_image.png"
    temp_file.write_bytes(b"dummy image bytes")
    try:
        res = adapter.caption(temp_file, prompt="hello")
        assert res == "captioned by dummy with prompt: hello"
        print("[통과] 템플릿 메서드: 정상 파이프라인 호출")

        # 3. 빈 문자열 반환 시 후행 처리 검증
        with patch.object(adapter, "_generate_caption", return_value="   "):
            try:
                adapter.caption(temp_file)
                assert False, "빈 문자열 결과에 대해 VlmError가 발생해야 함"
            except VlmError as e:
                assert "본문이 비어 있습니다" in str(e)
                print("[통과] 템플릿 메서드: 빈 응답 방어 검증")
    finally:
        if temp_file.exists():
            temp_file.unlink()


def test_factory_and_registry():
    """팩토리 및 레지스트리 패턴 검증."""
    # 1. 기본 프로바이더 생성 검증
    gemini = VlmFactory.create("gemini")
    assert isinstance(gemini, GeminiAdapter)
    local = VlmFactory.create("local")
    assert isinstance(local, LocalVlmAdapter)
    gpt = VlmFactory.create("gpt")
    assert isinstance(gpt, GptCrawlerAdapter)
    colab = VlmFactory.create("colab")
    assert isinstance(colab, ColabAiAdapter)
    auto = VlmFactory.create("auto")
    assert isinstance(auto, AutoFallbackAdapter)
    print("[통과] 팩토리: 기본 5종 공급자 생성 정상")

    # 2. 미지원 프로바이더 예외 검증
    try:
        VlmFactory.create("unknown_provider_xyz")
        assert False, "미지원 공급자에 대해 VlmError가 발생해야 함"
    except VlmError as e:
        assert "지원하지 않는 VLM 공급자" in str(e)
        print("[통과] 팩토리: 미지원 프로바이더 예외 처리")

    # 3. 새로운 어댑터 동적 등록 (OCP 검증)
    VlmFactory.register("custom_model", DummyAdapter)
    custom = VlmFactory.create("custom_model")
    assert isinstance(custom, DummyAdapter)
    assert "custom_model" in VlmFactory.list_providers()
    print("[통과] 팩토리: 신규 어댑터 동적 등록 및 생성 (OCP 확장성 검증)")


def test_facade_and_vlm_module():
    """vlm.py 단일 파사드 진입점 및 팩토리 연동 검증."""
    temp_file = HERE / "facade_test_image.png"
    temp_file.write_bytes(b"facade test image bytes")

    try:
        with patch("config.IMAGES_DIR", HERE):
            # 가짜 어댑터를 팩토리에 등록하여 테스트
            VlmFactory.register("dummy_facade", DummyAdapter)

            # vlm.py 파사드 함수 호출 검증
            res = vlm.caption_image("facade_test_image.png", provider="dummy_facade")
            assert "captioned by dummy" in res
            print("[통과] 파사드: vlm.caption_image 단일 진입점 정상 작동")
    finally:
        if temp_file.exists():
            temp_file.unlink()


def test_auto_fallback_strategy():
    """AutoFallbackAdapter 전략 및 복합 호출 검증."""
    temp_file = HERE / "fallback_test_image.png"
    temp_file.write_bytes(b"fallback test bytes")

    try:
        colab_mock = MagicMock(spec=ColabAiAdapter)
        colab_mock._generate_caption.side_effect = VlmError("코랩 환경 아님")

        gemini_mock = MagicMock(spec=GeminiAdapter)
        from vlm_provider.base import VlmQuotaExceededError
        gemini_mock._generate_caption.side_effect = VlmQuotaExceededError("429 한도 초과")

        local_mock = MagicMock(spec=LocalVlmAdapter)
        local_mock._generate_caption.return_value = "성공: 로컬 Qwen2.5-VL 설명"

        fallback = AutoFallbackAdapter(
            colab_adapter=colab_mock,
            gemini_adapter=gemini_mock,
            local_adapter=local_mock,
        )

        res = fallback.caption(temp_file)
        assert res == "성공: 로컬 Qwen2.5-VL 설명"
        assert AutoFallbackAdapter._gemini_exhausted is True
        print("[통과] AutoFallback: Colab -> Gemini 429 -> Local 자동 폴백 완료")
    finally:
        if temp_file.exists():
            temp_file.unlink()


if __name__ == "__main__":
    test_template_method()
    test_factory_and_registry()
    test_facade_and_vlm_module()
    test_auto_fallback_strategy()
    print("[통과] 모든 VLM 공급자 디자인 패턴 테스트 완료")

