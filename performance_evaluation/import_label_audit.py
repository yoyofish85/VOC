#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤 1/2：导入二次人工复核结果，写回 opinion 表。

用法：
  python3 performance_evaluation/import_label_audit.py --csv exports/ambiguous_xxx.csv --dry-run
  python3 performance_evaluation/import_label_audit.py --csv exports/ambiguous_xxx.csv

CSV 要求：
  - 必填：舆情编号、复核后一级
  - 复核后二级：产品质量/服务/体验需求必填；「非问题」可留空（写库时二级为空）
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PERF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PERF_DIR))
sys.path.insert(0, str(PERF_DIR.parent / "label_project"))

from label_audit_common import (  # noqa: E402
    DEFAULT_DB,
    IMPORT_ALIASES,
    audit_note_append,
    connect_ro,
    connect_rw,
)
from taxonomy_normalize import CANONICAL_L1_LABELS, canonicalize_l1_label, is_non_issue_l1  # noqa: E402

STATE_DIR = PERF_DIR / "state"

_L2_ALIAS: Dict[str, str] = {
    "车端充电": "车端充电问题",
    "充电问题": "车端充电问题",
    "LFC": "LFC问题",
    "lfc": "LFC问题",
    "售后": "售后服务问题",
    "售后问题": "售后服务问题",
    "销售": "销售服务问题",
    "销售问题": "销售服务问题",
    "交付": "交付问题",
    "座舱": "座舱问题",
    "咨询表扬": "咨询与表扬",
    "咨询": "咨询与表扬",
    "其他": "其他非问题",
    "其他非问题类": "其他非问题",
    "故障通用": "故障-通用",
    "故障": "故障-通用",
}


def _normalize_header(name: str) -> str:
    return (name or "").strip().lstrip("\ufeff")


def _map_row(raw: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in raw.items():
        key = _normalize_header(str(k))
        if key in IMPORT_ALIASES:
            key = IMPORT_ALIASES[key]
        out[key] = "" if v is None else str(v).strip()
    return out


def _load_l2_whitelist() -> Dict[str, List[str]]:
    path = PERF_DIR.parent / "label_project" / "gold_l2_whitelist_v3.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _nearest_l2(candidate: str, allowed: List[str]) -> Optional[str]:
    c = (candidate or "").strip()
    if not c or not allowed:
        return None
    if c in allowed:
        return c
    if c in _L2_ALIAS and _L2_ALIAS[c] in allowed:
        return _L2_ALIAS[c]
    pref = [a for a in allowed if a.startswith(c) or c.startswith(a)]
    if len(pref) == 1:
        return pref[0]
    contain = [a for a in allowed if c in a or a in c]
    if len(contain) == 1:
        return contain[0]
    return None


def normalize_audit_labels(
    l1: str, l2: str, l2_map: Dict[str, List[str]]
) -> Tuple[bool, str, str, str, Optional[str]]:
    """返回 (ok, msg, norm_l1, norm_l2, auto_hint)。"""
    l1c = canonicalize_l1_label(l1)
    if l1c not in CANONICAL_L1_LABELS:
        return False, f"一级无法规范为四类: {l1!r}", l1, l2, None

    l2s = (l2 or "").strip()
    hint: Optional[str] = None
    if not l2s and is_non_issue_l1(l1c):
        return True, "", l1c, "", hint
    if not l2s:
        return False, "复核后二级为空（业务三类必填；非问题可留空）", l1c, l2s, hint

    l2s = _L2_ALIAS.get(l2s, l2s)
    allowed = l2_map.get(l1c) or []
    if allowed and l2s not in allowed:
        fixed = _nearest_l2(l2s, allowed)
        if fixed:
            hint = f"二级 {l2s!r} 已规范为 {fixed!r}"
            l2s = fixed
        else:
            opts = "、".join(allowed[:8]) + ("…" if len(allowed) > 8 else "")
            return False, f"二级 {l2s!r} 不在 {l1c} 白名单内（可选：{opts}）", l1c, l2s, hint

    return True, "", l1c, l2s, hint


def read_audit_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV 无表头")
        return [_map_row(dict(r)) for r in reader]


def main() -> int:
    parser = argparse.ArgumentParser(description="导入二次人工复核 CSV")
    parser.add_argument("--csv", type=Path, required=True, help="复核完成的 CSV 路径")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 路径")
    parser.add_argument("--dry-run", action="store_true", help="只校验与预览，不写库")
    parser.add_argument("--skip-invalid", action="store_true", help="跳过校验失败行")
    args = parser.parse_args()

    csv_path = args.csv.resolve()
    if not csv_path.is_file():
        print(f"[错误] CSV 不存在: {csv_path}", file=sys.stderr)
        return 1

    rows = read_audit_csv(csv_path)
    l2_map = _load_l2_whitelist()

    to_apply: List[Dict[str, str]] = []
    skipped_empty = 0
    invalid: List[str] = []
    normalized_hints: List[str] = []

    for i, row in enumerate(rows, start=2):
        oid = row.get("舆情编号", "")
        new_l1 = row.get("复核后一级", "")
        new_l2 = row.get("复核后二级", "")
        if not oid:
            continue
        if not new_l1 and not new_l2:
            skipped_empty += 1
            continue
        if not new_l1 and new_l2:
            invalid.append(f"行{i} 舆情编号={oid}: 已填二级但未填一级")
            continue

        ok, msg, l1c, l2s, hint = normalize_audit_labels(new_l1, new_l2, l2_map)
        if hint:
            normalized_hints.append(f"行{i} {oid}: {hint}")
        row = dict(row)
        row["复核后一级"] = l1c
        row["复核后二级"] = l2s
        if not ok:
            invalid.append(f"行{i} 舆情编号={oid}: {msg}")
            if not args.skip_invalid:
                continue
        to_apply.append(row)

    if invalid and not args.skip_invalid:
        print("[错误] 存在校验失败行（可用 --skip-invalid 跳过）:", file=sys.stderr)
        for x in invalid[:20]:
            print(f"  {x}", file=sys.stderr)
        if len(invalid) > 20:
            print(f"  ... 共 {len(invalid)} 条", file=sys.stderr)
        return 1

    if not to_apply:
        print("[提示] 没有可导入行")
        return 0

    conn_ro = connect_ro(args.db)
    try:
        existing = {
            str(r["opinion_id"]): r
            for r in conn_ro.execute(
                "SELECT opinion_id, review_l1, review_l2, review_note FROM opinion"
            ).fetchall()
        }
    finally:
        conn_ro.close()

    changes: List[Dict[str, Any]] = []
    missing_ids: List[str] = []

    for row in to_apply:
        oid = row["舆情编号"]
        if oid not in existing:
            missing_ids.append(oid)
            continue
        old = existing[oid]
        new_l1 = row["复核后一级"]
        new_l2 = row["复核后二级"]
        old_l1 = str(old["review_l1"] or "").strip()
        old_l2 = str(old["review_l2"] or "").strip()
        if old_l1 == new_l1 and old_l2 == new_l2:
            continue
        changes.append(
            {
                "opinion_id": oid,
                "old_l1": old_l1,
                "old_l2": old_l2,
                "new_l1": new_l1,
                "new_l2": new_l2,
                "note": audit_note_append(
                    old["review_note"],
                    row.get("复核人", ""),
                    row.get("复核备注", ""),
                ),
            }
        )

    print("=" * 60)
    print(" 二次人工复核结果导入" + ("（DRY-RUN）" if args.dry_run else ""))
    print("=" * 60)
    print(f"CSV 总行：     {len(rows)}")
    print(f"待导入行：     {len(to_apply)}（跳过空复核 {skipped_empty}）")
    print(f"实际变更：     {len(changes)}")
    print(f"库中不存在：   {len(missing_ids)}")
    if invalid:
        print(f"校验警告：     {len(invalid)}")
    if normalized_hints:
        print(f"自动规范：     {len(normalized_hints)}")
    print("")

    for c in changes[:15]:
        print(f"  {c['opinion_id']}: {c['old_l1']}/{c['old_l2']} → {c['new_l1']}/{c['new_l2']}")
    if len(changes) > 15:
        print(f"  ... 另有 {len(changes) - 15} 条")
    if normalized_hints:
        print("\n【自动规范】")
        for h in normalized_hints[:10]:
            print(f"  {h}")
        if len(normalized_hints) > 10:
            print(f"  ... 另有 {len(normalized_hints) - 10} 条")

    if args.dry_run:
        print("\n[DRY-RUN] 未写库。确认无误后去掉 --dry-run 再执行。")
        return 0

    if not changes:
        print("\n[完成] 无标签变更需要写库。")
        return 0

    conn = connect_rw(args.db)
    try:
        now = datetime.now().isoformat(timespec="seconds")
        for c in changes:
            conn.execute(
                """
                UPDATE opinion
                SET review_status = 1,
                    review_l1 = ?,
                    review_l2 = ?,
                    review_note = ?,
                    reviewed_at = ?
                WHERE opinion_id = ?
                """,
                (c["new_l1"], c["new_l2"], c["note"], now, c["opinion_id"]),
            )
        conn.commit()
    finally:
        conn.close()

    report_path = STATE_DIR / "last_label_audit_import.json"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "imported_at": datetime.now().isoformat(timespec="seconds"),
                "csv": str(csv_path),
                "changed_count": len(changes),
                "changed_ids": [c["opinion_id"] for c in changes],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\n[已写库] 更新 {len(changes)} 条")
    print(f"[报告]   {report_path}")
    print("\n【下一步】python3 performance_evaluation/evaluate_accuracy.py")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
