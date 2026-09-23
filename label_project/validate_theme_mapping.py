#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验汇报主题 ↔ 定位 L3 映射。

用法：
  python3 -m label_project.validate_theme_mapping
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def _standard_paths(standard: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for l1, l2m in (standard.get("by_l1_l2") or {}).items():
        for l2, items in (l2m or {}).items():
            for l3 in items or []:
                paths.append(f"{l1}/{l2}/{l3}")
    return paths


def build_theme_l3_index(mapping: Dict[str, Any]) -> Dict[str, List[str]]:
    index: Dict[str, List[str]] = defaultdict(list)
    for path, row in (mapping.get("l3_mapping") or {}).items():
        tid = row.get("primary_theme_id")
        if tid:
            index[str(tid)].append(path)
    return {k: sorted(v) for k, v in sorted(index.items())}


def validate_theme_mapping(
    mapping: Dict[str, Any],
    standard: Dict[str, Any],
) -> Dict[str, Any]:
    themes = mapping.get("themes") or {}
    l3_mapping = mapping.get("l3_mapping") or {}
    std_paths = _standard_paths(standard)
    std_set = set(std_paths)

    missing_paths = sorted(std_set - set(l3_mapping.keys()))
    invalid_paths = sorted(set(l3_mapping.keys()) - std_set)

    unknown_primary: List[str] = []
    duplicate_primary: List[str] = []
    seen_primary_path = set()
    for path, row in l3_mapping.items():
        if path in seen_primary_path:
            duplicate_primary.append(path)
        seen_primary_path.add(path)
        tid = row.get("primary_theme_id")
        if not tid or tid not in themes:
            unknown_primary.append(path)
        secs = row.get("secondary_theme_ids") or []
        if tid in secs:
            duplicate_primary.append(path)

    formal_themes = {k: v for k, v in themes.items() if k != "T99"}
    coverage = 0.0 if not std_paths else (len(std_paths) - len(missing_paths)) / len(std_paths)

    ok = (
        not missing_paths
        and not invalid_paths
        and not unknown_primary
        and not duplicate_primary
        and coverage == 1.0
        and 30 <= len(formal_themes) <= 45
        and "T99" in themes
    )
    return {
        "ok": ok,
        "path_count": len(std_paths),
        "mapped_count": len(std_paths) - len(missing_paths),
        "coverage": coverage,
        "theme_count_formal": len(formal_themes),
        "missing_paths": missing_paths,
        "invalid_paths": invalid_paths,
        "unknown_primary_theme_ids": unknown_primary,
        "duplicate_primary_assignments": duplicate_primary,
        "theme_l3_index": build_theme_l3_index(mapping),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="校验汇报主题映射")
    root = Path(__file__).resolve().parent
    parser.add_argument("--standard", type=Path, default=root / "gold_l3_standard_v1.json")
    parser.add_argument("--mapping", type=Path, default=root / "l3_report_theme_mapping_v1.json")
    parser.add_argument("--export-json", type=Path, default=None)
    parser.add_argument("--export-checklist-md", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    standard = json.loads(args.standard.read_text(encoding="utf-8"))
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    report = validate_theme_mapping(mapping, standard)
    print(json.dumps({k: v for k, v in report.items() if k != "theme_l3_index"}, ensure_ascii=False, indent=2))

    if args.export_json:
        args.export_json.parent.mkdir(parents=True, exist_ok=True)
        args.export_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[已导出] {args.export_json}")

    if args.export_checklist_md:
        lines = [
            "# 汇报主题 — 定位 L3 对照清单（v1）",
            "",
            f"- 正式主题数：{report['theme_count_formal']}",
            f"- 定位 L3 覆盖：{report['mapped_count']}/{report['path_count']} ({report['coverage']:.0%})",
            "",
        ]
        index = report["theme_l3_index"]
        themes = mapping.get("themes") or {}
        for tid in sorted(themes.keys(), key=lambda x: (x == "T99", x)):
            theme = themes[tid]
            paths = index.get(tid, [])
            lines.append(f"## {tid} {theme.get('name')}（{len(paths)}）")
            lines.append("")
            lines.append(f"- L1：{theme.get('l1')}")
            lines.append(f"- 责任：{theme.get('owner')}")
            lines.append(f"- 定义：{theme.get('definition')}")
            lines.append(f"- 包含：{theme.get('includes')}")
            lines.append(f"- 排除：{theme.get('excludes')}")
            lines.append("")
            for p in paths:
                lines.append(f"- `{p}`")
            lines.append("")
        args.export_checklist_md.parent.mkdir(parents=True, exist_ok=True)
        args.export_checklist_md.write_text("\n".join(lines), encoding="utf-8")
        print(f"[已导出] {args.export_checklist_md}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
