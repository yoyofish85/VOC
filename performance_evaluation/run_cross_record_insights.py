#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S7：跨记录洞察 dry-run（只读）+ CSV 证据导出。

用法：
  python3 performance_evaluation/run_cross_record_insights.py --limit 500
  python3 performance_evaluation/run_cross_record_insights.py --db src/backend/opinion_review.db --days 30
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from label_project.cross_record_insights import (  # noqa: E402
    _enrich_row,
    _parse_day,
    evidence_detail_rows,
    run_cross_record_insights,
)
from label_project.theme_lookup import load_theme_mapping  # noqa: E402

EXPORTS = Path(__file__).resolve().parent / "exports"
DEFAULT_DB = ROOT / "src" / "backend" / "opinion_review.db"

_SQL_COLS = """
  opinion_id, original_text, v3_l1, v3_l2, v3_l3, v3_label_meta,
  review_status, review_l1, review_l2, review_l3, reviewed_at, create_time,
  vin, phone, car_model, country, reflow_synced, upload_batch
"""


def _fetch(db: Path, limit: int) -> List[Dict[str, Any]]:
    """按 rowid 取最近 limit 条（库内 create_time 格式不统一，窗口在 Python 侧切）。"""
    uri = f"file:{db.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    sql = f"""
      SELECT {_SQL_COLS}
      FROM opinion
      WHERE TRIM(IFNULL(original_text,'')) != ''
      ORDER BY rowid DESC
      LIMIT ?
    """
    rows = [dict(r) for r in conn.execute(sql, [int(limit)]).fetchall()]
    conn.close()
    return rows


def _split_windows(
    rows: List[Dict[str, Any]], days: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    dated: List[Tuple[datetime, Dict[str, Any]]] = []
    undated: List[Dict[str, Any]] = []
    for r in rows:
        d = _parse_day(r.get("reviewed_at")) or _parse_day(r.get("create_time"))
        if d:
            dated.append((d, r))
        else:
            undated.append(r)
    if not dated:
        return rows, []
    max_day = max(d for d, _ in dated)
    cur_cut = max_day - timedelta(days=max(days, 1) - 1)
    prev_cut = cur_cut - timedelta(days=max(days, 1))
    current = [r for d, r in dated if d >= cur_cut] + undated
    previous = [r for d, r in dated if prev_cut <= d < cur_cut]
    return current, previous


def _export_opinion_csv(path: Path, result: Dict[str, Any], rows: List[Dict[str, Any]]) -> int:
    """主题→L3→opinion 明细；行数应等于 opinion_id 去重总数。"""
    mp = load_theme_mapping()
    enriched: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        er = _enrich_row(dict(r), mp)
        oid = str(er.get("opinion_id") or "")
        if oid and oid not in enriched:
            enriched[oid] = er
    n = 0
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "theme_id",
                "theme_name",
                "path",
                "l1",
                "l2",
                "l3",
                "opinion_id",
                "reviewed",
                "vin",
                "phone",
                "car_model_norm",
                "country",
                "intent",
                "expected_action",
                "severity",
                "text",
            ],
        )
        w.writeheader()
        for th in result.get("evidence_chain") or []:
            for oid in th.get("opinion_ids") or []:
                er = enriched.get(str(oid)) or {}
                vin = er.get("vin") or ""
                phone = er.get("phone") or ""
                w.writerow(
                    {
                        "theme_id": th["theme_id"],
                        "theme_name": th["theme_name"],
                        "path": er.get("path"),
                        "l1": er.get("l1"),
                        "l2": er.get("l2"),
                        "l3": er.get("l3"),
                        "opinion_id": oid,
                        "reviewed": int(bool(er.get("reviewed"))),
                        "vin": (vin[:6] + "***") if vin else "",
                        "phone": (phone[:3] + "****") if phone else "",
                        "car_model_norm": er.get("car_model_norm"),
                        "country": er.get("country"),
                        "intent": er.get("intent"),
                        "expected_action": er.get("expected_action"),
                        "severity": er.get("severity"),
                        "text": (er.get("original_text") or "")[:200],
                    }
                )
                n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="S7 cross-record insights (read-only)")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--days", type=int, default=30, help="当前/上一窗天数（Python 侧切分）")
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--period-days", type=int, default=7)
    args = ap.parse_args()
    if not args.db.is_file():
        print(json.dumps({"error": f"db not found: {args.db}"}, ensure_ascii=False))
        return 2

    raw = _fetch(args.db, args.limit)
    current, previous = _split_windows(raw, args.days)
    query = {
        "db": str(args.db),
        "days": args.days,
        "limit": args.limit,
        "period_days": args.period_days,
        "fetched_n": len(raw),
        "current_n": len(current),
        "previous_n": len(previous),
        "read_only": True,
    }
    result = run_cross_record_insights(
        current,
        previous_rows=previous,
        window_days=args.days,
        period_days=args.period_days,
        query=query,
    )
    EXPORTS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = EXPORTS / f"cross_record_insights_{ts}.json"
    theme_csv = EXPORTS / f"cross_record_theme_l3_{ts}.csv"
    detail_csv = EXPORTS / f"cross_record_evidence_{ts}.csv"

    with theme_csv.open("w", encoding="utf-8-sig", newline="") as f:
        trows = evidence_detail_rows(result)
        if trows:
            w = csv.DictWriter(f, fieldnames=list(trows[0].keys()))
            w.writeheader()
            w.writerows(trows)

    detail_n = _export_opinion_csv(detail_csv, result, current)
    recon = result["reconcile"]
    export_ok = detail_n == recon.get("opinion_id_total")
    summary = {
        "gate_ok": bool(result["gate_ok"] and export_ok),
        "reconcile": recon,
        "export_detail_rows": detail_n,
        "export_match_opinion_ids": export_ok,
        "meta": result["meta"],
        "resolution": result["resolution"],
        "intent_shift_top": (result.get("intent_shift") or [])[:5],
        "top_expectations": (result.get("top_expectations") or [])[:5],
        "concentration_top": (result.get("concentration") or [])[:5],
        "fault_lifecycle_top": (result.get("fault_lifecycle") or [])[:5],
        "insights": result.get("insights") or [],
        "theme_count": len(result.get("evidence_chain") or []),
        "json": str(json_path),
        "theme_l3_csv": str(theme_csv),
        "evidence_csv": str(detail_csv),
    }
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if summary["gate_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
