# -*- coding: utf-8 -*-
"""T99「其他-待归类」归因（只读）。

统计哪些 L1/L2/L3 落进了 T99，区分：
  - 标签缺失/未消化：l3 为空 / 通用 / 其他-待归类
  - 旧 L3 未映射：l3 不在 214 标准层映射里（需补别名/映射）
口径与主题台账一致：已复核优先 review_l*，否则 v3_l*（含 meta 回退）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "label_project") not in sys.path:
    sys.path.insert(0, str(ROOT / "label_project"))

from theme_lookup import resolve_theme  # noqa: E402

MISSING_L3 = {"", "通用", "其他-待归类"}


def _first(*vals: Any) -> str:
    for v in vals:
        s = str(v or "").strip()
        if s:
            return s
    return ""


def effective_l123(row: Dict[str, Any]) -> tuple:
    """已复核优先 review，否则 v3（含 v3_label_meta 回退），再 model_class/keyword。"""
    l1 = _first(row.get("review_l1"), row.get("v3_l1"))
    l2 = _first(row.get("review_l2"), row.get("v3_l2"))
    l3 = _first(row.get("review_l3"), row.get("v3_l3"))
    if not (l1 and l2):
        meta = row.get("v3_label_meta")
        m: Dict[str, Any] = {}
        if isinstance(meta, str) and meta.strip():
            try:
                m = json.loads(meta)
            except Exception:
                m = {}
        elif isinstance(meta, dict):
            m = meta
        l1 = l1 or _first(m.get("l1"))
        l2 = l2 or _first(m.get("l2"))
        l3 = l3 or _first(m.get("l3"))
    if not l1:
        l1 = _first(row.get("model_class"))
    if not l2:
        l2 = _first(row.get("model_keyword"))
    return l1, l2, l3


def analyze(rows: Sequence[Dict[str, Any]], mapping: Optional[dict] = None) -> Dict[str, Any]:
    total = len(rows)
    empty_l3 = 0
    mapped = 0
    t99 = 0
    missing = 0
    unmapped = 0
    breakdown: Counter = Counter()
    for r in rows:
        l1, l2, l3 = effective_l123(r)
        if not l3:
            empty_l3 += 1
        tid, _name = resolve_theme(l1, l2, l3, mapping=mapping)
        if tid == "T99":
            t99 += 1
            breakdown[(l1 or "（空一级）", l2 or "（空二级）", l3 or "（空三级）")] += 1
            if l3 in MISSING_L3:
                missing += 1
            else:
                unmapped += 1
        else:
            mapped += 1
    top = [
        {"l1": k[0], "l2": k[1], "l3": k[2], "count": v}
        for k, v in breakdown.most_common()
    ]
    return {
        "total": total,
        "empty_l3": empty_l3,
        "mapped": mapped,
        "t99_count": t99,
        "t99_missing_label": missing,
        "t99_unmapped_l3": unmapped,
        "t99_top": top,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="T99「其他-待归类」归因（只读）")
    ap.add_argument("--db", default=str(ROOT / "src/backend/opinion_review.db"))
    ap.add_argument("--limit", type=int, default=0, help="0=全库")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    sql = (
        "SELECT opinion_id, review_l1, review_l2, review_l3, v3_l1, v3_l2, v3_l3, "
        "v3_label_meta, model_class, model_keyword FROM opinion "
        "WHERE TRIM(IFNULL(v3_l1,'')) != ''"
        + (f" LIMIT {int(args.limit)}" if args.limit else "")
    )
    rows = [dict(r) for r in conn.execute(sql)]
    conn.close()

    result = analyze(rows)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"总样本={result['total']}  空L3={result['empty_l3']}  "
            f"命中主题={result['mapped']}  T99={result['t99_count']} "
            f"(缺失/未消化={result['t99_missing_label']}，未映射={result['t99_unmapped_l3']})"
        )
        print("\nT99 Top L1/L2/L3：")
        for x in result["t99_top"][: args.top]:
            print(f"  {x['l1']} / {x['l2']} / {x['l3']}  →  {x['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
