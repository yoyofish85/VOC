#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M4-B ROI 对比：回归 217 vs 其他 L1 错 92，输出 fixable 估算。"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
sys.path.insert(0, str(PROJECT_ROOT / "src" / "backend"))
sys.path.insert(0, str(PERF_DIR))

from eval_p1_subset import classify_subset, load_reviewed, replay_rules  # noqa: E402
from evaluate_accuracy import _connect_readonly  # noqa: E402


def _fixable(enriched, baseline="model_l1"):
    return [
        r
        for r in enriched
        if r[baseline] != r["human_l1"] and r["replay_l1"] == r["human_l1"]
    ]


def _broken(enriched, baseline="model_l1"):
    return [
        r
        for r in enriched
        if r[baseline] == r["human_l1"] and r["replay_l1"] != r["human_l1"]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="M4-B ROI 对比")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    conn = _connect_readonly(args.db)
    try:
        rows = load_reviewed(conn)
    finally:
        conn.close()

    primary, regression, other = classify_subset(rows)
    ep, er, eo = replay_rules(primary), replay_rules(regression), replay_rules(other)
    n = len(rows)

    pf, pb = _fixable(ep), _broken(ep)
    rf, rb = _fixable(er), _broken(er)
    of_, ob = _fixable(eo), _broken(eo)

    print("=" * 60)
    print(" M4-B ROI 对比（当前规则离线重放）")
    print("=" * 60)
    print(f"全库 L1 分母：{n}")
    print()
    print(f"{'轨道':<28} {'池大小':>6} {'可修':>6} {'回退':>6} {'估算Δpp':>8}")
    print("-" * 60)
    for label, pool, fix, brk in [
        ("M4-B1 回归 guard 拉回", len(regression), len(rf), len(rb)),
        ("M4-B2 其他 L1 跨类纠偏", len(other), len(of_), len(ob)),
        ("主靶残余（参考）", len(primary), len(pf), len(pb)),
    ]:
        print(f"{label:<28} {pool:>6} {fix:>6} {brk:>6} {fix / n * 100:>+7.2f}pp")

    if rf:
        pat = Counter(f"{r['human_l1']}" for r in rf)
        print("\n回归可修·人工一级分布:", dict(pat.most_common()))
        flags = Counter()
        for r in rf:
            for k, v in (r.get("rule_flags") or {}).items():
                if v:
                    flags[k] += 1
        print("回归可修·规则标记:", dict(flags.most_common()))

    if of_:
        pat = Counter(f"{r['model_l1']}->{r['human_l1']}" for r in of_)
        print("\n其他 L1 可修·错误模式:", dict(pat.most_common(8)))

    reg_bad = [r for r in er if r["replay_l1"] != r["human_l1"]]
    if reg_bad:
        try:
            from qwen_ollama import explain_positive_capture_block

            diag = Counter(
                explain_positive_capture_block(r["text"]).split(";")[0] for r in reg_bad
            )
            print(f"\n回归未拉回 {len(reg_bad)} 条·若从业务类出发的捕获诊断 Top8:")
            for k, v in diag.most_common(8):
                print(f"  {k}: {v}")
        except ImportError:
            pass

    winner = "M4-B1 回归" if len(rf) >= len(of_) else "M4-B2 跨类"
    print(f"\n建议优先：{winner}（当前规则下可修 {max(len(rf), len(of_))} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
