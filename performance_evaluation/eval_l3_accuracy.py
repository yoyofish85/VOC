# -*- coding: utf-8 -*-
"""L3 准确率评估（条件 Top-1，只读观察，不进 KPI）。

口径：在「人工 L1/L2 与模型一致」的已复核样本上，比较 人工 review_l3 vs 模型 v3_l3。
review_l3 由 L3-E 人工采集逐步积累；当前可能为 0，脚本仍可跑并输出 0 分母。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "label_project") not in sys.path:
    sys.path.insert(0, str(ROOT / "label_project"))
from taxonomy_normalize import NON_ISSUE_L1  # noqa: E402


def evaluate(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    reviewed_with_l3 = 0
    denom = 0
    top1 = 0
    denom_by_l2: Counter = Counter()
    hit_by_l2: Counter = Counter()
    for r in rows:
        rl1 = (r.get("review_l1") or "").strip()
        rl2 = (r.get("review_l2") or "").strip()
        rl3 = (r.get("review_l3") or "").strip()
        if rl1 and rl1 != NON_ISSUE_L1 and rl3:
            reviewed_with_l3 += 1
        if rl1 != NON_ISSUE_L1 and rl3 and rl1 == (r.get("v3_l1") or "").strip() and rl2 == (r.get("v3_l2") or "").strip():
            denom += 1
            denom_by_l2[rl2] += 1
            if rl3 == (r.get("v3_l3") or "").strip():
                top1 += 1
                hit_by_l2[rl2] += 1
    return {
        "reviewed_with_l3": reviewed_with_l3,
        "denominator": denom,
        "top1": top1,
        "top1_rate": round(top1 / denom, 4) if denom else None,
        "by_l2": [
            {"l2": k, "top1": hit_by_l2.get(k, 0), "denom": v,
             "rate": round(hit_by_l2.get(k, 0) / v, 4) if v else None}
            for k, v in denom_by_l2.most_common()
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="L3 准确率评估（只读，条件 Top-1）")
    ap.add_argument("--db", default=str(ROOT / "src/backend/opinion_review.db"))
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    rows = [dict(r) for r in conn.execute(
        "SELECT review_l1, review_l2, review_l3, v3_l1, v3_l2, v3_l3 FROM opinion "
        "WHERE review_status = 1 AND TRIM(IFNULL(review_l3,'')) != ''"
    )]
    conn.close()
    print(json.dumps(evaluate(rows), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
