"""
통합 체인 VLM(ChainedVlmAdapter) 단위 테스트.

테스트 항목:
1. VlmFactory 레지스트리 및 '+' / ',' 구분자 공급자 생성 검증
2. fallback_on='limit' (기본값): 429 한도 초과 시 전환, 일반 오류 시 예외 전파 검증
3. fallback_on='all': 일반 오류 발생 시에도 다음 공급자로 자동 전환 검증
4. 공급자별 모델 매핑('flash+dots' 등) 지원 검증
5. 모든 공급자 소진 시 Sleep 모드 진입 및 우선순위 순서 복구 감지 검증
"""

from __future__ import annotations

import sys
from pathlib import Path

# 경로 설정
TOOL_ROOT = Path(__file__).resolve().parent.parent.parent
APP_DIR = TOOL_ROOT / "app"
sys.path.insert(0, str(APP_DIR))

from vlm_provider.base import BaseVlmAdapter, VlmError, VlmQuotaExceededError
from vlm_provider.chain import ChainedVlmAdapter
from vlm_provider.factory import VlmFactory


class MockAdapter(BaseVlmAdapter):
    """테스트용 가상 VLM 어댑터."""

    def __init__(self, name: str, model: str = "all", responses: list | None = None):
        self._name = name
        self.model = model
        self.responses = list(responses or [])
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    def _generate_caption(self, image_path: Path, prompt: str) -> str:
        self.call_count += 1
        if not self.responses:
            return f"[{self._name}] 기본 성공 응답"

        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return str(resp)


def test_factory_creation():
    """VlmFactory에서 복합 공급자 생성 테스트."""
    adapter_plus = VlmFactory.create("gemini+free")
    assert isinstance(adapter_plus, ChainedVlmAdapter), "gemini+free는 ChainedVlmAdapter여야 함"
    assert adapter_plus.provider_names == ["gemini", "free"]

    adapter_comma = VlmFactory.create("free,gemini")
    assert isinstance(adapter_comma, ChainedVlmAdapter), "free,gemini는 ChainedVlmAdapter여야 함"
    assert adapter_comma.provider_names == ["free", "gemini"]
    print("[통과] VlmFactory 복합 공급자('+'/',' 구분) 생성 검증 완료")


def test_fallback_on_limit():
    """fallback_on='limit'일 때: 429는 전환, 일반 에러는 중단 검증."""
    dummy_img = TOOL_ROOT / "data" / "labeling" / "images" / "1" / "00000_image.png"
    if not dummy_img.is_file():
        # 임의 더미 파일 생성
        dummy_img.parent.mkdir(parents=True, exist_ok=True)
        dummy_img.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    # 1. 429 발생 시 다음 공급자로 전환 테스트
    m1 = MockAdapter("gemini", responses=[VlmQuotaExceededError("429 한도 초과")])
    m2 = MockAdapter("free", responses=["Free 모델 정상 응답"])
    chain = ChainedVlmAdapter(
        providers=["gemini", "free"],
        fallback_on="limit",
        adapters=[m1, m2],
    )
    result = chain.caption(dummy_img)
    assert result == "Free 모델 정상 응답", f"예상과 다른 응답: {result}"
    assert m1.call_count == 1
    assert m2.call_count == 1
    assert chain._active_index == 1

    # 2. 일반 에러 발생 시: limit 모드에서는 전환되지 않고 예외 발생 테스트
    m1_err = MockAdapter("gemini", responses=[VlmError("500 서버 내부 오류")])
    m2_err = MockAdapter("free", responses=["Free 모델 응답"])
    chain_err = ChainedVlmAdapter(
        providers=["gemini", "free"],
        fallback_on="limit",
        adapters=[m1_err, m2_err],
    )
    try:
        chain_err.caption(dummy_img)
        assert False, "일반 에러 시 예외가 발생해야 함"
    except VlmError as e:
        assert "500" in str(e)
        assert m2_err.call_count == 0, "2순위 공급자가 호출되지 않아야 함"

    print("[통과] fallback_on='limit' 모드 (429 전환, 일반 에러 전파) 검증 완료")


def test_fallback_on_all():
    """fallback_on='all'일 때: 어떤 에러든 다음 공급자로 전환 검증."""
    dummy_img = TOOL_ROOT / "data" / "labeling" / "images" / "1" / "00000_image.png"

    m1 = MockAdapter("gemini", responses=[VlmError("네트워크 타임아웃 오류")])
    m2 = MockAdapter("free", responses=["Free 모델 폴백 응답"])
    chain = ChainedVlmAdapter(
        providers=["gemini", "free"],
        fallback_on="all",
        adapters=[m1, m2],
    )
    result = chain.caption(dummy_img)
    assert result == "Free 모델 폴백 응답", f"예상과 다른 응답: {result}"
    assert m1.call_count == 1
    assert m2.call_count == 1
    assert chain._active_index == 1
    print("[통과] fallback_on='all' 모드 (모든 에러 시 자동 전환) 검증 완료")


def test_model_mapping():
    """'flash+dots' 등 공급자별 모델 매핑 검증."""
    chain = ChainedVlmAdapter(
        providers="gemini+free",
        model="gemini-1.5-flash+dots",
    )
    assert len(chain.adapters) == 2
    assert chain.adapters[0].model == "gemini-1.5-flash"
    assert chain.adapters[1].model == "dots-studio/dots-3-note-preview:free"
    print("[통과] 공급자별 세부 모델 매핑 ('flash+dots') 지원 검증 완료")


def test_chain_sleep_and_recovery():
    """전체 공급자 소진 시 Sleep 및 우선순위 복구 루프 검증."""
    dummy_img = TOOL_ROOT / "data" / "labeling" / "images" / "1" / "00000_image.png"

    # m1: 최초 호출 429 -> Sleep 후 프로브 429
    # m2: 최초 호출 429 -> Sleep 후 프로브 성공 복구!
    m1 = MockAdapter("gemini", responses=[
        VlmQuotaExceededError("Gemini 한도 소진"),
        VlmQuotaExceededError("Gemini 아직 한도 소진"),
    ])
    m2 = MockAdapter("free", responses=[
        VlmQuotaExceededError("Free 한도 소진"),
        "Free 모델 복구 성공!",
        "실제 작업 재개 응답",
    ])

    sleep_called = []

    def fake_sleep(secs: float):
        sleep_called.append(secs)

    chain = ChainedVlmAdapter(
        providers=["gemini", "free"],
        sleep_interval=3600.0,
        fallback_on="limit",
        adapters=[m1, m2],
    )

    # 캡션 생성 시 Sleep을 fake_sleep으로 주입하여 테스트
    orig_sleep_wait = chain._sleep_and_wait_recovery
    def wrapped_sleep_wait(img, pmt):
        return orig_sleep_wait(img, pmt, sleep_fn=fake_sleep)
    chain._sleep_and_wait_recovery = wrapped_sleep_wait

    result = chain.caption(dummy_img)
    assert result == "실제 작업 재개 응답", f"예상과 다른 응답: {result}"
    assert sum(sleep_called) >= 3600.0, "1시간 이상 슬립 대기했어야 함"
    assert chain._active_index == 1, "m2(Free)가 복구되어 활성 인덱스로 지정되어야 함"
    print("[통과] 전체 소진 시 Sleep 대기 및 우선순위 순서 복구 감지 검증 완료")


if __name__ == "__main__":
    print("=" * 50)
    print(" [통합 체인 VLM(ChainedVlmAdapter) 단위 테스트]")
    print("=" * 50)
    test_factory_creation()
    test_fallback_on_limit()
    test_fallback_on_all()
    test_model_mapping()
    test_chain_sleep_and_recovery()
    print("=" * 50)
    print(" [성공] ChainedVlmAdapter 모든 단위 테스트 통과!")
    print("=" * 50)

