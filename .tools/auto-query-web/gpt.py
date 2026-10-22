"""
auto-query-web — ChatGPT 웹(Playwright + Chromium)으로 이미지 분석을 대행하는 크롤러 서버.

라벨러(app/vlm.py)가 이미지 한 장을 POST /caption 으로 보내면,
이 서버가 ChatGPT 임시 채팅에 이미지+프롬프트를 넣고 응답이 끝날 때까지 기다린 뒤 텍스트를 반환합니다.

포터블 구조 (모두 이 폴더 안에 보관):
  browsers/  Playwright Chromium 바이너리 (PLAYWRIGHT_BROWSERS_PATH)
  profile/   Chromium 사용자 프로필 (ChatGPT 로그인 세션 유지)

사용법 (labeler 폴더에서, .venv 파이썬 기준):
  python .tools/auto-query-web/gpt.py --install   # playwright 패키지 + Chromium 설치 (최초 1회, 미설치 시 자동 실행됨)
  python .tools/auto-query-web/gpt.py --login     # 브라우저가 열리면 직접 로그인 후 터미널에서 Enter
  python .tools/auto-query-web/gpt.py             # 서버 기동 (기본 http://127.0.0.1:8791)

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
import re
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

def _env_web(key: str, fallback_gpt_key: str, default: str) -> str:
    """공통 WEB_QUERY_* 환경 변수를 우선 조회하고, 기존 GPT_* 변수를 하위 호환으로 지원."""
    return os.getenv(key) or os.getenv(fallback_gpt_key) or default

HOST = _env_web("WEB_QUERY_HOST", "GPT_CRAWLER_HOST", "127.0.0.1")
PORT = int(os.getenv("GPT_CRAWLER_PORT", "8791"))
# 헤드리스는 봇 탐지에 걸리기 쉬워 기본은 창 표시(0)
HEADLESS = _env_web("WEB_QUERY_HEADLESS", "GPT_HEADLESS", "0").strip().lower() in ("1", "true", "yes")
# 응답 대기 최대 시간(초)
RESPONSE_TIMEOUT = float(_env_web("WEB_QUERY_TIMEOUT", "GPT_RESPONSE_TIMEOUT", "180"))
# 웹 질의 공통 요청 간 최소 휴식 시간(초) — 사용량 제한/봇 탐지 완화용 보정 knob
MIN_INTERVAL = float(_env_web("WEB_QUERY_MIN_INTERVAL", "GPT_MIN_INTERVAL", "3"))
# 연속 실패 허용 횟수 — 초과하면 계정 보호를 위해 서버 재시작 전까지 요청 거부(423)
MAX_FAILS = int(_env_web("WEB_QUERY_MAX_FAILS", "GPT_MAX_FAILS", "3"))
CHAT_URL = os.getenv("GPT_CHAT_URL", "https://chatgpt.com/?temporary-chat=true")

# ChatGPT DOM 셀렉터 — UI 변경 시 여기만 수정
SEL_INPUT = '#prompt-textarea, div[contenteditable="true"][role="textbox"]'
SEL_FILE = 'form input[type="file"], input[type="file"]'
SEL_ATTACH_BTN = (
    'button[aria-label*="첨부"], button[aria-label*="추가"], button[aria-label*="Attach"], '
    'button[data-testid*="attach"], button[data-testid*="upload"]'
)
SEL_THUMBNAIL = (
    '[data-testid="file-thumbnail"], '
    '[data-testid*="file-thumbnail"], '
    '[data-testid*="attachment"], '
    '[data-testid*="upload"], '
    '[aria-label*="제거"], '
    '[aria-label*="Remove"], '
    '[aria-label*="attachment"], '
    'div[class*="thumbnail"], '
    'img[alt*="Uploaded"]'
)
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
        self.profile_dir = PROFILE_DIR  # 서브클래스(google 등)가 별도 프로필로 교체 가능
        self._pw = None
        self._ctx = None
        self._last = 0.0
        self.fails = 0  # 연속 실패 횟수 (성공 시 0)

    def start(self) -> None:
        """브라우저를 (이미 떠 있지 않으면) 기동. 환경 미비 시 자동 보정."""
        if self._ctx is not None:
            return
        ensure_installed()
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:
            # 브라우저 실행 파일 누락 등의 오류 발생 시 자동 복구(재설치) 1회 시도
            if "executable doesn't exist" in str(exc).lower() or "playwright install" in str(exc).lower():
                print("[auto-query-web] 브라우저 바이너리 누락 감지, 자동 다운로드를 진행합니다...", flush=True)
                install()
                self._ctx = self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir),
                    headless=self.headless,
                    viewport={"width": 1280, "height": 900},
                    args=["--disable-blink-features=AutomationControlled"],
                )
            else:
                raise

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

    @staticmethod
    def _attach_via_chooser(page, path: Path, image: bytes) -> None:
        """'+' 버튼 → 업로드 메뉴 항목 클릭으로 실제 file chooser 를 열어 파일 지정 (가장 사용자 동작에 가까움)."""
        btn = page.locator(SEL_ATTACH_BTN)
        if btn.count() == 0:
            btn = page.locator("form button")  # 라벨 불명 시 composer 첫 버튼('+')
        btn.first.click()
        item = page.get_by_text(re.compile(r"사진|파일|photos|files|upload", re.I)).first
        with page.expect_file_chooser(timeout=5_000) as fc:
            item.click()
        fc.value.set_files(str(path))

    @staticmethod
    def _attach_via_paste(page, path: Path, image: bytes) -> None:
        """클립보드 붙여넣기(Ctrl+V)와 동일한 ClipboardEvent 를 입력창에 발생시켜 이미지 주입."""
        page.locator(SEL_INPUT).click()
        page.evaluate(
            """(b64) => {
                const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
                const dt = new DataTransfer();
                dt.items.add(new File([bin], 'image.png', {type: 'image/png'}));
                const el = document.querySelector('#prompt-textarea') || document.activeElement;
                el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
            }""",
            base64.b64encode(image).decode(),
        )

    @staticmethod
    def _attach_via_input(page, path: Path, image: bytes) -> None:
        """숨은 input[type=file] 에 직접 파일 지정 (마지막 수단)."""
        page.locator(SEL_FILE).last.set_input_files(str(path))

    @staticmethod
    def _wait_attached(page, timeout_ms: int) -> bool:
        """첨부 감지: 썸네일 셀렉터 / composer 내 이미지 / 빈 텍스트에서 전송 버튼 활성화 중 하나라도 충족하면 True."""
        end = time.time() + timeout_ms / 1000
        while time.time() < end:
            try:
                if page.locator(SEL_THUMBNAIL).count() > 0:
                    return True
                if page.evaluate(
                    """() => { const t = document.querySelector('#prompt-textarea');
                        const f = t && t.closest('form'); return !!(f && f.querySelector('img, canvas')); }"""
                ):
                    return True
                # 첨부만으로도 전송 버튼이 활성화됨(텍스트 없는 상태에서)
                if page.locator(SEL_SEND).count() > 0 and not page.locator(SEL_INPUT).inner_text().strip():
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

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
                    print(f"[auto-query-web] 브라우저 연결 끊김 감지 ({nav_err}), 컨텍스트 재시작 중...", flush=True)
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

            # 이미지 임시 저장 (OS 네이티브 파일 경로 전달로 MIME/크기 정보 완전 보장)
            DEBUG_DIR.mkdir(exist_ok=True)
            upload_path = DEBUG_DIR / f"_upload_{os.getpid()}.png"
            upload_path.write_bytes(image)

            # 1. 이미지 먼저 첨부 — 실제 사용자 동작에 가까운 순서로 여러 방법을 시도, 첨부 감지 시 중단
            attached = False
            for method in (self._attach_via_chooser, self._attach_via_paste, self._attach_via_input):
                try:
                    method(page, upload_path, image)
                except Exception as exc:
                    print(f"[auto-query-web] 첨부 시도 실패({method.__name__}): {exc}", flush=True)
                    continue
                if self._wait_attached(page, 8_000):
                    print(f"[auto-query-web] 이미지 첨부 확인 ({method.__name__})", flush=True)
                    attached = True
                    break

            # 첨부가 확인되지 않으면 절대 전송하지 않고 즉시 오류로 중단 (헛질의 방지)
            if not attached:
                shot = DEBUG_DIR / f"missing_thumb_{int(time.time())}.png"
                try:
                    page.screenshot(path=str(shot))
                except Exception:
                    pass
                # 계정 파일 첨부 한도 도달 안내 감지 — 재시도 무의미하므로 423(배치 즉시 중단)로 차단
                try:
                    body = page.inner_text("body")
                except Exception:
                    body = ""
                if re.search(r"파일 첨부 한도|file upload limit|upload limit", body, re.I):
                    m = re.search(r"(\d+월 \d+일[^\n]*?다시 시도)", body)
                    raise CrawlerError(
                        f"ChatGPT 파일 첨부 한도 도달 — {m.group(1) if m else '한도 해제 시각 이후'} 재시도 필요 [shot: {shot.name}]",
                        423,
                    )
                raise CrawlerError(f"이미지 파일 첨부 실패 (모든 방법 실패) [shot: {shot.name}]", 502)

            # 2. 첨부 확인 후 텍스트 붙여넣기 — fill 은 입력창 DOM(썸네일 포함)을 비우므로 insert_text 사용
            time.sleep(1.0)  # 업로드 처리 안정화
            page.locator(SEL_INPUT).click()
            page.keyboard.insert_text(prompt)
            time.sleep(0.5)

            # 붙여넣기 후에도 첨부가 살아있는지 최종 확인 (사라졌다면 전송 차단)
            if not self._wait_attached(page, 3_000):
                raise CrawlerError("텍스트 입력 후 이미지 첨부가 사라짐 — 전송 중단", 502)

            # 4. 업로드 완료 및 활성화된 전송 버튼 대기
            page.wait_for_selector(SEL_SEND, state="visible", timeout=60_000)
            time.sleep(1.0)
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
                    DEBUG_DIR.mkdir(exist_ok=True)
                    shot = DEBUG_DIR / f"missing_img_{int(time.time())}.png"
                    try:
                        page.screenshot(path=str(shot))
                    except Exception:
                        pass
                    raise CrawlerError(
                        f"이미지 미첨부 인식 오류 (ChatGPT 응답: '{phrase}' 감지됨 / 실제응답: {text[:60]!r}) [shot: {shot.name}]",
                        502,
                    )

            print(f"\n[auto-query-web] [성공] 응답 텍스트 ({len(text)}자 수신):\n{text}\n" + "-" * 60, flush=True)
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
        """헬스체크 및 리셋."""
        if self.path == "/health":
            self._send(200, {"ok": True, "fails": self.driver.fails})
        elif self.path == "/reset":
            self.driver.fails = 0
            print("[auto-query-web] 실패 카운터 리셋 완료 (fails=0)", flush=True)
            self._send(200, {"ok": True, "message": "실패 카운터가 리셋되었습니다.", "fails": 0})
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
            print(f"[auto-query-web] [요청 실패 HTTP {exc.status}] {exc}", flush=True)
            self._send(exc.status, {"error": str(exc)})
        except Exception as exc:  # 브라우저 크래시 등 — 다음 요청에서 재기동하도록 정리
            try:
                self.driver.stop()
            except Exception:
                pass
            print(f"[auto-query-web] [서버 예외 발생 HTTP 500] {type(exc).__name__}: {exc}", flush=True)
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt: str, *args) -> None:
        """기본 stderr 로그를 간결하게."""
        print(f"[auto-query-web] {fmt % args}", flush=True)


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


def is_playwright_installed() -> bool:
    """playwright 패키지가 현재 Python 환경에 설치되어 있는지 확인."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def is_chromium_installed() -> bool:
    """BROWSERS_DIR 에 Chromium 브라우저 바이너리가 설치되어 있는지 확인."""
    if not BROWSERS_DIR.is_dir():
        return False
    # Windows: chrome-win64/chrome.exe 등, Linux: chrome-linux/chrome 등
    for pattern in ("chrome.exe", "chrome", "headless_shell.exe", "headless_shell"):
        if any(BROWSERS_DIR.rglob(pattern)):
            return True
    return False


def ensure_installed(quiet: bool = False) -> None:
    """playwright 패키지와 Chromium 브라우저 존재 여부를 점검하고 누락 시 자동 설치."""
    if not is_playwright_installed():
        if not quiet:
            print("[auto-query-web] playwright 패키지 미설치 감지. 자동 설치를 시작합니다...", flush=True)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright>=1.40"])
        if not quiet:
            print("[auto-query-web] playwright 패키지 설치 완료.", flush=True)

    if not is_chromium_installed():
        if not quiet:
            print(f"[auto-query-web] Chromium 브라우저 미설치 감지 ({BROWSERS_DIR}). 자동 다운로드 중...", flush=True)
        BROWSERS_DIR.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"], env=env)
        if not quiet:
            print(f"[auto-query-web] Chromium 브라우저 설치 완료: {BROWSERS_DIR}", flush=True)


def install() -> None:
    """playwright 패키지와 Chromium(.tools/auto-query-web/browsers)을 명시적 재설치."""
    subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright>=1.40"])
    BROWSERS_DIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"], env=env)
    print(f"[auto-query-web] 설치 완료: {BROWSERS_DIR}")


def _reexec_in_venv() -> None:
    """프로젝트 루트에 .venv 가 존재하고 현재 실행기가 가상환경이 아닌 경우 .venv 로 자동 전환."""
    if sys.prefix != sys.base_prefix:
        return  # 이미 가상환경 내부에서 실행 중
    if os.environ.get("_GPT_AQ_REEXEC") == "1":
        return  # 재귀 루프 방지

    root = HERE.parent.parent
    venv_py = root / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python")
    if venv_py.is_file():
        env = dict(os.environ)
        env["_GPT_AQ_REEXEC"] = "1"
        try:
            sys.exit(subprocess.call([str(venv_py), *sys.argv], env=env))
        except Exception:
            pass  # 실패 시 현재 파이썬으로 계속 진행


def main() -> None:
    """CLI 진입점."""
    _reexec_in_venv()

    ap = argparse.ArgumentParser(description="ChatGPT 웹 자동 질의 크롤러 서버")
    ap.add_argument("--install", action="store_true", help="playwright + Chromium 설치")
    ap.add_argument("--login", action="store_true", help="수동 로그인(세션 저장)")
    args = ap.parse_args()

    if args.install:
        return install()

    # 로그인 또는 서버 실행 전 필수 의존성 사전 점검/자동 보정
    ensure_installed()

    driver = GptDriver(headless=False if args.login else HEADLESS)
    if args.login:
        try:
            return driver.login_interactive()
        finally:
            driver.stop()

    # 서버 시작 모드인 경우 세션 존재 여부 힌트 안내
    has_session = PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir())
    if not has_session:
        print("[auto-query-web] [안내] 로그인 세션이 아직 설정되지 않았습니다. 최초 1회 'python .tools/auto-query-web/gpt.py --login' 실행을 권장합니다.", flush=True)

    srv = serve(driver)
    print(f"[auto-query-web] listening on http://{HOST}:{PORT} (headless={HEADLESS})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        driver.stop()


if __name__ == "__main__":
    main()
