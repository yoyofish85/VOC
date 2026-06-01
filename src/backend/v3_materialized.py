# -*- coding: utf-8 -*-
"""v3_label_meta 物化列：v3_l1/l2/l3/confidence/match_type，供索引筛选与报表聚合。"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("voc.v3_materialized")

V3_MATERIALIZED_COLS = (
    ("v3_l1", "TEXT"),
    ("v3_l2", "TEXT"),
    ("v3_l3", "TEXT"),
    ("v3_confidence", "REAL"),
    ("v3_match_type", "TEXT"),
)

# 复核列表筛选：LLM / 规则路径 SQL 片段（基于物化列，可走索引）
V3_MATCH_LLM_WHERE = """(
    v3_match_type LIKE 'llm%' OR v3_match_type LIKE 'qwen%'
    OR v3_match_type IN ('llm_l3', 'llm_l2')
)"""

V3_MATCH_RULE_WHERE = """(
    TRIM(COALESCE(v3_match_type, '')) != ''
    AND v3_match_type NOT LIKE 'llm%'
    AND v3_match_type NOT LIKE 'qwen%'
    AND v3_match_type != 'clean_l1'
)"""


def _safe_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def materialized_from_meta(meta: Any) -> Dict[str, Any]:
    """从 v3 meta dict 或 JSON 字符串提取物化字段。"""
    obj: Dict[str, Any] = {}
    if isinstance(meta, dict):
        obj = meta
    elif meta is not None:
        raw = str(meta).strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    obj = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                obj = {}

    try:
        from taxonomy_normalize import canonicalize_l1_label

        l1_raw = str(obj.get("l1") or "").strip()
        l1 = canonicalize_l1_label(l1_raw) if l1_raw else ""
    except Exception:
        l1 = str(obj.get("l1") or "").strip()

    conf = _safe_float(obj.get("confidence"))
    return {
        "v3_l1": l1,
        "v3_l2": str(obj.get("l2") or "").strip(),
        "v3_l3": str(obj.get("l3") or "").strip(),
        "v3_confidence": conf if conf is not None else 0.0,
        "v3_match_type": str(obj.get("match_type") or "").strip(),
    }


def materialized_update_params(meta: Any) -> Tuple[str, str, str, float, str]:
    m = materialized_from_meta(meta)
    return (
        m["v3_l1"],
        m["v3_l2"],
        m["v3_l3"],
        float(m["v3_confidence"] or 0.0),
        m["v3_match_type"],
    )


def effective_confidence_sql() -> str:
    """列表/概览低置信筛选：优先物化列，回退 match_score。"""
    return "COALESCE(v3_confidence, match_score, 0)"


def ensure_v3_materialized_schema(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    c.execute("PRAGMA table_info(opinion)")
    cols = {r[1] for r in c.fetchall()}
    for name, typ in V3_MATERIALIZED_COLS:
        if name not in cols:
            c.execute(f"ALTER TABLE opinion ADD COLUMN {name} {typ}")
            logger.info("已新增物化列 %s", name)
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_opinion_v3_l1 ON opinion(v3_l1)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_v3_l2 ON opinion(v3_l2)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_v3_match_type ON opinion(v3_match_type)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_batch_v3l1 ON opinion(upload_batch, v3_l1)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_batch_rs_v3conf ON opinion(upload_batch, review_status, v3_confidence)",
        "CREATE INDEX IF NOT EXISTS idx_opinion_rs_v3conf ON opinion(review_status, v3_confidence)",
    ):
        try:
            c.execute(idx_sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()


def backfill_v3_materialized(
    db_path: str,
    *,
    batch_size: int = 500,
    opinion_ids: Optional[List[str]] = None,
) -> int:
    """从 v3_label_meta 回填物化列；返回更新条数。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_v3_materialized_schema(conn)
    c = conn.cursor()
    updated = 0
    while True:
        if opinion_ids:
            if not opinion_ids:
                break
            chunk = opinion_ids[:batch_size]
            opinion_ids = opinion_ids[batch_size:]
            ph = ",".join(["?"] * len(chunk))
            rows = c.execute(
                f"""SELECT id, v3_label_meta FROM opinion
                    WHERE opinion_id IN ({ph})
                    AND v3_label_meta IS NOT NULL AND TRIM(v3_label_meta) != ''""",
                chunk,
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT id, v3_label_meta FROM opinion
                   WHERE v3_label_meta IS NOT NULL AND TRIM(v3_label_meta) != ''
                   AND (v3_l1 IS NULL OR TRIM(v3_l1) = '')
                   LIMIT ?""",
                (batch_size,),
            ).fetchall()
        if not rows:
            break
        for r in rows:
            v3_l1, v3_l2, v3_l3, v3_conf, v3_mt = materialized_update_params(r["v3_label_meta"])
            c.execute(
                """UPDATE opinion SET v3_l1=?, v3_l2=?, v3_l3=?, v3_confidence=?, v3_match_type=?
                   WHERE id=?""",
                (v3_l1, v3_l2, v3_l3, v3_conf, v3_mt, r["id"]),
            )
            updated += 1
        conn.commit()
        if opinion_ids is None and len(rows) < batch_size:
            break
    conn.close()
    if updated:
        logger.info("v3 物化列回填完成: %d 条", updated)
    return updated
