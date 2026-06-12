#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回流管线健康检查（CI / cron / 部署后巡检）。

检查项：
  - reflow_synced = -1 的明确失败行
  - reflow_synced = 0 且已复核超过 N 小时未同步（卡住）
  - pending_reflow.jsonl / reflow_failures.jsonl 积压
  - data_clear.csv 行数

退出码：0=健康，1=存在问题（可触发告警）

用法：
  python3 performance_evaluation/check_reflow_health.py
  python3 performance_evaluation/check_reflow_health.py --db-path src/backend/opinion_review.db
  python3 performance_evaluation/check_reflow_health.py --warn-failures 0 --warn-stale 0
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "src" / "backend"
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config.paths import LABELED_DIR  # noqa: E402


def _count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8", errors="replace") as f:
        return sum(1 for ln in f if ln.strip())


def _count_csv_data_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8-sig", errors="replace") as f:
        lines = [ln for ln in f if ln.strip()]
    return max(0, len(lines) - 1)


def run_checks(
    db_path: Path,
    pending_path: Path,
    failures_path: Path,
    data_clear_path: Path,
    *,
    warn_failures: int,
    warn_stale: int,
    stale_hours: int,
    warn_pending: int,
    warn_failure_log: int,
) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    info: List[str] = []

    if not db_path.is_file():
        issues.append(f"数据库不存在: {db_path}")
        return False, issues

    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        failed = conn.execute(
            "SELECT COUNT(*) FROM opinion WHERE reflow_synced = -1"
        ).fetchone()[0]
        stale = conn.execute(
            """
            SELECT COUNT(*) FROM opinion
            WHERE reflow_synced = 0 AND review_status = 1
            AND reviewed_at IS NOT NULL AND TRIM(reviewed_at) != ''
            AND reviewed_at < datetime('now', ?)
            """,
            (f"-{int(stale_hours)} hours",),
        ).fetchone()[0]
        pending_review = conn.execute(
            "SELECT COUNT(*) FROM opinion WHERE reflow_synced = 0 AND review_status = 1"
        ).fetchone()[0]
    finally:
        conn.close()

    pending_lines = _count_lines(pending_path)
    failure_log_lines = _count_lines(failures_path)
    clear_rows = _count_csv_data_rows(data_clear_path)

    info.append(f"reflow_synced=-1 失败行: {failed}")
    info.append(f"reflow_synced=0 已复核行: {pending_review}")
    info.append(f"已复核 >{stale_hours}h 未同步: {stale}")
    info.append(f"pending_reflow.jsonl 行数: {pending_lines}")
    info.append(f"reflow_failures.jsonl 行数: {failure_log_lines}")
    info.append(f"data_clear.csv 数据行: {clear_rows}")

    if failed > warn_failures:
        issues.append(f"reflow_synced=-1 超过阈值 ({failed} > {warn_failures})")
    if stale > warn_stale:
        issues.append(f"已复核超过 {stale_hours}h 仍未同步 ({stale} > {warn_stale})")
    if pending_lines > warn_pending:
        issues.append(f"pending_reflow.jsonl 积压 ({pending_lines} > {warn_pending})")
    if failure_log_lines > warn_failure_log:
        issues.append(f"reflow_failures.jsonl 积压 ({failure_log_lines} > {warn_failure_log})")

    for line in info:
        print(f"  · {line}")

    return len(issues) == 0, issues


def main() -> int:
    parser = argparse.ArgumentParser(description="VOC 回流管线健康检查")
    parser.add_argument(
        "--db-path",
        default=str(BACKEND_DIR / "opinion_review.db"),
        help="SQLite 数据库路径",
    )
    parser.add_argument(
        "--pending-path",
        default=str(BACKEND_DIR / "pending_reflow.jsonl"),
        help="延迟回流队列 jsonl",
    )
    parser.add_argument(
        "--failures-path",
        default=str(BACKEND_DIR / "reflow_failures.jsonl"),
        help="回流失败审计 jsonl",
    )
    parser.add_argument(
        "--data-clear-path",
        default=str(LABELED_DIR / "data_clear.csv"),
        help="清洗库 CSV 路径",
    )
    parser.add_argument(
        "--warn-failures",
        type=int,
        default=0,
        help="reflow_synced=-1 允许上限（超过则退出 1）",
    )
    parser.add_argument(
        "--warn-stale",
        type=int,
        default=0,
        help="超时未同步行数允许上限",
    )
    parser.add_argument(
        "--stale-hours",
        type=int,
        default=48,
        help="判定卡住的已复核小时数",
    )
    parser.add_argument(
        "--warn-pending",
        type=int,
        default=100,
        help="pending_reflow.jsonl 行数上限",
    )
    parser.add_argument(
        "--warn-failure-log",
        type=int,
        default=500,
        help="reflow_failures.jsonl 行数上限",
    )
    args = parser.parse_args()

    print("=" * 56)
    print(" VOC 回流管线健康检查")
    print("=" * 56)
    print(f"  DB: {args.db_path}")

    ok, issues = run_checks(
        Path(args.db_path),
        Path(args.pending_path),
        Path(args.failures_path),
        Path(args.data_clear_path),
        warn_failures=args.warn_failures,
        warn_stale=args.warn_stale,
        stale_hours=args.stale_hours,
        warn_pending=args.warn_pending,
        warn_failure_log=args.warn_failure_log,
    )

    print("")
    if ok:
        print("回流健康状态正常")
        return 0
    print("发现以下问题：")
    for item in issues:
        print(f"  ✗ {item}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
