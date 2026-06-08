#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进化闭环 MVP · 周度报告（P0）。

聚合 KPI、P1 三池、规则可修估算、微调触发进度与行动建议；只读 DB，不写业务库。

用法（建议每周五）：
  python3 performance_evaluation/weekly_evolution_report.py
  python3 performance_evaluation/weekly_evolution_report.py --days 7 --db path/to/opinion_review.db

依赖：evaluate_accuracy、eval_p1_subset（规则重放）；基准见 state/evolution_baseline.json。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
STATE_DIR = PERF_DIR / "state"
REPORTS_DIR = PERF_DIR / "reports"
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"
BASELINE_PATH = STATE_DIR / "evolution_baseline.json"
WEEKLY_LAST_PATH = STATE_DIR / "evolution_weekly_last.json"
FINETUNE_STATE_PATH = STATE_DIR / "finetune_trigger.json"

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
sys.path.insert(0, str(PROJECT_ROOT / "src" / "backend"))
sys.path.insert(0, str(PERF_DIR))

from eval_p1_subset import (  # noqa: E402
    classify_subset,
    load_reviewed,
    replay_rules,
)
from evaluate_accuracy import (  # noqa: E402
    _connect_readonly,
    count_all_reviewed,
    evaluate,
    model_labels,
)
from taxonomy_normalize import canonicalize_l1_label  # noqa: E402
from time_utils import cutoff_from_since_days, row_review_time  # noqa: E402


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_baseline() -> Dict[str, Any]:
    if BASELINE_PATH.is_file():
        data = _load_json(BASELINE_PATH)
        if data:
            return data
    matches = sorted(STATE_DIR.glob("evolution_baseline_week0_*.json"), reverse=True)
    if matches:
        data = _load_json(matches[0])
        if data:
            return data
    return {}


def _fetch_review_timestamps(conn) -> List[Tuple[str, Optional[datetime], str, str]]:
    """(opinion_id, review_time, human_l1, model_l1)"""
    rows = conn.execute(
        """
        SELECT opinion_id, reviewed_at, create_time, review_l1,
               v3_label_meta, model_class, model_keyword
        FROM opinion WHERE review_status = 1
        """
    ).fetchall()
    out: List[Tuple[str, Optional[datetime], str, str]] = []
    for row in rows:
        hr1 = str(row["review_l1"] or "").strip()
        if not hr1:
            continue
        m1, _ = model_labels(row)
        if not m1:
            continue
        ts = row_review_time(row["reviewed_at"], row["create_time"])
        out.append(
            (
                str(row["opinion_id"] or ""),
                ts,
                canonicalize_l1_label(hr1),
                canonicalize_l1_label(m1),
            )
        )
    return out


def _period_stats(
    stamped: List[Tuple[str, Optional[datetime], str, str]], cutoff: datetime
) -> Dict[str, Any]:
    in_period = [(oid, h, m) for oid, ts, h, m in stamped if ts and ts >= cutoff]
    wrong = [(h, m) for _, h, m in in_period if h != m]
    return {
        "new_reviewed": len(in_period),
        "new_l1_wrong": len(wrong),
        "top_l1_wrong": Counter(f"{m} → {h}" for h, m in wrong).most_common(5),
    }


def _fixable_counts(rows: List[Dict[str, Any]]) -> Tuple[int, int, int, int, int, int]:
    primary, regression, other = classify_subset(rows)
    ep, er, eo = replay_rules(primary), replay_rules(regression), replay_rules(other)

    def _f(pool, enriched):
        return sum(
            1
            for r in enriched
            if r["model_l1"] != r["human_l1"] and r["replay_l1"] == r["human_l1"]
        )

    def _b(pool, enriched):
        return sum(
            1
            for r in enriched
            if r["model_l1"] == r["human_l1"] and r["replay_l1"] != r["human_l1"]
        )

    rf, of_ = _f(regression, er), _f(other, eo)
    rb, ob = _b(regression, er), _b(other, eo)
    pf = _f(primary, ep)
    return len(regression), len(other), len(primary), rf + of_, rf, of_


def _regression_unfixed_diag(regression_enriched: List[Dict[str, Any]], top: int = 8) -> List[Tuple[str, int]]:
    unfixed = [r for r in regression_enriched if r["replay_l1"] != r["human_l1"]]
    if not unfixed:
        return []
    try:
        from qwen_ollama import explain_positive_capture_block

        diag = Counter(
            explain_positive_capture_block(r["text"]).split(";")[0] for r in unfixed
        )
        return diag.most_common(top)
    except ImportError:
        return []


def _finetune_progress(
    baseline: Dict[str, Any],
    cumulative_reviewed: int,
    mismatch_total: int,
    fixable_total: int,
    consecutive_low_fixable: int,
) -> Dict[str, Any]:
    trig = baseline.get("finetune_trigger") or {}
    need_rev = int(trig.get("new_reviewed_since_baseline") or 3000)
    need_mis = int(trig.get("l1_mismatch_since_baseline") or 800)
    need_weeks = int(trig.get("consecutive_weeks_fixable_lt5") or 2)
    base_rev = int(baseline.get("cumulative_reviewed") or 0)
    ft_state = _load_json(FINETUNE_STATE_PATH) or {}
    if "baseline_l1_mismatch" in ft_state:
        base_mis = int(ft_state["baseline_l1_mismatch"])
    else:
        base_mis = int(baseline.get("l1_wrong") or 0)

    delta_rev = max(0, cumulative_reviewed - base_rev)
    delta_mis = max(0, mismatch_total - base_mis)

    reasons: List[str] = []
    if delta_rev >= need_rev:
        reasons.append(f"新增复核 {delta_rev} ≥ {need_rev}")
    if delta_mis >= need_mis:
        reasons.append(f"人机 L1 不一致增量 {delta_mis} ≥ {need_mis}")
    if fixable_total < 5 and consecutive_low_fixable >= need_weeks:
        reasons.append(f"连续 {consecutive_low_fixable} 周 fixable < 5")

    return {
        "delta_reviewed": delta_rev,
        "need_reviewed": need_rev,
        "delta_mismatch": delta_mis,
        "need_mismatch": need_mis,
        "consecutive_weeks_fixable_lt5": consecutive_low_fixable,
        "need_weeks_fixable_lt5": need_weeks,
        "ready": bool(reasons),
        "ready_reasons": reasons,
    }


def _suggest_actions(
    rf: int,
    of_: int,
    fixable_total: int,
    finetune: Dict[str, Any],
    regression_pool: int,
) -> Tuple[str, str, str]:
    rule = "本周无规则 patch 收益（fixable=0）；暂停扩 regex，避免 praise/neutral_query 回退。"
    if rf > 0:
        rule = (
            f"优先回归 patch：eval_p1_subset --export-regression-fixable auto → "
            f"patch_p1_regression_v3.py（可修 {rf} 条）"
        )
    elif of_ > 0:
        rule = (
            f"优先跨类 patch：--export-other-fixable auto → "
            f"patch_p1_other_l1_v3.py（可修 {of_} 条）"
        )
    elif fixable_total == 0 and regression_pool > 0:
        rule = (
            "规则轨见底；若攻回归池，需逐条门禁（Top：neutral_query/praise），ROI 低。"
        )

    if finetune.get("ready"):
        finetune_s = (
            "微调触发条件已满足（"
            + "；".join(finetune.get("ready_reasons") or [])
            + "）→ 运行 export_finetune_from_db.py（待实现）并 holdout A/B。"
        )
    else:
        finetune_s = (
            f"微调未触发：复核 +{finetune['delta_reviewed']}/{finetune['need_reviewed']}，"
            f"不一致 +{finetune['delta_mismatch']}/{finetune['need_mismatch']}。"
        )

    hold = "维持复核节奏（目标 ~200 条/天）；积累金标，不改规则/模型。"
    if fixable_total == 0 and not finetune.get("ready"):
        hold = "规则与微调均未就绪：专注复核与错误归因，等待样本量或触发阈值。"

    return rule, finetune_s, hold


def _pp_delta(cur: float, prev: Optional[float]) -> str:
    if prev is None:
        return "（无上周快照）"
    return f"{(cur - prev) * 100:+.2f}pp"


def format_weekly_report(ctx: Dict[str, Any]) -> str:
    w = 62
    lines: List[str] = []

    def bar(title: str) -> None:
        lines.append("=" * w)
        lines.append(f" {title}")
        lines.append("=" * w)

    bar("VOC 进化闭环 · 周度报告")
    lines.append(f"生成时间：{ctx['generated_at']}")
    lines.append(f"统计窗口：近 {ctx['days']} 天")
    lines.append(f"数据库：{ctx['db_path']}")
    lines.append(f"规则版本：{ctx.get('rule_version', '—')}")
    lines.append("")

    ev = ctx["eval"]
    lines.append("【1. KPI】")
    lines.append(
        f"  L1：{ev['l1_accuracy'] * 100:.2f}%（{ev['l1_correct']}/{ev['l1_denominator']}）"
        f"  较上周 {_pp_delta(ev['l1_accuracy'], ctx.get('last_l1'))}"
    )
    lines.append(
        f"  L2：{ev['l2_accuracy'] * 100:.2f}%（{ev['l2_correct']}/{ev['l2_denominator']}）"
        f"  较上周 {_pp_delta(ev['l2_accuracy'], ctx.get('last_l2'))}"
    )
    b0 = ctx.get("baseline") or {}
    if b0:
        d0_l1 = ev["l1_accuracy"] * 100 - float(b0.get("l1_accuracy", 0)) * 100
        lines.append(
            f"  较 Week0（{b0.get('locked_at', '—')}）：L1 {d0_l1:+.2f}pp"
            f"（目标 Month1 ≥ {float(b0.get('mvp_month1_target_l1_pct', 88)):.0f}%）"
        )
    lines.append(
        f"  全库已复核：{ctx['cumulative_reviewed']} 条"
        f"（较上周 +{ctx.get('delta_reviewed_since_last', 0)}）"
    )
    lines.append("")

    ps = ctx["period"]
    lines.append(f"【2. 近 {ctx['days']} 天新增】")
    lines.append(f"  新增已复核：{ps['new_reviewed']} 条")
    lines.append(f"  其中 L1 错误：{ps['new_l1_wrong']} 条")
    lines.append("  新增 L1 错误 Top5（模型 → 人工）：")
    for i, (k, n) in enumerate(ps["top_l1_wrong"], 1):
        lines.append(f"    {i}. {k} ({n}条)")
    if not ps["top_l1_wrong"]:
        lines.append("    （无）")
    lines.append("")

    lines.append("【3. P1 三池（当前库内 v3）】")
    lines.append(f"  回归（人工=业务，v3=非问题）：{ctx['pool_regression']} 条")
    lines.append(f"  主靶（人工=非问题，v3=业务）：{ctx['pool_primary']} 条")
    lines.append(f"  其他 L1 错：{ctx['pool_other']} 条")
    lines.append("")

    lines.append("【4. 规则可修估算（离线重放）】")
    lines.append(f"  合计可修：{ctx['fixable_total']} 条（回归 {ctx['fixable_regression']} + 跨类 {ctx['fixable_other']}）")
    lines.append(f"  主靶可修（参考）：{ctx['fixable_primary']} 条")
    if ctx.get("regression_diag"):
        lines.append("  回归未拉回·捕获诊断 Top：")
        for k, n in ctx["regression_diag"]:
            lines.append(f"    {k}: {n}")
    lines.append("")

    ft = ctx["finetune"]
    lines.append("【5. 微调触发进度】")
    lines.append(
        f"  复核增量：{ft['delta_reviewed']} / {ft['need_reviewed']}"
    )
    lines.append(
        f"  人机 L1 不一致增量：{ft['delta_mismatch']} / {ft['need_mismatch']}"
    )
    lines.append(
        f"  连续 fixable<5 周数：{ft['consecutive_weeks_fixable_lt5']} / {ft['need_weeks_fixable_lt5']}"
    )
    if ft.get("ready"):
        lines.append(f"  状态：**可启动微调**（{'; '.join(ft['ready_reasons'])}）")
    else:
        lines.append("  状态：未触发")
    lines.append("")

    lines.append("【6. 建议动作】")
    lines.append(f"  规则：{ctx['action_rule']}")
    lines.append(f"  微调：{ctx['action_finetune']}")
    lines.append(f"  暂缓：{ctx['action_hold']}")
    lines.append("")
    lines.append("【下次命令】")
    lines.append("  python3 performance_evaluation/evaluate_accuracy.py")
    lines.append("  python3 performance_evaluation/eval_p1_subset.py --compare \\")
    lines.append("    --export-regression-fixable auto --export-other-fixable auto")
    lines.append("  python3 performance_evaluation/weekly_evolution_report.py")
    lines.append("=" * w)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="进化闭环周度报告（只读）")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--days", type=int, default=7, help="近 N 天窗口（默认 7）")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Week0 基线路径",
    )
    args = parser.parse_args()

    baseline = _load_json(args.baseline) if args.baseline.is_file() else _load_baseline()
    last_weekly = _load_json(WEEKLY_LAST_PATH)
    cutoff = datetime.now() - timedelta(days=max(1, args.days))

    conn = _connect_readonly(args.db)
    try:
        cumulative = count_all_reviewed(conn)
        ev = evaluate(conn)
        stamped = _fetch_review_timestamps(conn)
        period = _period_stats(stamped, cutoff)
        rows = load_reviewed(conn)
    finally:
        conn.close()

    reg_n, oth_n, pri_n, fix_total, fix_reg, fix_oth = _fixable_counts(rows)
    _, regression, _ = classify_subset(rows)
    reg_enriched = replay_rules(regression)
    reg_diag = _regression_unfixed_diag(reg_enriched)
    primary, _, _ = classify_subset(rows)
    fix_pri = sum(
        1
        for r in replay_rules(primary)
        if r["model_l1"] != r["human_l1"] and r["replay_l1"] == r["human_l1"]
    )

    mismatch_total = sum(1 for r in rows if r["human_l1"] != r["model_l1"])

    prev_low = int((last_weekly or {}).get("consecutive_weeks_fixable_lt5") or 0)
    if fix_total < 5:
        consecutive_low = prev_low + 1 if last_weekly else 1
    else:
        consecutive_low = 0

    finetune = _finetune_progress(
        baseline, cumulative, mismatch_total, fix_total, consecutive_low
    )

    rule_a, fin_a, hold_a = _suggest_actions(
        fix_reg, fix_oth, fix_total, finetune, reg_n
    )

    rule_version = str(baseline.get("rule_version") or "—")
    qpath = PROJECT_ROOT / "src" / "backend" / "qwen_ollama.py"
    try:
        if qpath.is_file():
            m = re.search(
                r'_cache_key\("([^"]+)"',
                qpath.read_text(encoding="utf-8", errors="ignore"),
            )
            if m:
                rule_version = m.group(1)
    except OSError:
        pass

    ctx: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": args.days,
        "db_path": str(args.db.resolve()),
        "rule_version": rule_version,
        "eval": ev,
        "cumulative_reviewed": cumulative,
        "last_l1": (last_weekly or {}).get("l1_accuracy"),
        "last_l2": (last_weekly or {}).get("l2_accuracy"),
        "delta_reviewed_since_last": max(
            0,
            cumulative - int((last_weekly or {}).get("cumulative_reviewed") or cumulative),
        ),
        "baseline": baseline,
        "period": period,
        "pool_regression": reg_n,
        "pool_primary": pri_n,
        "pool_other": oth_n,
        "fixable_total": fix_total,
        "fixable_regression": fix_reg,
        "fixable_other": fix_oth,
        "fixable_primary": fix_pri,
        "regression_diag": reg_diag,
        "finetune": finetune,
        "action_rule": rule_a,
        "action_finetune": fin_a,
        "action_hold": hold_a,
    }

    text = format_weekly_report(ctx)
    print(text)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = REPORTS_DIR / f"{ts}_weekly_evolution.txt"
    report_path.write_text(text, encoding="utf-8")

    snapshot = {
        "generated_at": ctx["generated_at"],
        "l1_accuracy": round(ev["l1_accuracy"], 6),
        "l2_accuracy": round(ev["l2_accuracy"], 6),
        "l1_correct": ev["l1_correct"],
        "l1_denominator": ev["l1_denominator"],
        "cumulative_reviewed": cumulative,
        "fixable_total": fix_total,
        "consecutive_weeks_fixable_lt5": consecutive_low,
        "pool_regression": reg_n,
        "pool_other": oth_n,
        "period_new_reviewed": period["new_reviewed"],
    }
    WEEKLY_LAST_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n[已保存] {report_path}")
    print(f"[已更新] {WEEKLY_LAST_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        raise SystemExit(1)
