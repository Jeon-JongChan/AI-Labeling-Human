"""
auto-query-web / google — Google AI 모드(https://www.google.com/search?udm=50)로 이미지 분석을 대행하는 크롤러 서버.

gpt.py 의 서버/브라우저 인프라(GptDriver, Handler, serve, 설치 보정)를 재사용하고
질의 절차(_caption_once)만 Google AI 모드용으로 교체합니다. 로그인이 필요 없으며
Chromium 바이너리(browsers/)는 gpt.py 와 공유, 사용자 프로필은 profile-google/ 로 분리합니다.

사용법 (labeler 폴더에서):
  python .tools/auto-query-web/google.py             # 서버 기동 (기본 http://127.0.0.1:8792)

API 는 gpt.py 와 동일: POST /caption {"image_b64","prompt"} → {"text": "..."} | {"error": "..."}

주의: 자동화 트래픽은 Google 이 캡차(/sorry/)로 차단할 수 있습니다. 감지 시 423 으로 즉시 중단합니다.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import gpt  # 같은 폴더의 공용 인프라 (PLAYWRIGHT_BROWSERS_PATH 도 gpt import 시 설정됨)
from gpt import CrawlerError, GptDriver

PORT = int(gpt._clean_env_val(os.getenv("GOOGLE_CRAWLER_PORT", "8792")))
# Google 전용 오버라이드가 있으면 우선 적용, 없으면 gpt.py 의 공통 WEB_QUERY_* 값 사용
MIN_INTERVAL = float(gpt._clean_env_val(os.getenv("GOOGLE_MIN_INTERVAL", "")) or gpt.MIN_INTERVAL)
RESPONSE_TIMEOUT = float(gpt._clean_env_val(os.getenv("GOOGLE_RESPONSE_TIMEOUT", "")) or gpt.RESPONSE_TIMEOUT)
HEADLESS = gpt._clean_env_val(os.getenv("GOOGLE_HEADLESS", str(int(gpt.HEADLESS)))).lower() in ("1", "true", "yes")
PROFILE_GOOGLE = gpt.HERE / "profile-google"
AI_URL = gpt._clean_env_val(os.getenv("GOOGLE_AI_URL", "https://www.google.com/search?udm=50&hl=ko"))

# Google AI 모드 DOM 셀렉터 — UI 변경 시 여기만 수정
SEL_G_INPUT = "textarea"  # 화면에 보이는 첫 textarea 를 사용
SEL_G_PLUS = '[aria-label="파일 및 도구 추가"], [aria-label*="Add files"], [aria-label*="Upload"]'
RE_G_ADD_IMAGE = re.compile(r"이미지 추가|Add image|Upload image", re.I)
SEL_G_THUMB = 'img[src^="data:image"], img[src^="blob:"]'  # 입력창에 올라온 첨부 썸네일
SEL_G_ANSWER = '[data-subtree="aimc"]'  # AI 모드 답변 컨테이너
G_DONE_TEXT = "대답이 준비되었습니다"  # 답변 완료 시 나타나는 상태 문구
# 답변 본문 뒤에 붙는 안내/echo 문구 제거용
G_NOISE = re.compile(
    r"^(AI 대답에는 오류가 있을 수 있습니다.*|자세히 알아보기|다음을 보냈습니다.*|이전 프롬프트로 건너뛰기|"
    r"(?:제시된|해당|첨부된)?\s*이미지[^:\n]*?(?:내용은|텍스트는)?\s*다음과\s*같습니다[:\s]*|"
    r".*(?:필요하시면|궁금하신|추가적인|추가로|자세한 설명).*(?:말씀해 주세요|알려주세요|질문해 주세요|바랍니다|가요\?)[\.!\s]*)$",
    re.I,
)


class GoogleDriver(GptDriver):
    """Google AI 모드를 조작하는 드라이버. 연속 실패 차단기/브라우저 복구는 GptDriver 를 상속."""

    def __init__(self, headless: bool = gpt.HEADLESS):
        super().__init__(headless)
        self.profile_dir = PROFILE_GOOGLE

    def _caption_once(self, image: bytes, prompt: str) -> str:
        """이미지 한 장 + 프롬프트를 AI 모드에 보내고 답변 텍스트를 반환."""
        wait = MIN_INTERVAL - (time.time() - self._last)
        if wait > 0:
            print(f"[auto-query-web] 요청 간 최소 휴식 시간({MIN_INTERVAL:.0f}s) 보장 중... {wait:.1f}초 대기", flush=True)
            time.sleep(wait)

        page = self._get_active_page()
        try:
            page.goto(AI_URL, wait_until="domcontentloaded")
            self._check_blocked(page)
            page.locator(SEL_G_INPUT).first.wait_for(state="visible", timeout=30_000)

            gpt.DEBUG_DIR.mkdir(exist_ok=True)
            upload_path = gpt.DEBUG_DIR / f"_upload_google_{os.getpid()}.png"
            upload_path.write_bytes(image)

            # 1. '+' → '이미지 추가' 메뉴로 file chooser 를 열어 이미지 첨부
            page.locator(SEL_G_PLUS).first.click()
            with page.expect_file_chooser(timeout=8_000) as fc:
                page.get_by_text(RE_G_ADD_IMAGE).first.click()
            fc.value.set_files(str(upload_path))

            # 2. 썸네일이 입력창에 올라온 것을 확인 — 없으면 전송 금지 (헛질의 방지)
            try:
                page.wait_for_selector(SEL_G_THUMB, state="visible", timeout=15_000)
            except Exception:
                shot = gpt.DEBUG_DIR / f"google_missing_thumb_{int(time.time())}.png"
                page.screenshot(path=str(shot))
                raise CrawlerError(f"이미지 첨부 실패 (썸네일 미생성) [shot: {shot.name}]", 502)

            # 3. 첨부 확인 후 텍스트 붙여넣기 + Enter 전송
            time.sleep(1.0)
            box = page.locator(SEL_G_INPUT + ":visible").first
            box.click()
            page.keyboard.insert_text(prompt)
            time.sleep(0.5)
            page.keyboard.press("Enter")

            # 4. 답변 완료 대기: 완료 문구 출현 또는 본문이 6초간 변하지 않으면 완료로 간주
            text = self._wait_answer(page)
            if not text:
                raise CrawlerError("빈 응답", 502)
            print(f"\n[auto-query-web] [성공] Google 응답 ({len(text)}자 수신):\n{text}\n" + "-" * 60, flush=True)
            return text
        finally:
            self._last = time.time()

    @staticmethod
    def _check_blocked(page) -> None:
        """캡차(/sorry/) 차단 감지 — 재시도 무의미하므로 423 으로 배치 즉시 중단."""
        if "/sorry/" in page.url or "unusual traffic" in page.content()[:5000].lower():
            shot = gpt.DEBUG_DIR / f"google_blocked_{int(time.time())}.png"
            gpt.DEBUG_DIR.mkdir(exist_ok=True)
            page.screenshot(path=str(shot))
            raise CrawlerError(f"Google 캡차/비정상 트래픽 차단 감지 — 브라우저에서 직접 해결 후 재시작 [shot: {shot.name}]", 423)

    @staticmethod
    def _wait_answer(page) -> str:
        """답변이 완료될 때까지 폴링하고 정리된 답변 텍스트를 반환."""
        end = time.time() + RESPONSE_TIMEOUT
        last, stable_since = "", time.time()
        while time.time() < end:
            time.sleep(1.0)
            ans = page.locator(SEL_G_ANSWER)
            cur = ans.last.inner_text().strip() if ans.count() else ""
            done = G_DONE_TEXT in page.inner_text("body")
            if cur != last:
                last, stable_since = cur, time.time()
            if cur and (done or time.time() - stable_since > 6):
                break
        lines = [ln.strip() for ln in last.splitlines() if ln.strip() and not G_NOISE.match(ln.strip())]
        # 마지막 줄이 사족 문장으로 끝나면 해당 문장 잘라냄
        while lines:
            sentences = re.split(r"(?<=[.?!])\s+", lines[-1])
            while sentences and re.search(r"(?:말씀해\s*주세요|알려주세요|질문해\s*주세요|바랍니다|가요\?)[\.!\s]*$", sentences[-1], re.I):
                sentences.pop()
            if not sentences:
                lines.pop()
                continue
            lines[-1] = " ".join(sentences).strip()
            break
        return "\n".join(lines)


def main() -> None:
    """CLI 진입점 — gpt.main 과 동일 구조, 드라이버/포트만 Google 용."""
    gpt._reexec_in_venv()
    gpt.ensure_installed()
    driver = GoogleDriver()
    srv = gpt.serve(driver, gpt.HOST, PORT)
    print(
        f"[auto-query-web] google listening on http://{gpt.HOST}:{PORT} "
        f"(min_interval={MIN_INTERVAL:.0f}s, timeout={RESPONSE_TIMEOUT:.0f}s, headless={HEADLESS})",
        flush=True,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        driver.stop()


if __name__ == "__main__":
    main()
