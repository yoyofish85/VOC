#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S5 dry-run：对「通用」样本用定位 L3 标准层重放（只读，不写库）。

用法：
  VOC_L3_STANDARD_V1=1 python3 performance_evaluation/dry_run_l3_standard.py
  python3 performance_evaluation/dry_run_l3_standard.py --db src/backend/opinion_review.db --limit 400
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from label_project.l3_standard_resolver import (  # noqa: E402
    PENDING_L3,
    resolve_l3_with_standard,
)

EXPORTS = Path(__file__).resolve().parent / "exports"
DEFAULT_DB = ROOT / "src" / "backend" / "opinion_review.db"
FOCUS_L2 = ("销售服务问题", "售后服务问题", "座舱问题", "APP问题")


def _fetch_generic_rows(db: Path, limit: int, since_days: int) -> List[Dict[str, Any]]:
    uri = f"file:{db.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    sql = """
      SELECT opinion_id, original_text, v3_l1, v3_l2, v3_l3, v3_match_type,
             review_l1, review_l2, review_status, upload_batch
      FROM opinion
      WHERE TRIM(IFNULL(v3_l3,'')) = '通用'
        AND TRIM(IFNULL(v3_l1,'')) IN ('产品质量类','服务类')
    """
    params: List[Any] = []
    if since_days > 0:
        sql += " AND COALESCE(reviewed_at, create_time) >= datetime('now', ?)"
        params.append(f"-{int(since_days)} days")
    sql += " ORDER BY COALESCE(reviewed_at, create_time) DESC LIMIT ?"
    params.append(int(limit))
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def run_dry_run(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out_rows: List[Dict[str, Any]] = []
    mt = Counter()
    l3_new = Counter()
    l1_changed = l2_changed = 0
    still_generic = 0
    pending = 0
    for r in rows:
        l1 = (r.get("v3_l1") or "").strip()
        l2 = (r.get("v3_l2") or "").strip()
        text = r.get("original_text") or ""
        resolved = resolve_l3_with_standard(text, l1, l2, enabled=True)
        if not resolved:
            # 非标准层覆盖的 L2：保持原样记 unchanged
            new_l3 = r.get("v3_l3") or "通用"
            match_type = "unchanged_no_standard"
            status = "unchanged"
        else:
            new_l3 = resolved["level3"]
            match_type = resolved["match_type"]
            if new_l3 == "通用":
                status = "unchanged"
            elif new_l3 == PENDING_L3:
                status = "pending"
            else:
                status = "fixed"
        if resolved and (resolved.get("level1") != l1):
            l1_changed += 1
        if resolved and (resolved.get("level2") != l2):
            l2_changed += 1
        if new_l3 == "通用":
            still_generic += 1
        if new_l3 == PENDING_L3:
            pending += 1
        mt[match_type] += 1
        l3_new[new_l3] += 1
        out_rows.append(
            {
                "opinion_id": r.get("opinion_id"),
                "upload_batch": r.get("upload_batch"),
                "v3_l1": l1,
                "v3_l2": l2,
                "v3_l3_before": r.get("v3_l3"),
                "v3_l3_after": new_l3,
                "match_type": match_type,
                "status": status,
                "original_text": (text or "")[:240],
            }
        )

    n = max(len(rows), 1)
    before_generic_rate = 1.0  # 输入全是通用
    after_generic_rate = still_generic / n
    return {
        "sample_size": len(rows),
        "before_generic_rate": before_generic_rate,
        "after_generic_rate": round(after_generic_rate, 4),
        "after_pending_rate": round(pending / n, 4),
        "after_concrete_l3_rate": round(1.0 - after_generic_rate - pending / n, 4),
        "l1_changed": l1_changed,
        "l2_changed": l2_changed,
        "match_type_counts": dict(mt),
        "top_new_l3": l3_new.most_common(20),
        "focus_l2_coverage": {
            l2: sum(1 for r in rows if (r.get("v3_l2") or "") == l2) for l2 in FOCUS_L2
        },
        "rows": out_rows,
        "gate_ok": after_generic_rate <= 0.12 and l1_changed == 0 and l2_changed == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="L3 标准层「通用」dry-run（只读）")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--since-days", type=int, default=0, help="0=不限时间")
    parser.add_argument("--out-csv", type=Path, default=None)
    parser.add_argument("--out-json", type=Path, default=None)
    args = parser.parse_args()

    if not args.db.is_file():
        print(f"[错误] 数据库不存在: {args.db}", file=sys.stderr)
        return 2

    rows = _fetch_generic_rows(args.db, args.limit, args.since_days)
    report = run_dry_run(rows)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_csv = args.out_csv or (EXPORTS / f"l3_standard_dryrun_{stamp}.csv")
    out_json = args.out_json or (EXPORTS / f"l3_standard_dryrun_{stamp}.json")
    EXPORTS.mkdir(parents=True, exist_ok=True)

    detail = report.pop("rows")
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(detail[0].keys()) if detail else ["opinion_id"])
        w.writeheader()
        w.writerows(detail)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"[已导出 CSV] {out_csv}")
    print(f"[已导出 JSON] {out_json}")
    print(f"[门禁] gate_ok={report['gate_ok']}（目标：通用占比≤12% 且 L1/L2 不变；其他-待归类单独统计）")
    return 0 if report["gate_ok"] or report["sample_size"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
