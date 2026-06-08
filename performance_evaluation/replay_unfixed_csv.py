#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对 eval_p1_subset 导出的未修复 CSV 做规则重放统计。"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
EXPORTS_DIR = PERF_DIR / "exports"
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "label_project"))

from qwen_ollama import apply_classification_post_rules, explain_positive_capture_block, load_l2_whitelist  # noqa: E402


def _resolve_csv_path(raw: Path) -> Path:
    """相对路径优先在 performance_evaluation/exports/ 下查找；latest 取最新 p1_primary_unfixed_*.csv。"""
    if str(raw).lower() in ("latest", "auto", "@latest"):
        candidates = sorted(
            EXPORTS_DIR.glob("p1_primary_unfixed_*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(
                f"找不到 CSV：{EXPORTS_DIR}/p1_primary_unfixed_*.csv\n"
                f"请先运行: python3 performance_evaluation/eval_p1_subset.py --compare --export-unfixed auto"
            )
        return candidates[0]
    if raw.is_absolute() and raw.is_file():
        return raw
    if raw.is_file():
        return raw.resolve()
    alt = EXPORTS_DIR / raw.name
    if alt.is_file():
        return alt
    raise FileNotFoundError(
        f"找不到 CSV：{raw}\n"
        f"  已尝试：{raw.resolve() if not raw.is_absolute() else raw}\n"
        f"  已尝试：{alt}\n"
        f"  请将文件放在 {EXPORTS_DIR}/ 或使用完整路径。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="对 eval_p1_subset 导出的未修复 CSV 做规则重放统计。"
    )
    parser.add_argument(
        "csv_path",
        type=Path,
        help="CSV 路径；可只写文件名、latest（取 exports 下最新 p1_primary_unfixed_*.csv）",
    )
    parser.add_argument("--diag", type=str, default="", help="只统计某类捕获诊断，如 no_capture_pattern")
    parser.add_argument(
        "--print-text",
        choices=("all", "still", "fixed"),
        nargs="?",
        const="all",
        help="输出原文：all=按 --diag 筛选全部；still/fixed=重放后仍错/已修复",
    )
    args = parser.parse_args()
    csv_path = _resolve_csv_path(args.csv_path)
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    if args.diag:
        rows = [r for r in rows if (r.get("捕获诊断") or "").split(";")[0] == args.diag]
    if args.print_text:
        l2_map = load_l2_whitelist()
        n_out = 0
        for r in rows:
            text = (r.get("原文") or "").strip()
            m1 = r.get("模型一级") or ""
            m2 = r.get("模型二级") or ""
            human = r.get("人工一级") or ""
            if not text:
                continue
            if args.print_text == "all":
                n_out += 1
                print(f"--- record {n_out} ---")
                print(f"{m1} {text}")
                continue
            nl1, _, _ = apply_classification_post_rules(text, m1, m2, l2_map)
            ok = nl1 == human
            if args.print_text == "still" and not ok:
                diag = explain_positive_capture_block(text).split(";")[0]
                n_out += 1
                print(f"--- record {n_out} [{diag}] {m1} ---")
                print(text)
            elif args.print_text == "fixed" and ok:
                n_out += 1
                print(f"--- record {n_out} ---")
                print(f"{m1} {text}")
        print(f"# total_records: {n_out}", file=sys.stderr)
        return 0
    l2_map = load_l2_whitelist()
    fixed = 0
    still = Counter()
    for r in rows:
        text = r.get("原文") or ""
        m1 = r.get("模型一级") or ""
        m2 = r.get("模型二级") or ""
        nl1, _, _ = apply_classification_post_rules(text, m1, m2, l2_map)
        human = r.get("人工一级") or ""
        if nl1 == human:
            fixed += 1
        else:
            still[explain_positive_capture_block(text).split(";")[0]] += 1
    print(f"样本 {len(rows)} 条，新修复 {fixed}，仍错 {len(rows)-fixed}")
    if still:
        print("仍错诊断 Top:")
        for k, v in still.most_common(10):
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
