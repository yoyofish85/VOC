#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 0：导出 L1 错误切片 CSV，供 v9.x 规则回归与人工抽检。

用法：
  python3 performance_evaluation/export_l1_errors.py
  python3 performance_evaluation/export_l1_errors.py --pattern "产品质量类→非问题" --limit 50
  python3 performance_evaluation/export_l1_errors.py --snapshot
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
from typing import Any, Dict, List, Optional, Tuple

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
EXPORTS_DIR = PERF_DIR / "exports"
STATE_DIR = PERF_DIR / "state"
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
from taxonomy_normalize import canonicalize_l1_label  # noqa: E402

sys.path.insert(0, str(PERF_DIR))
from evaluate_accuracy import _connect_readonly, model_labels  # noqa: E402


def _pair_key(model_l1: str, human_l1: str) -> str:
    return f"{model_l1} → {human_l1}"


def collect_l1_errors(conn: sqlite3.Connection) -> Tuple[List[Dict[str, str]], Counter]:
    rows = conn.execute(
        """
        SELECT opinion_id, original_text, review_l1, review_l2,
               v3_label_meta, model_class, model_keyword
        FROM opinion
        WHERE review_status = 1
        """
    ).fetchall()
    errors: List[Dict[str, str]] = []
    counter: Counter = Counter()
    for row in rows:
        hr1 = str(row["review_l1"] or "").strip()
        if not hr1:
            continue
        m1_raw, m2_raw = model_labels(row)
        if not m1_raw:
            continue
        h1c = canonicalize_l1_label(hr1)
        m1c = canonicalize_l1_label(m1_raw)
        if m1c == h1c:
            continue
        key = _pair_key(m1c, h1c)
        counter[key] += 1
        errors.append(
            {
                "舆情编号": str(row["opinion_id"] or ""),
                "原文": str(row["original_text"] or "").strip(),
                "模型一级": m1c,
                "模型二级": (m2_raw or "").strip(),
                "人工一级": h1c,
                "人工二级": str(row["review_l2"] or "").strip(),
                "错误模式": key,
            }
        )
    return errors, counter


def write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["舆情编号", "原文", "模型一级", "模型二级", "人工一级", "人工二级", "错误模式"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def save_snapshot(counter: Counter, db_path: Path) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = STATE_DIR / f"baseline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    payload = {
        "snapshot_at": datetime.now().isoformat(timespec="seconds"),
        "db_abspath": str(db_path.resolve()),
        "l1_error_patterns": dict(counter.most_common()),
        "l1_error_total": sum(counter.values()),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 L1 错误切片 CSV")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--pattern",
        type=str,
        default="",
        help='错误模式，如 "产品质量类→非问题"；默认导出 Top10 各 50 条',
    )
    parser.add_argument("--limit", type=int, default=50, help="每个模式最多导出行数")
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="写入 performance_evaluation/state/baseline_*.json",
    )
    parser.add_argument("--out", type=Path, default=None, help="输出 CSV；默认 exports/l1_errors_<模式>.csv")
    args = parser.parse_args()

    conn = _connect_readonly(args.db)
    try:
        errors, counter = collect_l1_errors(conn)
    finally:
        conn.close()

    if args.snapshot:
        snap = save_snapshot(counter, args.db)
        print(f"[快照] {snap}")

    if not errors:
        print("[提示] 无 L1 错误")
        return 0

    print(f"L1 错误合计：{sum(counter.values())} 条；模式数：{len(counter)}")
    for i, (k, v) in enumerate(counter.most_common(10), 1):
        print(f"  {i}. {k} ({v})")

    if args.pattern:
        pat = args.pattern.replace("->", "→").strip()
        picked = [e for e in errors if e["错误模式"] == pat][: max(1, args.limit)]
        if not picked:
            print(f"[错误] 未找到模式: {pat}", file=sys.stderr)
            return 1
        safe = pat.replace(" → ", "_to_").replace("/", "-")
        out = args.out or (EXPORTS_DIR / f"l1_errors_{safe}.csv")
        write_csv(out, picked)
        print(f"[已导出] {len(picked)} 条 → {out}")
        return 0

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    for rank, (pat, _cnt) in enumerate(counter.most_common(10), 1):
        picked = [e for e in errors if e["错误模式"] == pat][: max(1, args.limit)]
        safe = pat.replace(" → ", "_to_").replace("/", "-")
        out = EXPORTS_DIR / f"l1_errors_{rank:02d}_{safe}_{ts}.csv"
        write_csv(out, picked)
        print(f"[已导出] {len(picked)} 条 → {out.name}")

    all_out = EXPORTS_DIR / f"l1_errors_all_{ts}.csv"
    write_csv(all_out, errors)
    print(f"[已导出] 全部 {len(errors)} 条 → {all_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
