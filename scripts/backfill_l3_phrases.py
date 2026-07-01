#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填旧数据的 L3 关键词短语。

仅处理 review_status=1 且 v3_label_meta 中缺少 l3_phrases 的行。
需要服务器已配置 VOC_USE_MLX=1 / VOC_MLX_MODEL / VOC_MLX_ADAPTER。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from qwen_ollama import _extract_l3_phrases, _mlx_model_load  # noqa: E402

DB_PATH = os.path.abspath(os.environ.get("VOC_DB_PATH", str(BACKEND_DIR / "opinion_review.db")))


def _load_meta(raw: str) -> dict:
    try:
        return json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return {}


def main() -> int:
    if not Path(DB_PATH).is_file():
        print(f"DB not found: {DB_PATH}")
        return 1
    if os.environ.get("VOC_USE_MLX") == "1" and not _mlx_model_load():
        print("MLX model load failed; aborting backfill")
        return 2

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """SELECT opinion_id, original_text, v3_label_meta FROM opinion
        WHERE review_status = 1
        AND original_text IS NOT NULL
        AND TRIM(original_text) != ''"""
    ).fetchall()
    done = 0
    skipped = 0
    for oid, text, meta_raw in rows:
        meta = _load_meta(meta_raw or "")
        if meta.get("l3_phrases"):
            skipped += 1
            continue
        phrases = _extract_l3_phrases(text or "")
        if not phrases:
            skipped += 1
            continue
        meta["l3_phrases"] = phrases
        conn.execute(
            "UPDATE opinion SET v3_label_meta = ? WHERE opinion_id = ?",
            [json.dumps(meta, ensure_ascii=False), oid],
        )
        done += 1
        if done % 100 == 0:
            conn.commit()
            print(f"{done} rows updated...")
    conn.commit()
    conn.close()
    print(f"Backfill complete: updated={done}, skipped={skipped}, db={DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
