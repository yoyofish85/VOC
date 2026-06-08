#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L1 已正确、L2 与人工不一致时，用当前规则重放 L2 写回 v3（不改一级）。

典型：r2 回归 patch 后 L1=服务类正确，但 L2 仍为「售后服务问题」，
回访样本应为「销售服务问题」。

用法：
  python3 performance_evaluation/patch_v3_l2_replay.py
  python3 performance_evaluation/patch_v3_l2_replay.py --write
  python3 performance_evaluation/evaluate_accuracy.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
STATE_DIR = PERF_DIR / "state"

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(PERF_DIR))

from evaluate_accuracy import _l2_eval_applicable, model_labels, normalize_l2  # noqa: E402
from eval_p1_subset import load_reviewed, replay_rules  # noqa: E402
from qwen_ollama import _pick_l2_after_remap, load_l2_whitelist  # noqa: E402
from taxonomy_normalize import canonicalize_l1_label  # noqa: E402
from v3_materialized import materialized_update_params  # noqa: E402


def _resolve_target_l2(
    human_l2: str,
    model_l2: str,
    replay_l2: str,
    model_l1: str,
    text: str,
    l2_map: dict,
) -> str:
    """L1 已正确时：优先全链 replay L2，否则用 _pick_l2_after_remap（回访映射等）。"""
    if replay_l2 and replay_l2 != model_l2 and replay_l2 == human_l2:
        return replay_l2
    mapped = normalize_l2(_pick_l2_after_remap(model_l1, text, l2_map))
    if mapped and mapped != model_l2 and mapped == human_l2:
        return mapped
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="L1 正确时规则重放 L2 写回 v3")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--write", action="store_true", help="实际写库（默认 dry-run）")
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db.resolve()))
    conn.row_factory = sqlite3.Row
    rows = load_reviewed(conn)
    by_id = {
        str(r["opinion_id"]): conn.execute(
            "SELECT id, opinion_id, v3_label_meta FROM opinion WHERE opinion_id = ?",
            (r["opinion_id"],),
        ).fetchone()
        for r in rows
    }
    conn.close()

    enriched = replay_rules(rows)
    l2_map = load_l2_whitelist()
    fixable: List[Dict[str, Any]] = []
    for r in enriched:
        human_l1 = r["human_l1"]
        human_l2 = normalize_l2(r["human_l2"])
        model_l1 = r["model_l1"]
        model_l2 = normalize_l2(r["model_l2"])
        replay_l1 = r["replay_l1"]
        replay_l2 = normalize_l2(r["replay_l2"])
        if not _l2_eval_applicable(human_l1, model_l1):
            continue
        if not human_l2:
            continue
        if model_l1 != human_l1 or replay_l1 != human_l1:
            continue
        if model_l2 == human_l2:
            continue
        target_l2 = _resolve_target_l2(
            human_l2, model_l2, replay_l2, model_l1, r["text"], l2_map
        )
        if not target_l2:
            continue
        fixable.append(
            {
                **r,
                "human_l2_norm": human_l2,
                "model_l2_norm": model_l2,
                "replay_l2_norm": target_l2,
            }
        )

    print(f"可写 L2 纠偏：{len(fixable)} 条")
    for r in fixable[:40]:
        print(
            f"  {r['opinion_id']}  {r['model_l2_norm']} -> {r['replay_l2_norm']}  "
            f"({r['text'][:48]})"
        )
    if len(fixable) > 40:
        print(f"  ... 共 {len(fixable)} 条")

    if not args.write:
        print("\n[dry-run] 加 --write 写库")
        return 0

    conn = sqlite3.connect(str(args.db.resolve()))
    updated = 0
    applied_at = datetime.now().isoformat(timespec="seconds")
    for r in fixable:
        row = by_id.get(r["opinion_id"])
        if not row:
            continue
        try:
            meta = json.loads(row["v3_label_meta"] or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["l1"] = r["model_l1"]
        meta["l2"] = r["replay_l2_norm"]
        meta["patch_v3_l2_replay_at"] = applied_at
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
        "at": applied_at,
        "updated": updated,
        "ids": [r["opinion_id"] for r in fixable],
    }
    out = STATE_DIR / f"patch_v3_l2_replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[已写库] {updated} 条  报告: {out}")
    print("验收: python3 performance_evaluation/evaluate_accuracy.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
