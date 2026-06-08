#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仅将 P1 主靶「规则可修复」样本写回 v3（人工=非问题，v3=业务，replay→非问题）。

与 apply_post_rules_to_v3.py --write 全库不同：只 UPDATE 明确 fixable 的行，避免非幂等回退。

用法：
  python3 performance_evaluation/eval_p1_subset.py --compare --export-fixable auto
  python3 performance_evaluation/patch_p1_fixable_v3.py --csv performance_evaluation/exports/p1_primary_fixable_*.csv
  python3 performance_evaluation/patch_p1_fixable_v3.py --csv ... --write
  python3 performance_evaluation/evaluate_accuracy.py
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
STATE_DIR = PERF_DIR / "state"

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(PERF_DIR))

from evaluate_accuracy import model_labels  # noqa: E402
from eval_p1_subset import (  # noqa: E402
    P1_PRIMARY_HUMAN,
    P1_PRIMARY_MODEL,
    replay_rules,
)
from taxonomy_normalize import canonicalize_l1_label  # noqa: E402
from patch_csv_utils import resolve_patch_csv  # noqa: E402
from v3_materialized import materialized_update_params  # noqa: E402


def _load_ids_from_csv(csv_path: Path) -> Set[str]:
    ids: Set[str] = set()
    with csv_path.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            oid = (row.get("舆情编号") or row.get("opinion_id") or "").strip()
            if oid:
                ids.add(oid)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 fixable 样本安全写回 v3")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--csv",
        nargs="+",
        required=True,
        help="export-fixable CSV；可 glob（shell 展开多个时取最新）",
    )
    parser.add_argument("--write", action="store_true", help="实际写库（默认 dry-run）")
    args = parser.parse_args()

    csv_path = resolve_patch_csv(args.csv, perf_dir=PERF_DIR)
    id_filter = _load_ids_from_csv(csv_path)
    if not id_filter:
        print("[错误] CSV 无 opinion_id", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(args.db.resolve()))
    conn.row_factory = sqlite3.Row
    ph = ",".join(["?"] * len(id_filter))
    rows = conn.execute(
        f"""
        SELECT id, opinion_id, original_text, review_l1, review_l2,
               v3_label_meta, model_class, model_keyword
        FROM opinion
        WHERE opinion_id IN ({ph})
        """,
        tuple(sorted(id_filter)),
    ).fetchall()

    batch: List[Dict[str, Any]] = []
    for row in rows:
        m1, m2 = model_labels(row)
        if not m1:
            continue
        d = {
            "opinion_id": str(row["opinion_id"] or ""),
            "text": str(row["original_text"] or "").strip(),
            "human_l1": canonicalize_l1_label(row["review_l1"]),
            "model_l1": canonicalize_l1_label(m1),
            "model_l2": (m2 or "").strip(),
        }
        batch.append(d)

    enriched = replay_rules(batch)
    fixable = [
        r
        for r in enriched
        if r["human_l1"] == P1_PRIMARY_HUMAN
        and r["model_l1"] in P1_PRIMARY_MODEL
        and r["model_l1"] != r["human_l1"]
        and r["replay_l1"] == r["human_l1"]
    ]

    print(f"CSV: {csv_path}  ids={len(id_filter)}  DB命中={len(batch)}  可写={len(fixable)}")
    for r in fixable:
        print(
            f"  {r['opinion_id']}  {r['model_l1']} -> {r['replay_l1']}  "
            f"{(r['text'] or '')[:50]}"
        )

    missing = id_filter - {r["opinion_id"] for r in batch}
    if missing:
        print(f"[警告] DB 未找到 {len(missing)} 条: {sorted(missing)[:5]}...")

    if not args.write:
        print("\n[dry-run] 加 --write 写库")
        conn.close()
        return 0

    updated = 0
    applied_at = datetime.now().isoformat(timespec="seconds")
    by_oid = {str(r["opinion_id"]): r for r in rows}
    for r in fixable:
        row = by_oid.get(r["opinion_id"])
        if not row:
            continue
        try:
            meta = json.loads(row["v3_label_meta"] or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["l1"] = r["replay_l1"]
        meta["l2"] = r["replay_l2"]
        meta["positive_capture"] = True
        meta["patch_p1_fixable_at"] = applied_at
        flags = r.get("rule_flags") or {}
        if flags:
            meta["post_rules_flags"] = flags
        v3_json = json.dumps(meta, ensure_ascii=False)
        v3_l1, v3_l2, v3_l3, v3_conf, v3_mt = materialized_update_params(meta)
        conn.execute(
            """UPDATE opinion SET v3_label_meta = ?,
               v3_l1 = ?, v3_l2 = ?, v3_l3 = ?, v3_confidence = ?, v3_match_type = ?
               WHERE id = ?""",
            (v3_json, v3_l1, v3_l2, v3_l3, v3_conf, v3_mt, row["id"]),
        )
        updated += 1
    conn.commit()
    conn.close()

    report = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "csv": str(csv_path),
        "updated": updated,
        "ids": [r["opinion_id"] for r in fixable],
    }
    out = STATE_DIR / f"patch_p1_fixable_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[已写库] {updated} 条  报告: {out}")
    print("验收: python3 performance_evaluation/evaluate_accuracy.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
