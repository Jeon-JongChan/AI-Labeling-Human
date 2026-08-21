"""
라벨링 데이터 저장 — SQLite (data/labeling/labeler.sqlite3).

docs  : 등록된 PDF (data/docs 기준 상대 경로)
items : 추출된 요소 + 라벨 (pending / approved / rejected) + 수정 텍스트
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import DB_PATH, DOCS_DIR

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path TEXT UNIQUE NOT NULL,
    file_name TEXT NOT NULL,
    extracted_at TEXT
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    order_idx INTEGER NOT NULL,
    page INTEGER,
    element_type TEXT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    ocr_text TEXT NOT NULL DEFAULT '',
    edited_text TEXT,
    image_path TEXT,
    bbox TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT,
    FOREIGN KEY (doc_id) REFERENCES docs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_items_doc ON items(doc_id, order_idx);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LabelStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- docs ---------------------------------------------------------------

    def sync_docs(self, pdf_paths: List[Path]) -> None:
        """data/docs 스캔 결과를 등록 (기존 항목 유지, 새 파일 추가)."""
        for path in pdf_paths:
            rel = path.resolve().relative_to(DOCS_DIR.resolve()).as_posix()
            self._conn.execute(
                "INSERT OR IGNORE INTO docs (rel_path, file_name) VALUES (?, ?)",
                (rel, path.name),
            )
        self._conn.commit()

    def list_docs(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT d.id, d.rel_path, d.file_name, d.extracted_at,
                   COUNT(i.id) AS total,
                   SUM(CASE WHEN i.status='approved' THEN 1 ELSE 0 END) AS approved,
                   SUM(CASE WHEN i.status='rejected' THEN 1 ELSE 0 END) AS rejected,
                   SUM(CASE WHEN i.status='pending' THEN 1 ELSE 0 END) AS pending
            FROM docs d LEFT JOIN items i ON i.doc_id = d.id
            GROUP BY d.id
            ORDER BY d.rel_path
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def get_doc(self, doc_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT id, rel_path, file_name, extracted_at FROM docs WHERE id = ?",
            (doc_id,),
        ).fetchone()
        return dict(row) if row else None

    # -- items --------------------------------------------------------------

    def replace_items(self, doc_id: int, items: List[Dict[str, Any]]) -> int:
        """추출 결과로 교체 (기존 라벨 삭제 주의)."""
        cur = self._conn.cursor()
        cur.execute("DELETE FROM items WHERE doc_id = ?", (doc_id,))
        cur.executemany(
            """
            INSERT INTO items
              (doc_id, order_idx, page, element_type, kind,
               text, ocr_text, image_path, bbox, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            [
                (
                    doc_id,
                    it["order_idx"],
                    it["page"],
                    it["element_type"],
                    it["kind"],
                    it["text"],
                    it["ocr_text"],
                    it["image_path"],
                    it["bbox"],
                    _now(),
                )
                for it in items
            ],
        )
        cur.execute(
            "UPDATE docs SET extracted_at = ? WHERE id = ?", (_now(), doc_id)
        )
        self._conn.commit()
        return len(items)

    def list_items(self, doc_id: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT id, doc_id, order_idx, page, element_type, kind,
                   text, ocr_text, edited_text, image_path, bbox,
                   status, updated_at
            FROM items WHERE doc_id = ? ORDER BY order_idx
            """,
            (doc_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_item(self, item_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            """
            SELECT id, doc_id, order_idx, page, element_type, kind,
                   text, ocr_text, edited_text, image_path, bbox,
                   status, updated_at
            FROM items WHERE id = ?
            """,
            (item_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_item(
        self,
        item_id: int,
        *,
        status: Optional[str] = None,
        edited_text: Optional[str] = None,
    ) -> bool:
        sets: List[str] = ["updated_at = ?"]
        args: List[Any] = [_now()]
        if status is not None:
            sets.append("status = ?")
            args.append(status)
        if edited_text is not None:
            sets.append("edited_text = ?")
            args.append(edited_text)
        args.append(item_id)
        cur = self._conn.execute(
            f"UPDATE items SET {', '.join(sets)} WHERE id = ?", args
        )
        self._conn.commit()
        return cur.rowcount > 0
