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
    clean_and_validate_caption,
    ColabAiAdapter,
    GeminiAdapter,
    GptCrawlerAdapter,
    GoogleCrawlerAdapter,
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


def test_factory_instance_caching():
    """VlmFactory 인스턴스 캐싱을 통한 활성 모델 및 소진 상태 보존 검증."""
    VlmFactory.reset_instances()

    # 1. 동일 공급자/모델 호출 시 동일한 어댑터 객체 반환 검증
    f1 = VlmFactory.create("free")
    f2 = VlmFactory.create("free")
    assert f1 is f2, "동일한 공급자에 대해 캐시된 동일 인스턴스가 반환되어야 함"

    # 2. 첫 번째 이미지에서 성공한 활성 모델 상태가 두 번째 호출에서도 유지되는지 검증
    f1.current_active_model = "nemotron-vl-test"
    f1.exhausted_models.add("failed-model-xyz")

    f_next = VlmFactory.create("free")
    assert f_next.current_active_model == "nemotron-vl-test", "이전 요청의 활성 모델이 유지되어야 함"
    assert "failed-model-xyz" in f_next.exhausted_models, "소진된 모델 목록이 유지되어야 함"

    # 3. reset_instances 검증
    VlmFactory.reset_instances()
    f3 = VlmFactory.create("free")
    assert f3 is not f1, "reset 후에는 새 인스턴스가 생성되어야 함"
    print("[통과] 팩토리: 인스턴스 캐싱을 통한 활성 모델 및 소진 상태 연속 유지 검증 완료")


def test_factory_and_registry():
    """팩토리 및 레지스트리 패턴 검증."""
    # 1. 기본 프로바이더 생성 검증
    gemini = VlmFactory.create("gemini")
    assert isinstance(gemini, GeminiAdapter)
    local = VlmFactory.create("local")
    assert isinstance(local, LocalVlmAdapter)
    gpt = VlmFactory.create("gpt")
    assert isinstance(gpt, GptCrawlerAdapter)
    google = VlmFactory.create("google")
    assert isinstance(google, GoogleCrawlerAdapter)
    colab = VlmFactory.create("colab")
    assert isinstance(colab, ColabAiAdapter)
    auto = VlmFactory.create("auto")
    assert isinstance(auto, AutoFallbackAdapter)
    free = VlmFactory.create("free")
    assert isinstance(free, vlm.FreeModelAdapter)
    free_model = VlmFactory.create("free_model")
    assert isinstance(free_model, vlm.FreeModelAdapter)
    print("[통과] 팩토리: 기본 공급자 생성 정상 (gemini, free_model, local, gpt, google, colab, auto)")

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


def test_strip_conversational_noise():
    """서두 메타 문구 및 말미 대화 유도(사족) 마지막 문장 제거 검증."""
    from vlm_provider.base import strip_conversational_noise

    # 1. 정상 데이터는 온전히 보존
    clean = "| 헤더 | 값 |\n|---|---|\n| STA | 0x01 |"
    assert strip_conversational_noise(clean) == clean

    # 2. 말미 대화 유도 문장(별도 라인) 잘라냄
    dirty1 = clean + "\n\n이 패킷 구조에서 STA(?)에 들어갈 구체적인 상태 코드 값이나 추가적인 명령(CMD) 정의가 필요하시면 말씀해 주세요."
    assert strip_conversational_noise(dirty1) == clean

    # 3. 같은 줄 끝에 붙은 사족 마지막 문장만 정확히 잘라냄
    dirty2 = "STA 필드는 1바이트 상태 코드입니다. 추가 정보가 필요하시면 언제든 말씀해 주세요."
    assert strip_conversational_noise(dirty2) == "STA 필드는 1바이트 상태 코드입니다."

    # 4. 질문형 맺음말 잘라냄
    dirty3 = "다이어그램 흐름 요약입니다.\n더 자세한 설명이 필요하신가요?"
    assert strip_conversational_noise(dirty3) == "다이어그램 흐름 요약입니다."

    # 5. 서두 메타 문구 및 말미 사족 동시 제거
    dirty4 = "제시된 이미지의 표 내용은 다음과 같습니다:\n" + clean + "\n도움이 필요하시면 알려주세요."
    assert strip_conversational_noise(dirty4) == clean

    # 6. 추론 태그(<think>) 제거 검증
    dirty5 = "<think>\n사고 과정 단계 1... 단계 2...\n</think>\n" + clean
    assert strip_conversational_noise(dirty5) == clean

    # 7. 텍스트형 사고 과정 블록 및 최종 출력 분리 검증
    dirty6 = "Thinking Process:\n1. Analyze the request...\n2. Analyze the image...\n\nFinal Output:\n" + clean
    assert strip_conversational_noise(dirty6) == clean

    # 8. 본문 없이 사고 과정만 출력하다 토큰 소진으로 잘린 경우 빈 문자열 반환 검증
    dirty7 = "Thinking Process:\n1. Analyze the request...\n2. Analyze the image... And the"
    assert strip_conversational_noise(dirty7) == ""

    # 9. 외곽 마크다운 코드블록 래핑 해제 검증
    wrapped = "```markdown\n| A | B |\n|---|---|\n| 1 | 2 |\n```"
    assert clean_and_validate_caption(wrapped) == "| A | B |\n|---|---|\n| 1 | 2 |"

    # 10. 서버 에러 단문 감지 검증
    assert clean_and_validate_caption("502 Bad Gateway\nnginx") == ""
    try:
        clean_and_validate_caption("502 Bad Gateway", raise_on_empty=True, provider_name="test_p")
        assert False, "서버 에러는 raise_on_empty=True 시 예외를 던져야 함"
    except VlmError as e:
        assert "서버 에러 응답 수신" in str(e)

    # 11. 특수문자/공백만 있는 무의미 찌꺼기 필터링 검증
    assert clean_and_validate_caption("... - | #") == ""

    # 12. 안전(Safety) 모델 오답 차단 검증 (User Safety: safe)
    assert clean_and_validate_caption("User Safety: safe") == ""
    try:
        clean_and_validate_caption("User Safety: safe", raise_on_empty=True, provider_name="free")
        assert False, "안전 모델 응답은 예외를 던져야 함"
    except VlmError as e:
        assert "안전(Safety) 분류 모델 오답 수신" in str(e)

    # 13. 영어 메타 사고/규칙 독백(The user wants me to...) 정제 및 표 본문 보존 검증
    monologue_with_table = (
        "The user wants me to extract text from the provided image.\n"
        "I need to follow specific rules:\n"
        "1. No conversational noise\n\n"
        "| Field | Size |\n"
        "|---|---|\n"
        "| Sub-CMD | 1Byte |"
    )
    cleaned_table = clean_and_validate_caption(monologue_with_table)
    assert cleaned_table == "| Field | Size |\n|---|---|\n| Sub-CMD | 1Byte |"

    # 14. 표/본문 없는 독백만 있는 경우 빈 문자열 / VlmError 반환 검증
    monologue_only = "The user wants me to extract text from an image. I need to follow rules..."
    assert clean_and_validate_caption(monologue_only) == ""

    # 15. 영어 대화형 서두 제거 검증 (The image is a sequence diagram titled...)
    opening_noise = (
        'The image is a sequence diagram titled "On-Demand Access"\n'
        "**Key Flow:**\n"
        "* FEP sends Request"
    )
    assert clean_and_validate_caption(opening_noise) == "**Key Flow:**\n* FEP sends Request"

    print("[통과] VLM 응답 단일 표준 검증 함수(clean_and_validate_caption) 종합 검증 완료")


def test_chained_vlm_adapter_with_google():
    """gemini+free+google 3단계 복수 체인 통합 및 순차 폴백 검증."""
    from vlm_provider.chain import ChainedVlmAdapter
    from vlm_provider.base import VlmQuotaExceededError

    # 1. 팩토리를 통한 3중 체인 생성 검증
    chain = VlmFactory.create("gemini+free+google")
    assert isinstance(chain, ChainedVlmAdapter)
    assert len(chain.adapters) == 3
    assert chain.provider_names == ["gemini", "free", "google"]
    assert isinstance(chain.adapters[0], GeminiAdapter)
    assert isinstance(chain.adapters[1], vlm.FreeModelAdapter)
    assert isinstance(chain.adapters[2], GoogleCrawlerAdapter)

    # 2. 순차 한도 초과 시 3단계 폴백 동작 검증 (Mock)
    gemini_mock = MagicMock(spec=GeminiAdapter)
    gemini_mock.name = "gemini"
    gemini_mock._generate_caption.side_effect = VlmQuotaExceededError("Gemini 429 한도 초과")

    free_mock = MagicMock(spec=vlm.FreeModelAdapter)
    free_mock.name = "free"
    free_mock._generate_caption.side_effect = VlmQuotaExceededError("Free 429 Rate Limit")

    google_mock = MagicMock(spec=GoogleCrawlerAdapter)
    google_mock.name = "google"
    google_mock._generate_caption.return_value = "성공: Google AI 모드 캡션"

    mock_chain = ChainedVlmAdapter(
        providers=["gemini", "free", "google"],
        adapters=[gemini_mock, free_mock, google_mock],
    )

    dummy_img = HERE / "dummy_chain_img.png"
    dummy_img.write_bytes(b"dummy")
    try:
        res = mock_chain.caption(dummy_img, prompt="test")
        assert res == "성공: Google AI 모드 캡션"
        assert mock_chain._active_index == 2
        print("[통과] ChainedVlmAdapter: gemini -> free -> google 3단계 순차 한도 초과 자동 전환 완료")
    finally:
        if dummy_img.exists():
            dummy_img.unlink()


def test_gemini_modality_filter_and_fallback():
    """Gemini TTS 등 비전 미지원 모델 사전 필터링 및 400 모달리티 오류 자동 전환 검증."""
    # 1. 사전 필터링 검증: tts 모델이 필터링되는지 확인
    fake_raw = {
        "models": [
            {"name": "models/gemini-3.8-flash-tts", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
        ]
    }
    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = fake_raw

        adapter = GeminiAdapter(api_key="fake-key", model="all")
        models = adapter.get_supported_models("fake-key")
        assert "gemini-3.8-flash-tts" not in models, "TTS 모델은 비전 모델 목록에서 제외되어야 함"
        assert "gemini-2.5-flash" in models, "정상 Flash 모델은 포함되어야 함"

    # 2. 런타임 400 모달리티 오류 시 다음 모델로 자동 전환되는지 검증
    adapter._supported_models = ["gemini-bad-model", "gemini-good-model"]
    dummy_img = HERE / "dummy_modality_img.png"
    dummy_img.write_bytes(b"dummy")

    try:
        # 첫 번째 모델은 400 Modality Error, 두 번째 모델은 200 성공 Mock
        resp_400 = MagicMock()
        resp_400.status_code = 400
        resp_400.text = '{"error": {"message": "Image input modality is not enabled for this model"}}'

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "성공한 비전 캡션"}]}}]
        }

        with patch("requests.post", side_effect=[resp_400, resp_200]):
            res = adapter._generate_caption_all(dummy_img, prompt="test")
            assert res == "성공한 비전 캡션"
            assert "gemini-bad-model" in adapter.exhausted_models
            assert adapter.current_active_model == "gemini-good-model"
            print("[통과] Gemini: TTS 사전 필터링 및 400 모달리티 오류 시 자동 전환 검증 완료")
    finally:
        if dummy_img.exists():
            dummy_img.unlink()


def test_sub_adapter_sleep_bypass_in_chain():
    """체인에 포함된 서브 어댑터는 독자 슬립하지 않고 즉시 VlmQuotaExceededError를 발생시켜 다음 공급자로 폴백하는지 검증."""
    from vlm_provider.base import VlmQuotaExceededError
    from vlm_provider.chain import ChainedVlmAdapter

    # 1. allow_sleep=False 인 GeminiAdapter 검증
    gemini = GeminiAdapter(api_key="fake-key", model="all", allow_sleep=False)
    gemini._supported_models = ["model-a", "model-b"]
    gemini.exhausted_models = {"model-a", "model-b"}  # 모든 모델 소진 상태

    dummy_img = HERE / "dummy_sub_sleep.png"
    dummy_img.write_bytes(b"dummy")
    try:
        try:
            gemini._generate_caption_all(dummy_img, prompt="test")
            assert False, "allow_sleep=False 일 때 슬립하지 않고 VlmQuotaExceededError를 던져야 함"
        except VlmQuotaExceededError as e:
            assert "일일 한도 소진" in str(e)

        # 2. ChainedVlmAdapter 에 탑재된 경우 체인의 다음 공급자로 즉시 넘어가는지 검증
        free_mock = MagicMock()
        free_mock.name = "free"
        free_mock._generate_caption.return_value = "성공: Free 공급자 응답"

        chain = ChainedVlmAdapter(
            providers=["gemini", "free"],
            adapters=[gemini, free_mock],
        )
        res = chain.caption(dummy_img, prompt="test")
        assert res == "성공: Free 공급자 응답"
        assert chain._active_index == 1
        print("[통과] 체인 내 서브 어댑터 슬립 방지 및 다음 공급자 즉각 전환 검증 완료")
    finally:
        if dummy_img.exists():
            dummy_img.unlink()


def test_gemini_quota_scoring_and_rpm_distinction():
    """Gemini 무료 티어 모델 가중치(500 RPD 우선) 및 RPM vs RPD 429 처리 구분 검증."""
    from vlm_provider.gemini import score_gemini_model, GeminiAdapter

    # 1. 모델 점수 우선순위 검증 (500회 한도 Lite 계열이 20회 Flash 및 0회 Pro보다 최우선이어야 함)
    assert score_gemini_model("gemini-3.5-flash-lite") > score_gemini_model("gemini-3.8-flash")
    assert score_gemini_model("gemini-3.1-flash-lite") > score_gemini_model("gemini-3.8-flash")
    assert score_gemini_model("gemini-3.8-flash") > score_gemini_model("gemini-2.5-pro")

    # 2. RPM 일시 한도(429) 시 영구 소진하지 않고 재시도 성공 검증
    adapter = GeminiAdapter(api_key="fake-key", model="all")
    adapter._supported_models = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    dummy_img = HERE / "dummy_rpm_img.png"
    dummy_img.write_bytes(b"dummy")

    try:
        resp_rpm_429 = MagicMock()
        resp_rpm_429.status_code = 429
        resp_rpm_429.text = '{"error": {"message": "Resource has been exhausted (e.g. check quota): Queries per minute exceeded"}}'

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "RPM 재시도 후 성공한 캡션"}]}}]
        }

        with patch("time.sleep") as mock_sleep, patch("requests.post", side_effect=[resp_rpm_429, resp_ok]):
            res = adapter._generate_caption_all(dummy_img, prompt="test")
            assert res == "RPM 재시도 후 성공한 캡션"
            assert "gemini-3.5-flash-lite" not in adapter.exhausted_models, "RPM 일시 한도는 exhausted_models에 추가되지 않아야 함"
            mock_sleep.assert_called()

        print("[통과] Gemini: 500 RPD 모델 우선순위 및 RPM(분당)/RPD(일일) 구분 정상 작동")
    finally:
        if dummy_img.exists():
            dummy_img.unlink()


def test_free_model_auto_switch_on_thinking_process():
    """사고 과정(Thinking Process)만 출력하다 잘린 모델 발생 시 어댑터 내부에서 다음 모델로 자동 전환되는지 검증."""
    from vlm_provider.free_model import FreeModelAdapter

    adapter = FreeModelAdapter(model="all", allow_sleep=False)
    adapter._free_models = ["dots-thinking-model", "nemotron-good-model"]
    dummy_img = HERE / "dummy_thinking_switch.png"
    dummy_img.write_bytes(b"dummy")

    try:
        # 첫 번째 모델은 미완성 사고과정만 반환, 두 번째 모델은 정상 마크다운 반환
        resp_thinking = MagicMock()
        resp_thinking.status_code = 200
        resp_thinking.json.return_value = {
            "choices": [{"message": {"content": "Thinking Process:\n1. Analyze the request...\n2. Analyze the image..."}}]
        }

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {
            "choices": [{"message": {"content": "| Table Header |\n| :--- |\n| Data 1 |"}}]
        }

        with patch("requests.post", side_effect=[resp_thinking, resp_ok]):
            res = adapter.caption(dummy_img, prompt="test")
            assert "Table Header" in res
            assert "dots-thinking-model" in adapter.exhausted_models, "미완성 사고과정 모델은 소진 목록에 추가되어야 함"
            assert adapter.current_active_model == "nemotron-good-model", "다음 정상 모델로 활성 모델이 전환되어야 함"

        print("[통과] FreeModel: 사고 과정(Thinking Process) 미완성 응답 시 다음 모델 자동 전환 검증 완료")
    finally:
        if dummy_img.exists():
            dummy_img.unlink()


if __name__ == "__main__":
    test_template_method()
    test_factory_instance_caching()
    test_factory_and_registry()
    test_facade_and_vlm_module()
    test_auto_fallback_strategy()
    test_strip_conversational_noise()
    test_chained_vlm_adapter_with_google()
    test_gemini_modality_filter_and_fallback()
    test_sub_adapter_sleep_bypass_in_chain()
    test_gemini_quota_scoring_and_rpm_distinction()
    test_free_model_auto_switch_on_thinking_process()
    print("[통과] 모든 VLM 공급자 디자인 패턴 테스트 완료")

