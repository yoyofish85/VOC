#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤 1/2：导出「人工标注存疑」样本 CSV，供二次人工复核。

默认：优先导出充电/LFC/表扬边界，按优先级取 Top 200 条（试点规模）。

用法：
  python3 performance_evaluation/export_ambiguous_labels.py
  python3 performance_evaluation/export_ambiguous_labels.py --limit 200
  python3 performance_evaluation/export_ambiguous_labels.py --limit 500 --min-priority all

导出后请在 Excel 中填写：复核后一级、复核后二级、复核备注、复核人
完成后：python3 performance_evaluation/import_label_audit.py --csv <路径>
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

PERF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PERF_DIR))

from label_audit_common import (  # noqa: E402
    CSV_COLUMNS,
    DEFAULT_DB,
    ambiguity_priority_score,
    connect_ro,
    detect_ambiguity,
    fetch_reviewed_for_audit,
    model_labels,
    reason_text,
)

EXPORTS_DIR = PERF_DIR / "exports"
STATE_DIR = PERF_DIR / "state"

CHARGING_REASONS = frozenset(
    {
        "lfc_consult_vs_lfc",
        "lfc_lfc_vs_consult",
        "lfc_pure_consult",
        "lfc_issue_signal",
        "praise_vs_product",
        "sales_praise_boundary",
        "nonissue_l2_split",
    }
)


def row_to_csv_dict(row, reasons: List[str], priority: int) -> Dict[str, str]:
    m_l1, m_l2 = model_labels(row)
    return {
        "舆情编号": str(row["opinion_id"] or "").strip(),
        "原文": str(row["original_text"] or "").strip(),
        "创建时间": str(row["create_time"] or "").strip(),
        "批次": str(row["upload_batch"] or "").strip(),
        "当前人工一级": str(row["review_l1"] or "").strip(),
        "当前人工二级": str(row["review_l2"] or "").strip(),
        "模型一级": m_l1,
        "模型二级": m_l2,
        "优先级": str(priority),
        "存疑原因代码": ";".join(reasons),
        "复核说明": reason_text(reasons),
        "复核后一级": "",
        "复核后二级": "",
        "复核备注": "",
        "复核人": "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="导出人工标注存疑样本 CSV（默认 Top 200）")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 路径")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出 CSV 路径；默认 performance_evaluation/exports/ambiguous_YYYYMMDD_HHMM.csv",
    )
    parser.add_argument(
        "--min-priority",
        choices=("charging", "all"),
        default="charging",
        help="charging=充电/LFC/表扬边界（试点推荐）；all=含全部 Top 一级错误",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="最多导出条数，默认 200；0 表示不限制",
    )
    args = parser.parse_args()

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    out_path = args.out
    if out_path is None:
        out_path = EXPORTS_DIR / f"ambiguous_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    out_path = out_path.resolve()

    conn = connect_ro(args.db)
    try:
        rows = fetch_reviewed_for_audit(conn)
    finally:
        conn.close()

    candidates: List[Tuple[int, Any, List[str]]] = []

    for row in rows:
        reasons = detect_ambiguity(row)
        if args.min_priority == "charging":
            reasons = [r for r in reasons if r in CHARGING_REASONS]
        if not reasons:
            continue
        score = ambiguity_priority_score(reasons)
        candidates.append((score, row, reasons))

    candidates.sort(key=lambda x: (-x[0], str(x[1]["opinion_id"] or "")))

    total_candidates = len(candidates)
    if args.limit > 0:
        candidates = candidates[: args.limit]

    exported: List[Dict[str, str]] = []
    reason_counter: Counter = Counter()
    for score, row, reasons in candidates:
        for r in reasons:
            reason_counter[r] += 1
        exported.append(row_to_csv_dict(row, reasons, score))

    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(exported)

    manifest = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "db": str(args.db.resolve()),
        "output_csv": str(out_path),
        "min_priority": args.min_priority,
        "limit": args.limit,
        "total_reviewed_scanned": len(rows),
        "total_candidates": total_candidates,
        "exported_count": len(exported),
        "reason_breakdown": dict(reason_counter),
    }
    manifest_path = STATE_DIR / "last_ambiguous_export.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 60)
    print(" 人工标注存疑样本导出（试点 Top N）")
    print("=" * 60)
    print(f"扫描已复核：     {len(rows)} 条")
    print(f"符合筛选条件：   {total_candidates} 条")
    print(f"本次导出：       {len(exported)} 条（limit={args.limit}, scope={args.min_priority}）")
    if total_candidates > len(exported):
        print(f"未导出（低优先级）：{total_candidates - len(exported)} 条")
    print(f"CSV：            {out_path}")
    print(f"清单：           {manifest_path}")
    print("")
    print("【本次导出 · 存疑原因分布】")
    for k, v in reason_counter.most_common():
        print(f"  {k}: {v}")
    print("")
    print("【下一步】")
    print("  1. Excel 填写：复核后一级、复核后二级、复核备注、复核人")
    print("  2. python3 performance_evaluation/import_label_audit.py --csv <路径> --dry-run")
    print("  3. 去掉 --dry-run 写库 → evaluate_accuracy.py 看效果")
    print("  4. 试点有效后再 --limit 500 或 --min-priority all 扩大复核")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
