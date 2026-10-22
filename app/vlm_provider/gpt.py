"""
ChatGPT 웹 크롤러(gpt-auto-query) 연동 어댑터 모듈.
"""

from __future__ import annotations

from pathlib import Path
import requests

from config import GPT_CRAWLER_URL, VLM_TIMEOUT
from .base import BaseVlmAdapter, VlmAbortError, VlmError


class GptCrawlerAdapter(BaseVlmAdapter):
    """Playwright 기반 ChatGPT 웹 자동 질의 크롤러 어댑터."""

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

    def _generate_caption(self, image_path: Path, prompt: str) -> str:
        """gpt-auto-query 크롤러 서버에 이미지 한 장을 전송하고 완료 대기."""
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
                "python .tools/gpt-auto-query/server.py 기동 필요"
            ) from exc
        except requests.Timeout as exc:
            raise VlmError(f"GPT 크롤러 응답 시간 초과 ({VLM_TIMEOUT:.0f}초)") from exc

        try:
            data = resp.json()
        except ValueError:
            raise VlmError(f"GPT 크롤러 HTTP {resp.status_code}: {resp.text[:200]}")

        if resp.status_code != 200:
            # 401(로그인 만료)/423(연속 실패 차단)은 계정 보호를 위해 즉시 배치 중단 신호(VlmAbortError) 발생
            err_cls = VlmAbortError if resp.status_code in (401, 423) else VlmError
            raise err_cls(f"GPT 크롤러 HTTP {resp.status_code}: {data.get('error', data)}")

        text = (data.get("text") or "").strip()
        if not text:
            raise VlmError("GPT 크롤러 응답 본문이 비어 있음")

        # 이미지가 제대로 전달되지 않아 ChatGPT가 되묻는 응답 방어
        lower = text.lower()
        if any(p in lower for p in ("이미지를 첨부", "이미지가 첨부", "이미지를 올려", "이미지가 보이지", "사진을 첨부", "please provide the image", "please attach the image")):
            raise VlmError(f"ChatGPT 이미지 미인식 오류 (미첨부 문구 감지): {text[:60]}...")

        return text

