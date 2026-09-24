# -*- coding: utf-8 -*-
"""S8：opinion 向量独立存储（不改 opinion 分类字段）。

默认库路径：与复核库同目录的 opinion_embedding.db；
也可传入同一 opinion_review.db（仅建新表）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS opinion_embedding (
  opinion_id TEXT PRIMARY KEY,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vector_json TEXT NOT NULL,
  text_md5 TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_opinion_embedding_model
  ON opinion_embedding(model);
"""


def default_embedding_db(opinion_db: Path) -> Path:
    return Path(opinion_db).resolve().parent / "opinion_embedding.db"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_vectors(
    conn: sqlite3.Connection,
    rows: Sequence[Dict[str, Any]],
) -> int:
    """rows: opinion_id, model, vector (list[float]), text_md5?"""
    n = 0
    for r in rows:
        vec = list(r["vector"])
        conn.execute(
            """
            INSERT INTO opinion_embedding(opinion_id, model, dim, vector_json, text_md5, updated_at)
            VALUES(?,?,?,?,?,datetime('now'))
            ON CONFLICT(opinion_id) DO UPDATE SET
              model=excluded.model,
              dim=excluded.dim,
              vector_json=excluded.vector_json,
              text_md5=excluded.text_md5,
              updated_at=datetime('now')
            """,
            [
                str(r["opinion_id"]),
                str(r["model"]),
                len(vec),
                json.dumps(vec, ensure_ascii=False),
                r.get("text_md5") or "",
            ],
        )
        n += 1
    conn.commit()
    return n


def load_vectors(
    conn: sqlite3.Connection,
    *,
    model: Optional[str] = None,
    opinion_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT opinion_id, model, dim, vector_json, text_md5, updated_at FROM opinion_embedding WHERE 1=1"
    params: List[Any] = []
    if model:
        sql += " AND model = ?"
        params.append(model)
    if opinion_ids:
        placeholders = ",".join("?" * len(opinion_ids))
        sql += f" AND opinion_id IN ({placeholders})"
        params.extend(str(x) for x in opinion_ids)
    out: List[Dict[str, Any]] = []
    for row in conn.execute(sql, params):
        out.append(
            {
                "opinion_id": row["opinion_id"],
                "model": row["model"],
                "dim": int(row["dim"]),
                "vector": json.loads(row["vector_json"]),
                "text_md5": row["text_md5"],
                "updated_at": row["updated_at"],
            }
        )
    return out


def count_vectors(conn: sqlite3.Connection, model: Optional[str] = None) -> int:
    if model:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM opinion_embedding WHERE model=?", [model]
            ).fetchone()[0]
        )
    return int(conn.execute("SELECT COUNT(*) FROM opinion_embedding").fetchone()[0])
