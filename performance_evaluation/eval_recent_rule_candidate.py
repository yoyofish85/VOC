#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
近批次规则候选 dry-run：对比 v3 基线 vs apply_classification_post_rules 重放。

只读 SQLite，不写库。用于 Release A/B 门禁（fixed/broken/net）。

用法:
  python3 performance_evaluation/eval_recent_rule_candidate.py --since-days 7
  python3 performance_evaluation/eval_recent_rule_candidate.py \\
    --upload-batch 20260713_1_105 --max-l1-broken 8 --export-csv exports/candidate.csv
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Sequence

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(PERF_DIR))

from taxonomy_normalize import canonicalize_l1_label  # noqa: E402
from evaluate_accuracy import (  # noqa: E402
    _connect_readonly,
    _l2_eval_applicable,
    model_labels,
    normalize_l2,
)
from time_utils import row_review_time  # noqa: E402
from post_rules_replay import apply_post_rules_with_context  # noqa: E402

SELECT_REVIEWED = """
SELECT opinion_id, original_text, source, vin, review_l1, review_l2,
       v3_label_meta, model_class, model_keyword, reviewed_at, create_time, upload_batch
FROM opinion
WHERE review_status = 1
"""


def _pct(correct: int, total: int) -> float:
    return round(correct / total * 100, 2) if total else 0.0


def compare_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """对比 model_*（基线）与 replay_*（规则重放）相对人工标签的 fixed/broken。"""
    l1_total = 0
    l1_before_correct = 0
    l1_after_correct = 0
    l1_fixed = 0
    l1_broken = 0

    l2_total = 0
    l2_before_correct = 0
    l2_after_correct = 0
    l2_fixed = 0
    l2_broken = 0

    for row in rows:
        human_l1 = canonicalize_l1_label(str(row.get("human_l1") or ""))
        model_l1 = canonicalize_l1_label(str(row.get("model_l1") or ""))
        replay_l1 = canonicalize_l1_label(str(row.get("replay_l1") or ""))
        if not human_l1:
            continue

        l1_total += 1
        before_ok = model_l1 == human_l1
        after_ok = replay_l1 == human_l1
        if before_ok:
            l1_before_correct += 1
        if after_ok:
            l1_after_correct += 1
        if not before_ok and after_ok:
            l1_fixed += 1
        if before_ok and not after_ok:
            l1_broken += 1

        human_l2 = normalize_l2(str(row.get("human_l2") or ""))
        if not human_l2 or not _l2_eval_applicable(human_l1, model_l1):
            continue

        model_l2 = normalize_l2(str(row.get("model_l2") or ""))
        replay_l2 = normalize_l2(str(row.get("replay_l2") or ""))
        l2_total += 1
        l2_before_ok = model_l2 == human_l2
        l2_after_ok = replay_l2 == human_l2
        if l2_before_ok:
            l2_before_correct += 1
        if l2_after_ok:
            l2_after_correct += 1
        if not l2_before_ok and l2_after_ok:
            l2_fixed += 1
        if l2_before_ok and not l2_after_ok:
            l2_broken += 1

    return {
        "l1_total": l1_total,
        "l1_before_correct": l1_before_correct,
        "l1_after_correct": l1_after_correct,
        "l1_before_pct": _pct(l1_before_correct, l1_total),
        "l1_after_pct": _pct(l1_after_correct, l1_total),
        "l1_fixed": l1_fixed,
        "l1_broken": l1_broken,
        "l1_net": l1_fixed - l1_broken,
        "l2_total": l2_total,
        "l2_before_correct": l2_before_correct,
        "l2_after_correct": l2_after_correct,
        "l2_before_pct": _pct(l2_before_correct, l2_total),
        "l2_after_pct": _pct(l2_after_correct, l2_total),
        "l2_fixed": l2_fixed,
        "l2_broken": l2_broken,
        "l2_net": l2_fixed - l2_broken,
    }


def _row_dict(row: sqlite3.Row) -> Dict[str, Any]:
    m1, m2 = model_labels(row)
    return {
        "opinion_id": str(row["opinion_id"] or ""),
        "text": str(row["original_text"] or "").strip(),
        "source": str(row["source"] or "").strip(),
        "vin": str(row["vin"] or "").strip(),
        "upload_batch": str(row["upload_batch"] or "").strip(),
        "human_l1": canonicalize_l1_label(row["review_l1"]),
        "human_l2": str(row["review_l2"] or "").strip(),
        "model_l1": canonicalize_l1_label(m1),
        "model_l2": (m2 or "").strip(),
        "replay_l1": "",
        "replay_l2": "",
        "rule_flags": {},
    }


def fetch_rows(
    conn: sqlite3.Connection,
    *,
    since_days: int,
    upload_batches: Sequence[str],
) -> List[Dict[str, Any]]:
    rows = conn.execute(SELECT_REVIEWED).fetchall()
    if upload_batches:
        batch_set = {b.strip() for b in upload_batches if b.strip()}
        rows = [r for r in rows if str(r["upload_batch"] or "").strip() in batch_set]
    else:
        cutoff = datetime.now() - timedelta(days=max(1, since_days))
        filtered: List[sqlite3.Row] = []
        for row in rows:
            ts = row_review_time(row["reviewed_at"], row["create_time"])
            if ts and ts >= cutoff:
                filtered.append(row)
        rows = filtered

    out: List[Dict[str, Any]] = []
    for row in rows:
        d = _row_dict(row)
        if not d["human_l1"] or not d["model_l1"]:
            continue
        out.append(d)
    return out


def replay_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from qwen_ollama import load_l2_whitelist  # noqa: E402

    l2_map = load_l2_whitelist()
    enriched: List[Dict[str, Any]] = []
    for d in rows:
        nl1, nl2, flags = apply_post_rules_with_context(
            d["text"],
            d["model_l1"],
            d["model_l2"],
            l2_map,
            source=d["source"],
            vin=d["vin"],
        )
        nd = dict(d)
        nd["replay_l1"] = canonicalize_l1_label(nl1)
        nd["replay_l2"] = (nl2 or "").strip()
        nd["rule_flags"] = flags
        enriched.append(nd)
    return enriched


def _format_flags(flags: Dict[str, bool]) -> str:
    if not flags:
        return ""
    return ";".join(sorted(k for k, v in flags.items() if v))


def export_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "opinion_id",
        "upload_batch",
        "human_l1",
        "model_l1",
        "replay_l1",
        "human_l2",
        "model_l2",
        "replay_l2",
        "l1_change",
        "l2_change",
        "rule_flags",
        "text",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "opinion_id": r["opinion_id"],
                    "upload_batch": r.get("upload_batch", ""),
                    "human_l1": r["human_l1"],
                    "model_l1": r["model_l1"],
                    "replay_l1": r["replay_l1"],
                    "human_l2": r["human_l2"],
                    "model_l2": r["model_l2"],
                    "replay_l2": r["replay_l2"],
                    "l1_change": "fixed"
                    if r["model_l1"] != r["human_l1"] and r["replay_l1"] == r["human_l1"]
                    else "broken"
                    if r["model_l1"] == r["human_l1"] and r["replay_l1"] != r["human_l1"]
                    else "",
                    "l2_change": "fixed"
                    if normalize_l2(r["model_l2"]) != normalize_l2(r["human_l2"])
                    and normalize_l2(r["replay_l2"]) == normalize_l2(r["human_l2"])
                    else "broken"
                    if normalize_l2(r["model_l2"]) == normalize_l2(r["human_l2"])
                    and normalize_l2(r["replay_l2"]) != normalize_l2(r["human_l2"])
                    else "",
                    "rule_flags": _format_flags(r.get("rule_flags") or {}),
                    "text": r["text"][:500],
                }
            )


def print_report(result: Dict[str, Any]) -> None:
    print(f"L1 total={result['l1_total']}")
    print(
        f"  before: {result['l1_before_correct']}/{result['l1_total']} "
        f"({result['l1_before_pct']}%)"
    )
    print(
        f"  after:  {result['l1_after_correct']}/{result['l1_total']} "
        f"({result['l1_after_pct']}%)"
    )
    print(
        f"  fixed={result['l1_fixed']} broken={result['l1_broken']} "
        f"net={result['l1_net']}"
    )
    print(f"L2 total={result['l2_total']}")
    print(
        f"  before: {result['l2_before_correct']}/{result['l2_total']} "
        f"({result['l2_before_pct']}%)"
    )
    print(
        f"  after:  {result['l2_after_correct']}/{result['l2_total']} "
        f"({result['l2_after_pct']}%)"
    )
    print(
        f"  fixed={result['l2_fixed']} broken={result['l2_broken']} "
        f"net={result['l2_net']}"
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="近批次规则候选 dry-run 评估")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--since-days", type=int, default=7)
    parser.add_argument("--upload-batch", action="append", default=[])
    parser.add_argument("--export-csv", type=Path)
    parser.add_argument("--max-l1-broken", type=int, default=8)
    parser.add_argument("--max-l2-broken", type=int, default=3)
    args = parser.parse_args(argv)

    conn = _connect_readonly(args.db)
    try:
        rows = fetch_rows(
            conn,
            since_days=args.since_days,
            upload_batches=args.upload_batch,
        )
    finally:
        conn.close()

    if not rows:
        print("[提示] 无符合条件的已复核样本")
        return 0

    replayed = replay_rows(rows)
    result = compare_rows(replayed)
    print_report(result)

    if args.export_csv:
        export_csv(args.export_csv, replayed)
        print(f"CSV: {args.export_csv.resolve()}")

    if result["l1_broken"] > args.max_l1_broken or result["l2_broken"] > args.max_l2_broken:
        print("FAIL: broken gate exceeded")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
