#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 apply_classification_post_rules() 重放结果写回 v3_label_meta（与 eval_p1_subset --compare 同源）。

⚠️ 限制：仅当库内 v3 ≈ 14B 原始输出（未套过后处理）时安全。
全量 reclassify 后的 v3 已含旧版规则，再重放会导致 L1 回退（非幂等）。
此类场景请用 reclassify_all_with_14b.py 写库，勿用本脚本。

默认 dry-run；确认后加 --write。

用法（【需部署服务器】，写生产库前建议 cp 备份 db）：
  python3 performance_evaluation/evaluate_accuracy.py   # 写库前 KPI 快照（写入 last_eval.json）
  python3 performance_evaluation/apply_post_rules_to_v3.py
  python3 performance_evaluation/apply_post_rules_to_v3.py --write
  python3 performance_evaluation/evaluate_accuracy.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
STATE_DIR = PERF_DIR / "state"
REPORTS_DIR = PERF_DIR / "reports"
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(PERF_DIR))

from taxonomy_normalize import canonicalize_l1_label  # noqa: E402
from evaluate_accuracy import _connect_readonly, model_labels  # noqa: E402
from v3_materialized import materialized_update_params  # noqa: E402


def _parse_v3_meta(raw: Optional[str]) -> Dict[str, Any]:
    if not raw or not str(raw).strip():
        return {}
    try:
        j = json.loads(raw)
        return j if isinstance(j, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _load_ids(path: Path) -> Set[str]:
    return {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}


def _fetch_rows(conn: sqlite3.Connection, id_filter: Optional[Set[str]]) -> List[sqlite3.Row]:
    if id_filter is not None:
        if not id_filter:
            return []
        ph = ",".join(["?"] * len(id_filter))
        return conn.execute(
            f"""
            SELECT id, opinion_id, original_text, review_l1, review_l2, review_status,
                   v3_label_meta, model_class, model_keyword
            FROM opinion
            WHERE opinion_id IN ({ph})
            """,
            tuple(sorted(id_filter)),
        ).fetchall()
    return conn.execute(
        """
        SELECT id, opinion_id, original_text, review_l1, review_l2, review_status,
               v3_label_meta, model_class, model_keyword
        FROM opinion
        WHERE review_status = 1
        """
    ).fetchall()


def _replay_one(
    text: str, m1: str, m2: str, l2_map: Dict[str, List[str]]
) -> Tuple[str, str, Dict[str, bool]]:
    from qwen_ollama import apply_classification_post_rules  # noqa: E402

    nl1, nl2, flags = apply_classification_post_rules(text, m1, m2, l2_map)
    return canonicalize_l1_label(nl1), (nl2 or "").strip(), flags


def main() -> int:
    parser = argparse.ArgumentParser(description="规则重放写回 v3_label_meta")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--write",
        action="store_true",
        help="实际 UPDATE（默认仅 dry-run 统计）",
    )
    parser.add_argument(
        "--ids-file",
        type=Path,
        default=None,
        help="仅处理列表内 opinion_id（如 eval_p1_subset --export-ids 输出）",
    )
    parser.add_argument(
        "--include-unreviewed",
        action="store_true",
        help="处理全库有 v3/model 标签的行（默认仅 review_status=1）",
    )
    args = parser.parse_args()

    id_filter: Optional[Set[str]] = None
    if args.ids_file:
        id_filter = _load_ids(args.ids_file)

    if args.include_unreviewed and id_filter is None:
        conn_ro = sqlite3.connect(str(args.db.resolve()))
        conn_ro.row_factory = sqlite3.Row
        rows = conn_ro.execute(
            """
            SELECT id, opinion_id, original_text, review_l1, review_l2, review_status,
                   v3_label_meta, model_class, model_keyword
            FROM opinion
            WHERE (v3_label_meta IS NOT NULL AND TRIM(v3_label_meta) != '')
               OR (model_class IS NOT NULL AND TRIM(model_class) != '')
            """
        ).fetchall()
        conn_ro.close()
    else:
        conn_ro = _connect_readonly(args.db) if not args.write else sqlite3.connect(str(args.db.resolve()))
        conn_ro.row_factory = sqlite3.Row
        try:
            rows = _fetch_rows(conn_ro, id_filter)
        finally:
            conn_ro.close()

    from qwen_ollama import load_l2_whitelist  # noqa: E402

    l2_map = load_l2_whitelist()
    changed: List[Dict[str, Any]] = []
    skipped_no_model = 0
    l1_delta = Counter()

    for row in rows:
        m1, m2 = model_labels(row)
        if not m1:
            skipped_no_model += 1
            continue
        text = str(row["original_text"] or "").strip()
        old_l1 = canonicalize_l1_label(m1)
        old_l2 = (m2 or "").strip()
        new_l1, new_l2, flags = _replay_one(text, m1, m2, l2_map)
        if new_l1 == old_l1 and new_l2 == old_l2:
            continue
        human_l1 = canonicalize_l1_label(row["review_l1"]) if row["review_l1"] else ""
        rec = {
            "opinion_id": str(row["opinion_id"] or ""),
            "old_l1": old_l1,
            "old_l2": old_l2,
            "new_l1": new_l1,
            "new_l2": new_l2,
            "human_l1": human_l1,
            "fixed_l1": bool(human_l1 and old_l1 != human_l1 and new_l1 == human_l1),
            "broken_l1": bool(human_l1 and old_l1 == human_l1 and new_l1 != human_l1),
            "rule_flags": flags,
        }
        changed.append(rec)
        l1_delta[f"{old_l1} → {new_l1}"] += 1

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "db": str(args.db.resolve()),
        "dry_run": not args.write,
        "scanned": len(rows),
        "skipped_no_model": skipped_no_model,
        "changed": len(changed),
        "l1_fixed_on_reviewed": sum(1 for r in changed if r["fixed_l1"]),
        "l1_broken_on_reviewed": sum(1 for r in changed if r["broken_l1"]),
        "l1_transitions": dict(l1_delta.most_common()),
        "sample_changes": changed[:20],
    }

    if args.write and changed:
        conn = sqlite3.connect(str(args.db.resolve()))
        conn.row_factory = sqlite3.Row
        by_oid = {str(r["opinion_id"]): r for r in rows}
        applied_at = datetime.now().isoformat(timespec="seconds")
        for rec in changed:
            oid = rec["opinion_id"]
            row = by_oid.get(oid)
            if not row:
                continue
            meta = _parse_v3_meta(row["v3_label_meta"])
            meta["l1"] = rec["new_l1"]
            meta["l2"] = rec["new_l2"]
            meta["post_rules_replay_at"] = applied_at
            if rec["rule_flags"]:
                meta["post_rules_flags"] = rec["rule_flags"]
            v3_json = json.dumps(meta, ensure_ascii=False)
            v3_l1, v3_l2, v3_l3, v3_conf, v3_mt = materialized_update_params(meta)
            conn.execute(
                """UPDATE opinion SET v3_label_meta = ?,
                   v3_l1 = ?, v3_l2 = ?, v3_l3 = ?, v3_confidence = ?, v3_match_type = ?
                   WHERE id = ?""",
                (v3_json, v3_l1, v3_l2, v3_l3, v3_conf, v3_mt, row["id"]),
            )
        conn.commit()
        conn.close()
        report["written"] = len(changed)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    state_path = STATE_DIR / f"post_rules_replay_{ts}.json"
    state_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    mode = "写库" if args.write else "dry-run"
    print(f"[{mode}] 扫描 {len(rows)} 条，无模型标签跳过 {skipped_no_model}，变更 {len(changed)} 条")
    print(f"  已复核 L1 修复 {report['l1_fixed_on_reviewed']}，回退 {report['l1_broken_on_reviewed']}")
    if l1_delta:
        print("  L1 迁移 Top5:")
        for k, v in l1_delta.most_common(5):
            print(f"    {k}: {v}")
    print(f"[已保存] {state_path}")
    if not args.write and changed:
        print("确认无误后执行: python3 performance_evaluation/apply_post_rules_to_v3.py --write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
