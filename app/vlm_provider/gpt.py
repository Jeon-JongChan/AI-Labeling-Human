"""
ChatGPT 웹 크롤러(auto-query-web) 연동 어댑터 모듈.
"""

from __future__ import annotations

import atexit
import subprocess
import sys
import time
from pathlib import Path
import requests

from config import GPT_CRAWLER_URL, TOOL_DIR, VLM_TIMEOUT
from .base import BaseVlmAdapter, VlmAbortError, VlmError


# 라벨러 프로세스 종료 시 자동 기동된 크롤러 및 브라우저를 자동 종료하기 위한 프로세스 목록
_spawned_crawler_processes: list[subprocess.Popen] = []


def _cleanup_spawned_crawlers() -> None:
    """라벨러 종료(atexit) 시 백그라운드로 자동 기동한 크롤러 프로세스 트리 완전 회수."""
    for proc in _spawned_crawler_processes:
        try:
            if proc.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    proc.terminate()
                    proc.wait(timeout=2)
        except Exception:
            pass


atexit.register(_cleanup_spawned_crawlers)


class GptCrawlerAdapter(BaseVlmAdapter):
    """Playwright 기반 ChatGPT 웹 자동 질의 크롤러 어댑터."""

    SERVER_CMD = ".tools/auto-query-web/gpt.py"  # 서버 기동 안내용 (서브클래스에서 교체)

    def __init__(self, crawler_url: str | None = None):
        """
        크롤러 어댑터 초기화.
        
        Args:
            crawler_url: 크롤러 서버 주소 (기본값: config.GPT_CRAWLER_URL)
        """
        self.crawler_url = (crawler_url or GPT_CRAWLER_URL).rstrip("/")

    @property
    def name(self) -> str:
        """어댑터 식별 명칭."""
        return "gpt"

    @classmethod
    def _is_server_alive(cls, url: str) -> bool:
        """크롤러 서버의 /health 엔드포인트 응답 여부 확인."""
        try:
            r = requests.get(f"{url}/health", timeout=1.5)
            return r.status_code == 200
        except Exception:
            return False

    def _ensure_server_running(self) -> None:
        """크롤러 서버가 실행 중이 아니면 백그라운드 프로세스로 자동 기동."""
        if self._is_server_alive(self.crawler_url):
            return

        print(f"[vlm] {self.name} 크롤러 서버({self.crawler_url}) 미응답 - 백그라운드 자동 기동을 시작합니다...", flush=True)

        script_path = (TOOL_DIR / self.SERVER_CMD).resolve()
        if not script_path.is_file():
            script_path = (Path(__file__).resolve().parent.parent.parent / self.SERVER_CMD).resolve()

        if not script_path.is_file():
            raise VlmAbortError(f"{self.name} 크롤러 실행 스크립트를 찾을 수 없습니다: {script_path}")

        # 실행 환경 파이썬 인터프리터 (.venv 우선)
        py_exe = sys.executable
        venv_py = TOOL_DIR / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python")
        if venv_py.is_file():
            py_exe = str(venv_py)

        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)

        try:
            proc = subprocess.Popen(
                [py_exe, str(script_path)],
                cwd=str(TOOL_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            _spawned_crawler_processes.append(proc)
        except Exception as exc:
            raise VlmAbortError(f"{self.name} 크롤러 서버 자동 기동 실패: {exc}") from exc

        # 서버가 활성화될 때까지 최대 15초 동안 대기
        start_time = time.time()
        while time.time() - start_time < 15.0:
            time.sleep(1.0)
            if self._is_server_alive(self.crawler_url):
                print(f"[vlm] [성공] {self.name} 크롤러 서버 자동 기동 성공! ({self.crawler_url})", flush=True)
                return

        raise VlmAbortError(f"{self.name} 크롤러 서버 자동 기동 시간 초과 (15초). 수동 실행 필요: python {self.SERVER_CMD}")

    def _generate_caption(self, image_path: Path, prompt: str) -> str:
        """auto-query-web 크롤러 서버에 이미지 한 장을 전송하고 완료 대기."""
        self._ensure_server_running()
        b64 = self.encode_image_b64(image_path)
        try:
            # 크롤러는 응답 완료까지 HTTP 연결을 유지하므로 VLM_TIMEOUT 만큼 대기
            resp = requests.post(
                f"{self.crawler_url}/caption",
                json={"image_b64": b64, "prompt": prompt},
                timeout=VLM_TIMEOUT,
            )
        except requests.ConnectionError as exc:
            raise VlmAbortError(
                f"GPT 크롤러({self.crawler_url})에 연결할 수 없습니다. "
                f"python {self.SERVER_CMD} 기동 필요"
            ) from exc
        except requests.Timeout as exc:
            raise VlmError(f"GPT 크롤러 응답 시간 초과 ({VLM_TIMEOUT:.0f}초)") from exc

        try:
            data = resp.json()
        except ValueError:
            raise VlmError(f"GPT 크롤러 HTTP {resp.status_code}: {resp.text[:200]}")

        if resp.status_code != 200:
            # 401(로그인 만료)/423(차단)/429(요청 한도 초과)는 계정 보호를 위해 즉시 배치 중단 신호(VlmAbortError) 발생
            err_cls = VlmAbortError if resp.status_code in (401, 423, 429) else VlmError
            raise err_cls(f"크롤러 HTTP {resp.status_code}: {data.get('error', data)}")

        text = (data.get("text") or "").strip()
        if not text:
            raise VlmError("크롤러 응답 본문이 비어 있음")

        # 이미지가 제대로 전달되지 않아 모델이 되묻는 응답 방어
        lower = text.lower()
        if any(p in lower for p in ("이미지를 첨부", "이미지가 첨부", "이미지를 올려", "이미지가 보이지", "사진을 첨부", "please provide the image", "please attach the image")):
            raise VlmError(f"크롤러 이미지 미인식 오류 (미첨부 문구 감지): {text[:60]}...")

        # AI 요청 한도 도달 문구 감지 방어
        if any(p in text for p in ("요청 한도에 도달", "대답 요청 한도", "reached the current limit")):
            raise VlmAbortError(f"크롤러 AI 요청 한도 초과 오류: {text[:60]}...")

        return text

