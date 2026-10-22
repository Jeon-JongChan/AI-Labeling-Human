"""
gpt-auto-query — ChatGPT 웹(Playwright + Chromium)으로 이미지 분석을 대행하는 크롤러 서버.

라벨러(app/vlm.py)가 이미지 한 장을 POST /caption 으로 보내면,
이 서버가 ChatGPT 임시 채팅에 이미지+프롬프트를 넣고 응답이 끝날 때까지 기다린 뒤 텍스트를 반환합니다.

포터블 구조 (모두 이 폴더 안에 보관):
  browsers/  Playwright Chromium 바이너리 (PLAYWRIGHT_BROWSERS_PATH)
  profile/   Chromium 사용자 프로필 (ChatGPT 로그인 세션 유지)

사용법 (labeler 폴더에서, .venv 파이썬 기준):
  python .tools/gpt-auto-query/server.py --install   # playwright 패키지 + Chromium 설치 (최초 1회)
  python .tools/gpt-auto-query/server.py --login     # 브라우저가 열리면 직접 로그인 후 터미널에서 Enter
  python .tools/gpt-auto-query/server.py             # 서버 기동 (기본 http://127.0.0.1:8791)

API:
  GET  /health                         → {"ok": true, "busy": bool}
  POST /caption {"image_b64","prompt"} → {"text": "..."} | {"error": "..."} (HTTP 4xx/5xx)

주의: 자동화는 ChatGPT 이용약관 위반 소지가 있으므로 반드시 버리는 계정으로 사용하세요.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
BROWSERS_DIR = HERE / "browsers"
PROFILE_DIR = HERE / "profile"
DEBUG_DIR = HERE / "debug"

# Chromium 바이너리를 .tools 안에 두기 위해 playwright import 전에 반드시 지정
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS_DIR))

HOST = os.getenv("GPT_CRAWLER_HOST", "127.0.0.1")
PORT = int(os.getenv("GPT_CRAWLER_PORT", "8791"))
# 헤드리스는 봇 탐지에 걸리기 쉬워 기본은 창 표시(0)
HEADLESS = os.getenv("GPT_HEADLESS", "0").strip().lower() in ("1", "true", "yes")
# 응답 대기 최대 시간(초)
RESPONSE_TIMEOUT = float(os.getenv("GPT_RESPONSE_TIMEOUT", "180"))
# 요청 사이 최소 간격(초) — 사용량 제한/봇 탐지 완화용 보정 knob
MIN_INTERVAL = float(os.getenv("GPT_MIN_INTERVAL", "3"))
# 연속 실패 허용 횟수 — 초과하면 계정 보호를 위해 서버 재시작 전까지 요청 거부(423)
MAX_FAILS = int(os.getenv("GPT_MAX_FAILS", "3"))
CHAT_URL = os.getenv("GPT_CHAT_URL", "https://chatgpt.com/?temporary-chat=true")

# ChatGPT DOM 셀렉터 — UI 변경 시 여기만 수정
SEL_INPUT = '#prompt-textarea, div[contenteditable="true"][role="textbox"]'
SEL_FILE = 'input[type="file"][accept^="image/*"]'
SEL_SEND = (
    'button[data-testid="send-button"]:not([disabled]):not([aria-disabled="true"]), '
    'button[aria-label="보내기"]:not([disabled]):not([aria-disabled="true"]), '
    'button[aria-label="Send prompt"]:not([disabled]):not([aria-disabled="true"])'
)
SEL_STOP = 'button[data-testid="stop-button"], button[aria-label="스트리밍 중지"], button[aria-label="Stop streaming"]'
SEL_ASSISTANT = '[data-markdown-text-style="assistant-message"], [data-message-author-role="assistant"]'
SEL_LOGIN = '[data-testid="login-button"]'

# 이미지가 정상 업로드되지 않아 ChatGPT가 되묻는 응답 패턴 목록
MISSING_IMAGE_PHRASES = (
    "이미지를 첨부",
    "이미지가 첨부",
    "이미지를 올려",
    "이미지가 보이지",
    "이미지를 제공",
    "사진을 첨부",
    "사진이 첨부",
    "사진을 올려",
    "사진이 보이지",
    "사진을 제공",
    "이미지가 누락",
    "please provide the image",
    "please attach the image",
    "please upload the image",
    "haven't attached any image",
    "didn't attach an image",
    "no image was provided",
    "no image provided",
)


class CrawlerError(RuntimeError):
    """크롤링 실패. status 는 HTTP 응답 코드로 사용."""

    def __init__(self, msg: str, status: int = 502):
        super().__init__(msg)
        self.status = status


class GptDriver:
    """Playwright persistent context 로 ChatGPT 를 조작. 단일 스레드에서만 호출할 것."""

    def __init__(self, headless: bool = HEADLESS):
        self.headless = headless
        self._pw = None
        self._ctx = None
        self._last = 0.0
        self.fails = 0  # 연속 실패 횟수 (성공 시 0)

    def start(self) -> None:
        """브라우저를 (이미 떠 있지 않으면) 기동."""
        if self._ctx is not None:
            return
        from playwright.sync_api import sync_playwright

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )

    def stop(self) -> None:
        """브라우저 종료."""
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = self._pw = None

    def _get_active_page(self):
        """유효한 페이지를 반환하며, 브라우저/컨텍스트가 닫혀있으면 안전하게 자동 복원."""
        self.start()
        try:
            for p in self._ctx.pages:
                if not p.is_closed():
                    return p
            return self._ctx.new_page()
        except Exception:
            # 브라우저 프로세스 또는 컨텍스트가 외부에서 닫힌 경우(TargetClosedError 등) 재기동
            self.stop()
            self.start()
            return self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def login_interactive(self) -> None:
        """창을 띄워 사용자가 직접 로그인하도록 대기 (프로필에 세션 저장)."""
        page = self._get_active_page()
        page.goto("https://chatgpt.com/")
        input("브라우저에서 ChatGPT 로그인을 마친 뒤 여기서 Enter 를 누르세요... ")

    def caption(self, image: bytes, prompt: str) -> str:
        """연속 실패 차단기를 거쳐 _caption_once 실행. 로그인 만료(401)는 즉시 차단, 성공 시 카운터 초기화."""
        if self.fails >= MAX_FAILS:
            raise CrawlerError(f"연속 {self.fails}회 실패로 차단됨 — 원인 확인 후 서버를 재시작하세요", 423)
        try:
            text = self._caption_once(image, prompt)
        except CrawlerError as exc:
            # 로그인 필요는 재시도해도 소용없고 계정 위험만 커지므로 바로 차단
            self.fails = MAX_FAILS if exc.status == 401 else self.fails + 1
            raise
        except Exception:
            self.fails += 1
            raise
        self.fails = 0
        return text

    def _caption_once(self, image: bytes, prompt: str) -> str:
        """이미지 한 장 + 프롬프트를 새 임시 채팅에 보내고 응답 텍스트를 반환."""
        from playwright.sync_api import TimeoutError as PwTimeout

        # 요청 간 최소 간격 보장
        wait = MIN_INTERVAL - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)

        page = self._get_active_page()
        try:
            try:
                page.goto(CHAT_URL, wait_until="domcontentloaded")
            except Exception as nav_err:
                # 브라우저 페이지/컨텍스트가 예기치 않게 닫힌 경우 1회 자동 재기동 복구
                if "closed" in str(nav_err).lower() or "target" in str(nav_err).lower():
                    print(f"[gpt-auto-query] 브라우저 연결 끊김 감지 ({nav_err}), 컨텍스트 재시작 중...", flush=True)
                    self.stop()
                    page = self._get_active_page()
                    page.goto(CHAT_URL, wait_until="domcontentloaded")
                else:
                    raise
            try:
                page.wait_for_selector(SEL_INPUT, timeout=30_000)
            except PwTimeout:
                if page.query_selector(SEL_LOGIN):
                    raise CrawlerError("ChatGPT 로그인 필요: server.py --login 실행", 401)
                # 원인 분석용 스크린샷 저장 (debug/ 는 .gitignore 대상)
                DEBUG_DIR.mkdir(exist_ok=True)
                shot = DEBUG_DIR / "no-input.png"
                page.screenshot(path=str(shot))
                raise CrawlerError(
                    f"입력창을 찾지 못함 (캡차/UI 변경 가능성) url={page.url} title={page.title()!r} shot={shot}", 503
                )

            # 새 채팅이므로 기존 assistant 메시지는 0개 — 이후 마지막 메시지가 응답
            page.set_input_files(
                SEL_FILE, {"name": "image.png", "mimeType": "image/png", "buffer": image}
            )
            # 이미지 파일이 업로드되어 썸네일/버튼 상태가 반영될 때까지 대기
            time.sleep(1.0)
            page.fill(SEL_INPUT, prompt)
            # 업로드 완료 및 활성화된 전송 버튼 대기
            page.wait_for_selector(SEL_SEND, state="visible", timeout=60_000)
            time.sleep(0.5)
            page.click(SEL_SEND)

            # 생성 시작(정지 버튼 등장) → 종료(정지 버튼 소멸) 순으로 대기
            try:
                page.wait_for_selector(SEL_STOP, state="visible", timeout=30_000)
            except PwTimeout:
                pass  # 매우 빠른 응답은 정지 버튼을 못 볼 수 있음
            page.wait_for_selector(SEL_STOP, state="detached", timeout=RESPONSE_TIMEOUT * 1000)
            page.wait_for_selector(SEL_ASSISTANT, timeout=10_000)

            text = page.locator(SEL_ASSISTANT).last.inner_text().strip()
            if not text:
                raise CrawlerError("빈 응답", 502)

            # 이미지가 정상 첨부되지 않아 ChatGPT가 되묻는 응답인지 검증 (성공으로 오판 방지)
            lower_text = text.lower()
            for phrase in MISSING_IMAGE_PHRASES:
                if phrase in lower_text:
                    raise CrawlerError(f"이미지 미첨부 인식 오류 (ChatGPT 응답: '{phrase}' 감지됨)", 502)

            print(f"\n[gpt-auto-query] [성공] 응답 텍스트 ({len(text)}자 수신):\n{text}\n" + "-" * 60, flush=True)
            return text
        except PwTimeout as exc:
            DEBUG_DIR.mkdir(exist_ok=True)
            page.screenshot(path=str(DEBUG_DIR / "timeout.png"))
            (DEBUG_DIR / "timeout.html").write_text(page.content(), encoding="utf-8")
            raise CrawlerError(f"응답 대기 시간 초과: {exc}", 504) from exc
        finally:
            self._last = time.time()


class Handler(BaseHTTPRequestHandler):
    """HTTP 핸들러. 단일 스레드 HTTPServer 이므로 요청은 자연스럽게 직렬 처리됨."""

    driver: GptDriver  # serve() 에서 주입

    def _send(self, status: int, body: dict) -> None:
        """JSON 응답 전송."""
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        """헬스체크."""
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        """이미지 캡션 요청 처리."""
        if self.path != "/caption":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(n) or b"{}")
            image = base64.b64decode(req["image_b64"], validate=True)
            prompt = str(req["prompt"])
        except (ValueError, KeyError, TypeError) as exc:  # 입력 검증 (신뢰 경계)
            return self._send(400, {"error": f"잘못된 요청: {exc}"})
        try:
            self._send(200, {"text": self.driver.caption(image, prompt)})
        except CrawlerError as exc:
            print(f"[gpt-auto-query] [요청 실패 HTTP {exc.status}] {exc}", flush=True)
            self._send(exc.status, {"error": str(exc)})
        except Exception as exc:  # 브라우저 크래시 등 — 다음 요청에서 재기동하도록 정리
            try:
                self.driver.stop()
            except Exception:
                pass
            print(f"[gpt-auto-query] [서버 예외 발생 HTTP 500] {type(exc).__name__}: {exc}", flush=True)
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt: str, *args) -> None:
        """기본 stderr 로그를 간결하게."""
        print(f"[gpt-auto-query] {fmt % args}", flush=True)


def serve(driver: GptDriver, host: str = HOST, port: int = PORT) -> HTTPServer:
    """드라이버를 주입한 HTTP 서버 생성 (테스트에서도 재사용)."""
    handler = type("BoundHandler", (Handler,), {"driver": driver})

    class _Server(HTTPServer):
        def server_bind(self) -> None:
            """getfqdn(역방향 DNS) 호출을 생략 — 포터블 Python 에 idna 인코딩이 없어 실패함."""
            import socketserver

            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = host, self.server_address[1]

    return _Server((host, port), handler)


def install() -> None:
    """playwright 패키지와 Chromium(.tools/gpt-auto-query/browsers)을 설치."""
    subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright>=1.40"])
    BROWSERS_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    print(f"[gpt-auto-query] 설치 완료: {BROWSERS_DIR}")


def main() -> None:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description="ChatGPT 웹 자동 질의 크롤러 서버")
    ap.add_argument("--install", action="store_true", help="playwright + Chromium 설치")
    ap.add_argument("--login", action="store_true", help="수동 로그인(세션 저장)")
    args = ap.parse_args()

    if args.install:
        return install()
    driver = GptDriver(headless=False if args.login else HEADLESS)
    if args.login:
        try:
            return driver.login_interactive()
        finally:
            driver.stop()
    srv = serve(driver)
    print(f"[gpt-auto-query] listening on http://{HOST}:{PORT} (headless={HEADLESS})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        driver.stop()


if __name__ == "__main__":
    main()
