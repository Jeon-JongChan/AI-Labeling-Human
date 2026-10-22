"""
Google AI 모드 웹 크롤러(auto-query-web/google.py) 연동 어댑터 모듈.
"""

from __future__ import annotations

from config import GOOGLE_CRAWLER_URL
from .gpt import GptCrawlerAdapter


class GoogleCrawlerAdapter(GptCrawlerAdapter):
    """gpt 어댑터와 동일한 /caption HTTP 규약을 쓰므로 주소·식별명·기동 안내만 교체."""

    SERVER_CMD = ".tools/auto-query-web/google.py"

    def __init__(self, crawler_url: str | None = None):
        """크롤러 주소 기본값을 config.GOOGLE_CRAWLER_URL 로 지정."""
        super().__init__(crawler_url or GOOGLE_CRAWLER_URL)

    @property
    def name(self) -> str:
        """어댑터 식별 명칭."""
        return "google"
