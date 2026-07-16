#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按 upload_batch 或近 N 天入库切片，输出与 evaluate_accuracy.py 一致的 L1/L2 口径。

用法（项目根）:
  python3 performance_evaluation/eval_batch_accuracy.py --list-batches 7
  python3 performance_evaluation/eval_batch_accuracy.py --since-days 4
  python3 performance_evaluation/eval_batch_accuracy.py --upload-batch 20260713_1_105
  python3 performance_evaluation/eval_batch_accuracy.py --upload-batch A --upload-batch B
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
from taxonomy_normalize import canonicalize_l1_label  # noqa: E402

sys.path.insert(0, str(PERF_DIR))
from evaluate_accuracy import (  # noqa: E402
    _connect_readonly,
    _l2_eval_applicable,
    model_labels,
    normalize_l2,
)
from time_utils import parse_timestamp, row_review_time  # noqa: E402


def _metrics(rows: Sequence[sqlite3.Row]) -> Dict[str, Any]:
    l1_ok = l1_tot = l2_ok = l2_tot = 0
    err = Counter()
    for row in rows:
        h1 = canonicalize_l1_label(str(row["review_l1"] or ""))
        m1, m2 = model_labels(row)
        m1c = canonicalize_l1_label(m1)
        if not h1 or not m1c:
            continue
        l1_tot += 1
        if m1c == h1:
            l1_ok += 1
        else:
            err[f"{m1c}→{h1}"] += 1
        h2 = normalize_l2(str(row["review_l2"] or ""))
        if _l2_eval_applicable(h1, m1c) and h2:
            l2_tot += 1
            if normalize_l2(m2) == h2:
                l2_ok += 1
    l1_pct = round(l1_ok / max(l1_tot, 1) * 100, 2)
    l2_pct = round(l2_ok / max(l2_tot, 1) * 100, 2) if l2_tot else None
    return {
        "rows": len(rows),
        "l1_ok": l1_ok,
        "l1_tot": l1_tot,
        "l1_pct": l1_pct,
        "l2_ok": l2_ok,
        "l2_tot": l2_tot,
        "l2_pct": l2_pct,
        "top_l1_errors": err.most_common(5),
    }


def _cutoff(days: int) -> datetime:
    return datetime.now() - timedelta(days=max(1, days))


def list_batches(conn: sqlite3.Connection, since_days: int) -> None:
    cutoff = _cutoff(since_days)
    rows = conn.execute(
        """SELECT upload_batch, review_status, create_time, reviewed_at
           FROM opinion
           WHERE upload_batch IS NOT NULL AND TRIM(upload_batch) != ''"""
    ).fetchall()
    stats: Dict[str, Dict[str, int]] = {}
    for row in rows:
        ts = row_review_time(row["reviewed_at"], row["create_time"])
        if not ts or ts < cutoff:
            continue
        batch = str(row["upload_batch"]).strip()
        st = stats.setdefault(batch, {"total": 0, "reviewed": 0})
        st["total"] += 1
        if int(row["review_status"] or 0) == 1:
            st["reviewed"] += 1
    if not stats:
        print(f"[提示] 近 {since_days} 天无 upload_batch 记录")
        return
    print(f"近 {since_days} 天批次（按 create_time/reviewed_at）:")
    for batch, st in sorted(stats.items()):
        print(f"  {batch}  入库={st['total']}  已复核={st['reviewed']}")


def fetch_by_batches(conn: sqlite3.Connection, batches: Sequence[str]) -> List[sqlite3.Row]:
    ph = ",".join("?" * len(batches))
    return conn.execute(
        f"""SELECT * FROM opinion
            WHERE upload_batch IN ({ph}) AND review_status = 1""",
        list(batches),
    ).fetchall()


def fetch_since_days(conn: sqlite3.Connection, since_days: int) -> List[sqlite3.Row]:
    cutoff = _cutoff(since_days)
    rows = conn.execute(
        """SELECT * FROM opinion WHERE review_status = 1"""
    ).fetchall()
    out: List[sqlite3.Row] = []
    for row in rows:
        ts = row_review_time(row["reviewed_at"], row["create_time"])
        if ts and ts >= cutoff:
            out.append(row)
    return out


def _print_report(title: str, m: Dict[str, Any]) -> None:
    print(f"\n=== {title} ===")
    print(f"已复核行数: {m['rows']}")
    print(f"L1: {m['l1_ok']}/{m['l1_tot']} = {m['l1_pct']}%")
    if m["l2_tot"]:
        print(f"L2: {m['l2_ok']}/{m['l2_tot']} = {m['l2_pct']}%")
    else:
        print("L2: 分母为 0（业务三类+L1一致+有人工L2）")
    for k, v in m["top_l1_errors"]:
        print(f"  {k}: {v}")


def main() -> int:
    parser = argparse.ArgumentParser(description="按批次/时间窗口评估 L1/L2")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--upload-batch", action="append", default=[], help="可重复指定")
    parser.add_argument("--since-days", type=int, default=0, help="近 N 天已复核入库")
    parser.add_argument("--list-batches", type=int, default=0, metavar="N", help="列出近 N 天批次")
    args = parser.parse_args()

    conn = _connect_readonly(args.db)
    try:
        if args.list_batches:
            list_batches(conn, args.list_batches)
            return 0
        if args.upload_batch:
            for batch in args.upload_batch:
                rows = fetch_by_batches(conn, [batch.strip()])
                _print_report(batch, _metrics(rows))
            all_rows = fetch_by_batches(conn, [b.strip() for b in args.upload_batch])
            if len(args.upload_batch) > 1:
                _print_report("合计", _metrics(all_rows))
            return 0
        if args.since_days:
            rows = fetch_since_days(conn, args.since_days)
            _print_report(f"近{args.since_days}天已复核", _metrics(rows))
            return 0
        parser.error("请指定 --list-batches N、--since-days N 或 --upload-batch")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
