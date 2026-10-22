"""
DOCS_DIR 내부 PDF 문서 자동 탐색 모듈.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from config import DOCS_DIR


def discover_pdfs() -> List[Path]:
    """data/docs 이하 PDF 재귀 탐색 (_rejected 제외)."""
    if not DOCS_DIR.is_dir():
        return []
    found: List[Path] = []
    for path in DOCS_DIR.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        if path.name.startswith("."):
            continue
        rel_parts = path.relative_to(DOCS_DIR).parts
        if any(part in ("_rejected",) for part in rel_parts[:-1]):
            continue
        found.append(path)
    return sorted(found)

