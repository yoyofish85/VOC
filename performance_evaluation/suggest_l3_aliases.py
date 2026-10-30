# -*- coding: utf-8 -*-
"""通用消化辅助：对「通用/其他-待归类」样本建议候选定位 L3（宽松匹配），供人工补别名。

在服务器跑：
  python3 performance_evaluation/suggest_l3_aliases.py --db src/backend/opinion_review.db --limit 200
输出建议清单（l1/l2/原文/候选 L3 及得分），人工确认后补进 l3_alias_v1.json。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "label_project") not in sys.path:
    sys.path.insert(0, str(ROOT / "label_project"))

from l3_standard_resolver import suggest_l3_candidates  # noqa: E402

PENDING = {"通用", "其他-待归类"}


def suggest(rows: List[Dict[str, Any]], top_n: int = 3) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in rows:
        l1 = (r.get("v3_l1") or "").strip()
        l2 = (r.get("v3_l2") or "").strip()
        l3 = (r.get("v3_l3") or "").strip()
        text = (r.get("original_text") or "").strip()
        if not (l1 and l2 and l3 in PENDING and text):
            continue
        cands = suggest_l3_candidates(text, l1, l2, top_n=top_n)
        if cands:
            out.append({
                "l1": l1, "l2": l2, "text": text[:120],
                "suggestions": [{"l3": c, "score": sc} for c, sc in cands],
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="通用消化：建议候选 L3 别名（只读）")
    ap.add_argument("--db", default=str(ROOT / "src/backend/opinion_review.db"))
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--top-n", type=int, default=3)
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    rows = [dict(r) for r in conn.execute(
        "SELECT v3_l1, v3_l2, v3_l3, original_text FROM opinion "
        "WHERE TRIM(IFNULL(v3_l3,'')) IN ('通用','其他-待归类') LIMIT ?",
        (int(args.limit),),
    )]
    conn.close()
    result = suggest(rows, top_n=args.top_n)
    print(json.dumps({"sample": len(result), "suggestions": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
