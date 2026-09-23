#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验定位 L3 标准层（产品质量类 + 服务类）。

用法：
  python3 -m label_project.validate_l3_standard
  python3 -m label_project.validate_l3_standard --standard label_project/gold_l3_standard_v1.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

EXPECTED_STANDARD_COUNT = 214
SCOPE_L1 = ("产品质量类", "服务类")

# 清单 L2 → 现网白名单 L2（本期不改白名单，只登记别名）
DEFAULT_L2_ALIASES: Dict[str, Dict[str, str]] = {
    "外饰问题": {"maps_to": "故障-通用", "reason": "清单新增 L2，暂挂故障-通用"},
    "故障告警": {"maps_to": "故障-通用", "reason": "清单新增 L2，暂挂故障-通用"},
    "灯类故障": {"maps_to": "电子电器问题", "reason": "清单新增 L2，暂挂电子电器问题"},
    "销售承诺未能兑现": {"maps_to": "销售服务问题", "reason": "清单新增 L2，暂挂销售服务问题"},
}


def _iter_paths(standard: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    paths: List[Tuple[str, str, str]] = []
    by = standard.get("by_l1_l2") or {}
    for l1, l2m in by.items():
        if not isinstance(l2m, dict):
            continue
        for l2, items in l2m.items():
            if not isinstance(items, list):
                continue
            for l3 in items:
                paths.append((str(l1), str(l2), str(l3).strip()))
    return paths


def validate_l3_standard(
    standard: Dict[str, Any],
    *,
    whitelist: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    paths = _iter_paths(standard)
    path_keys = [f"{a}/{b}/{c}" for a, b, c in paths]
    dup_counter = Counter(path_keys)
    duplicate_paths = sorted([p for p, n in dup_counter.items() if n > 1])

    l3_parents: Dict[str, set] = defaultdict(set)
    for l1, l2, l3 in paths:
        l3_parents[l3].add(f"{l1}/{l2}")
    multi_parent_l3 = sorted(
        [{"l3": l3, "parents": sorted(parents)} for l3, parents in l3_parents.items() if len(parents) > 1],
        key=lambda x: x["l3"],
    )

    l1_counts = Counter(l1 for l1, _, _ in paths)
    empty_l3 = [f"{a}/{b}" for a, b, c in paths if not c]

    l2_aliases = standard.get("l2_aliases") or {}
    unmapped_l2: List[str] = []
    if whitelist is not None:
        sheet_l2 = sorted({l2 for _, l2, _ in paths})
        for l2 in sheet_l2:
            in_wl = any(l2 in (whitelist.get(l1) or []) for l1 in SCOPE_L1)
            if not in_wl and l2 not in l2_aliases:
                unmapped_l2.append(l2)

    ok = (
        len(paths) == EXPECTED_STANDARD_COUNT
        and not duplicate_paths
        and not multi_parent_l3
        and not empty_l3
        and not unmapped_l2
        and set(l1_counts) <= set(SCOPE_L1)
        and l1_counts.get("服务类", 0) == 85
        and l1_counts.get("产品质量类", 0) == 129
    )
    return {
        "ok": ok,
        "path_count": len(paths),
        "unique_path_count": len(set(path_keys)),
        "duplicate_paths": duplicate_paths,
        "multi_parent_l3": multi_parent_l3,
        "empty_l3": empty_l3,
        "unmapped_l2": unmapped_l2,
        "l1_counts": dict(l1_counts),
        "l2_alias_count": len(l2_aliases),
    }


def build_validation_report(
    standard: Dict[str, Any],
    *,
    whitelist: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base = validate_l3_standard(standard, whitelist=whitelist)
    by = standard.get("by_l1_l2") or {}
    l2_counts: Dict[str, Dict[str, int]] = {}
    for l1, l2m in by.items():
        l2_counts[l1] = {l2: len(items) for l2, items in (l2m or {}).items()}
    return {
        **base,
        "version": (standard.get("_meta") or {}).get("version"),
        "status": (standard.get("_meta") or {}).get("status"),
        "l2_counts": l2_counts,
        "excluded": (standard.get("_meta") or {}).get("excluded") or [],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="校验定位 L3 标准层")
    root = Path(__file__).resolve().parent
    parser.add_argument(
        "--standard",
        type=Path,
        default=root / "gold_l3_standard_v1.json",
    )
    parser.add_argument(
        "--whitelist",
        type=Path,
        default=root / "gold_l2_whitelist_v3.json",
    )
    parser.add_argument(
        "--export-json",
        type=Path,
        default=None,
        help="写出机器可读校验报告",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    standard = json.loads(args.standard.read_text(encoding="utf-8"))
    whitelist = (
        json.loads(args.whitelist.read_text(encoding="utf-8"))
        if args.whitelist.is_file()
        else None
    )
    report = build_validation_report(standard, whitelist=whitelist)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.export_json:
        args.export_json.parent.mkdir(parents=True, exist_ok=True)
        args.export_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[已导出] {args.export_json}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
