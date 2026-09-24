#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S6 dry-run：对存量样本抽取洞察字段（只读，不写库）。

用法：
  python3 performance_evaluation/dry_run_insight_fields.py --limit 200
  python3 performance_evaluation/dry_run_insight_fields.py --db src/backend/opinion_review.db --limit 200
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

from label_project.insight_fields import (  # noqa: E402
    classification_core_unchanged,
    extract_insight_fields,
)

EXPORTS = Path(__file__).resolve().parent / "exports"
DEFAULT_DB = ROOT / "src" / "backend" / "opinion_review.db"


def _fetch_rows(db: Path, limit: int, since_days: int) -> List[Dict[str, Any]]:
    uri = f"file:{db.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    sql = """
      SELECT opinion_id, original_text, v3_l1, v3_l2, v3_l3, v3_match_type,
             upload_batch
      FROM opinion
      WHERE TRIM(IFNULL(original_text,'')) != ''
        AND TRIM(IFNULL(v3_l1,'')) != ''
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
    intent_c: Counter = Counter()
    action_c: Counter = Counter()
    sev_c: Counter = Counter()
    urg_c: Counter = Counter()
    repeat_n = 0
    core_ok = 0
    for r in rows:
        text = r.get("original_text") or ""
        l1 = (r.get("v3_l1") or "").strip()
        l2 = (r.get("v3_l2") or "").strip()
        l3 = (r.get("v3_l3") or "").strip()
        before = {
            "l1": l1,
            "l2": l2,
            "l3": l3,
            "confidence": 0.9,
            "match_type": r.get("v3_match_type") or "",
        }
        fields = extract_insight_fields(text, l1=l1, l2=l2, l3=l3)
        after = dict(before)
        after.update(fields)
        if classification_core_unchanged(before, after):
            core_ok += 1
        intent_c[fields["intent"]] += 1
        action_c[fields["expected_action"]] += 1
        sev_c[fields["severity"]] += 1
        urg_c[fields["urgency"]] += 1
        if fields["repeat_signal"]:
            repeat_n += 1
        out_rows.append(
            {
                "opinion_id": r.get("opinion_id"),
                "upload_batch": r.get("upload_batch"),
                "l1": l1,
                "l2": l2,
                "l3": l3,
                "text": (text or "")[:200],
                **fields,
            }
        )
    n = len(rows) or 1
    return {
        "sample_n": len(rows),
        "core_unchanged_rate": round(core_ok / n, 4),
        "repeat_rate": round(repeat_n / n, 4),
        "intent_distribution": dict(intent_c.most_common()),
        "expected_action_distribution": dict(action_c.most_common()),
        "severity_distribution": dict(sev_c.most_common()),
        "urgency_distribution": dict(urg_c.most_common()),
        "gate_ok": core_ok == len(rows),
        "rows": out_rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="S6 insight fields dry-run (read-only)")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--since-days", type=int, default=0)
    args = ap.parse_args()
    if not args.db.is_file():
        print(json.dumps({"error": f"db not found: {args.db}"}, ensure_ascii=False))
        return 2
    rows = _fetch_rows(args.db, args.limit, args.since_days)
    result = run_dry_run(rows)
    EXPORTS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = EXPORTS / f"insight_fields_dryrun_{ts}.csv"
    json_path = EXPORTS / f"insight_fields_dryrun_{ts}.json"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "opinion_id",
                "upload_batch",
                "l1",
                "l2",
                "l3",
                "text",
                "intent",
                "expected_action",
                "severity",
                "urgency",
                "repeat_signal",
                "root_cause_hint",
                "human_ok",
            ],
        )
        w.writeheader()
        for r in result["rows"]:
            row = dict(r)
            row["human_ok"] = ""
            w.writerow(row)
    summary = {k: v for k, v in result.items() if k != "rows"}
    summary["csv"] = str(csv_path)
    summary["note"] = "请在 CSV 的 human_ok 列标注 1/0，目标一致率≥85%"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result["gate_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
