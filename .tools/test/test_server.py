"""
auto-query-web HTTP 계층 및 차단기 검증 — 브라우저 없이 가짜 드라이버로 실행.
"""

from __future__ import annotations

import base64
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
GPT_AQ_DIR = HERE.parent / "auto-query-web"

if str(GPT_AQ_DIR) not in sys.path:
    sys.path.insert(0, str(GPT_AQ_DIR))

import gpt as server  # auto-query-web/gpt.py


class FakeDriver:
    """caption 호출을 기록하고 고정 응답/오류를 돌려주는 가짜 드라이버."""

    def caption(self, image: bytes, prompt: str) -> str:
        if prompt == "boom":
            raise server.CrawlerError("login", 401)
        return f"{len(image)}:{prompt}"


def post(port: int, body: dict) -> tuple[int, dict]:
    """/caption POST 후 (status, json) 반환."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/caption",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_gpt_crawler_server() -> None:
    """크롤러 HTTP 서버 및 423 서킷 브레이커 검증."""
    srv = server.serve(FakeDriver(), "127.0.0.1", 0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    try:
        b64 = base64.b64encode(b"abc").decode()
        assert post(port, {"image_b64": b64, "prompt": "hi"}) == (200, {"text": "3:hi"})
        assert post(port, {"image_b64": b64, "prompt": "boom"})[0] == 401
        assert post(port, {"prompt": "x"})[0] == 400
        assert post(port, {"image_b64": "!!!", "prompt": "x"})[0] == 400
    finally:
        srv.shutdown()

    # 연속 실패 차단기: 3회 실패 후 423, 성공 시 카운터 초기화, 401 은 즉시 차단
    class Flaky(server.GptDriver):
        mode = "fail"

        def _caption_once(self, image, prompt):
            if self.mode == "ok":
                return "ok"
            if self.mode == "login":
                raise server.CrawlerError("login", 401)
            raise server.CrawlerError("boom", 504)

    d = Flaky()
    for _ in range(server.MAX_FAILS):
        try:
            d.caption(b"x", "p")
        except server.CrawlerError as e:
            assert e.status == 504

    try:
        d.caption(b"x", "p")
        raise AssertionError("차단되지 않음")
    except server.CrawlerError as e:
        assert e.status == 423

    d = Flaky()
    d.mode = "ok"
    assert d.caption(b"x", "p") == "ok" and d.fails == 0

    d = Flaky()
    d.mode = "login"
    try:
        d.caption(b"x", "p")
    except server.CrawlerError:
        pass
    assert d.fails >= server.MAX_FAILS

    # 미첨부 문구 감지 패턴 목록 검증
    test_text = "이미지를 첨부해 주시면 상세히 설명해 드리겠습니다."
    assert any(p in test_text for p in server.MISSING_IMAGE_PHRASES)

    print("[통과] auto-query-web 서버 및 차단기 테스트 완료")


def test_environment_and_auto_ensure() -> None:
    """playwright/Chromium 설치 감지 및 자동 보정 유틸리티 검증."""
    # 1. 설치 여부 검사 함수 존재 및 bool 반환 검증
    pw_ok = server.is_playwright_installed()
    assert isinstance(pw_ok, bool)

    chrome_ok = server.is_chromium_installed()
    assert isinstance(chrome_ok, bool)

    # 2. ensure_installed() 실행 (이미 설치된 상태에서 오류 없이 통과해야 함)
    server.ensure_installed(quiet=True)
    print("[통과] auto-query-web 환경 및 자동 보정 테스트 완료")


def test_google_crawler_module() -> None:
    """google.py 모듈 구조 및 설정 검증."""
    import google

    # 1. 포트 및 상속 구조 검증
    assert google.PORT == 8792
    assert issubclass(google.GoogleDriver, server.GptDriver)

    # 2. 노이즈 제거 정규식 검증
    noise_samples = [
        "AI 대답에는 오류가 있을 수 있습니다. 자세히 알아보기",
        "자세히 알아보기",
        "다음을 보냈습니다. 이미지 1개",
        "이전 프롬프트로 건너뛰기",
    ]
    for noise in noise_samples:
        assert google.G_NOISE.match(noise) is not None

    clean_sample = "이것은 정상적인 표/그림 설명 텍스트입니다."
    assert google.G_NOISE.match(clean_sample) is None
    print("[통과] auto-query-web/google.py 구조 및 필터링 검증 완료")


if __name__ == "__main__":
    test_gpt_crawler_server()
    test_environment_and_auto_ensure()
    test_google_crawler_module()
