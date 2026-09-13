#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目汇报：各阶段识别准确率汇总（只读 DB）。

阶段说明：
  A. 全库已复核 — 当前线上 v3 标签 vs 人工（evaluate_accuracy 口径）
  B. 近 N 天已复核 — 本周实测窗口
  C. 规则重放 dry-run — 同一批样本上「模型基线 vs 后规则重放」
  D. 匹配方式分布 — v3_match_type / match_type 占比
  E. 近 N 天 L1 错误模式 Top

用法（项目根或任意目录）：
  python3 performance_evaluation/report_project_stages.py
  python3 performance_evaluation/report_project_stages.py --days 7 --export-md exports/project_report_7d.md
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
EXPORTS_DIR = PERF_DIR / "exports"
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
sys.path.insert(0, str(PROJECT_ROOT / "src" / "backend"))
sys.path.insert(0, str(PERF_DIR))

from eval_recent_rule_candidate import compare_rows, fetch_rows, replay_rows  # noqa: E402
from evaluate_accuracy import (  # noqa: E402
    _connect_readonly,
    _l2_eval_applicable,
    count_all_reviewed,
    evaluate,
    model_labels,
    normalize_l2,
)
from taxonomy_normalize import canonicalize_l1_label  # noqa: E402
from time_utils import row_review_time  # noqa: E402


def _pct(ok: int, tot: int) -> str:
    if not tot:
        return "N/A"
    return f"{ok / tot * 100:.2f}%"


def _metrics_from_rows(rows: Sequence[sqlite3.Row]) -> Dict[str, Any]:
    l1_ok = l1_tot = l2_ok = l2_tot = 0
    err = Counter()
    mt = Counter()
    for row in rows:
        h1 = canonicalize_l1_label(str(row["review_l1"] or ""))
        m1, m2 = model_labels(row)
        m1c = canonicalize_l1_label(m1)
        if not h1 or not m1c:
            continue
        l1_tot += 1
        if m1c == h1:
            l1_ok += 1
        else:
            err[f"{m1c}→{h1}"] += 1
        h2 = normalize_l2(str(row["review_l2"] or ""))
        if _l2_eval_applicable(h1, m1c) and h2:
            l2_tot += 1
            if normalize_l2(m2) == h2:
                l2_ok += 1
        mt[str(row["v3_match_type"] or "（空）").strip() or "（空）"] += 1
    return {
        "rows": len(rows),
        "l1_ok": l1_ok,
        "l1_tot": l1_tot,
        "l1_pct": _pct(l1_ok, l1_tot),
        "l2_ok": l2_ok,
        "l2_tot": l2_tot,
        "l2_pct": _pct(l2_ok, l2_tot),
        "top_l1_errors": err.most_common(8),
        "match_types": mt.most_common(12),
    }


def _fetch_reviewed_since(conn: sqlite3.Connection, days: int) -> List[sqlite3.Row]:
    cutoff = datetime.now() - timedelta(days=max(1, days))
    rows = conn.execute(
        "SELECT * FROM opinion WHERE review_status = 1"
    ).fetchall()
    out: List[sqlite3.Row] = []
    for row in rows:
        ts = row_review_time(row["reviewed_at"], row["create_time"])
        if ts and ts >= cutoff:
            out.append(row)
    return out


def _adjustment_hint(
    full_ev: Dict[str, Any],
    window: Dict[str, Any],
    replay: Optional[Dict[str, Any]],
) -> List[str]:
    hints: List[str] = []
    w_l1 = window["l1_ok"] / max(window["l1_tot"], 1) * 100 if window["l1_tot"] else 0
    f_l1 = full_ev["l1_accuracy"] * 100

    if window["l1_tot"] < 80:
        hints.append(
            f"近窗口仅 {window['l1_tot']} 条已复核，样本偏少，汇报时建议同时给出全库口径。"
        )
    if w_l1 >= 84:
        hints.append("近窗口 L1 ≥84%，O1 v4.2 线上表现稳定，**暂不建议大改规则**。")
    elif w_l1 >= 80:
        hints.append("近窗口 L1 80–84%，可维持现状，优先分析 Top 错误是否集中。")
    else:
        hints.append("近窗口 L1 <80%，建议导出 L1 错误切片并做 targeted 规则/Prompt 迭代。")

    if f_l1 - w_l1 > 2:
        hints.append(
            f"全库 L1 ({f_l1:.1f}%) 明显高于近窗口 ({w_l1:.1f}%)，新批次更难，汇报应分「全库/近7天」两列。"
        )

    if replay:
        rb = int(replay.get("l1_broken") or 0)
        rf = int(replay.get("l1_fixed") or 0)
        if rb > 8:
            hints.append(f"规则 dry-run L1 broken={rb}>8，**不要扩规则**，先收窄。")
        elif rb == 0 and rf >= 20:
            hints.append("规则 dry-run broken=0 且 fixed 充足，当前规则链健康。")
        elif rb <= 3:
            hints.append(f"规则 dry-run L1 broken={rb}≤3，规则链可接受。")

    top = window.get("top_l1_errors") or []
    if top:
        k, n = top[0]
        if "服务类→非问题" in k and n >= window["l1_tot"] * 0.25:
            hints.append(
                "Top 错误仍为「服务类→非问题」，下一批可优先 pos_capture relay，而非继续堆 O1。"
            )
    if not hints:
        hints.append("暂无异常信号，继续按周复核 + 周报跟踪。")
    return hints


def _section_lines(title: str, body: List[str]) -> List[str]:
    lines = [f"## {title}", ""]
    lines.extend(body)
    lines.append("")
    return lines


def build_report(days: int, db_path: Path, *, include_replay: bool) -> str:
    conn = _connect_readonly(db_path)
    try:
        full_ev = evaluate(conn)
        window_rows = _fetch_reviewed_since(conn, days)
        window = _metrics_from_rows(window_rows)
        cumulative = count_all_reviewed(conn)
        replay_result: Optional[Dict[str, Any]] = None
        if include_replay and window_rows:
            replay_rows_data = replay_rows(fetch_rows(conn, since_days=days))
            replay_result = compare_rows(replay_rows_data)
    finally:
        conn.close()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: List[str] = [
        "# VOC 各阶段识别准确率（项目汇报）",
        "",
        f"- 生成时间：{now}",
        f"- 数据库：`{db_path.resolve()}`",
        f"- 统计窗口：近 **{days}** 天已复核",
        f"- 全库已复核总数：**{cumulative}** 条",
        "",
    ]

    lines.extend(
        _section_lines(
            "A. 全库已复核（当前线上 v3）",
            [
                f"- L1：**{full_ev['l1_correct']}/{full_ev['l1_denominator']} = {full_ev['l1_accuracy']*100:.2f}%**",
                f"- L2：**{full_ev['l2_correct']}/{full_ev['l2_denominator']} = {full_ev['l2_accuracy']*100:.2f}%**",
                "- 口径：人工 review_l1/l2 vs 库内 v3_label_meta（含后规则结果）",
            ],
        )
    )

    lines.extend(
        _section_lines(
            f"B. 近 {days} 天已复核（本周实测）",
            [
                f"- 样本数：**{window['rows']}** 条（参与 L1 评估 {window['l1_tot']} 条）",
                f"- L1：**{window['l1_ok']}/{window['l1_tot']} = {window['l1_pct']}**",
                f"- L2：**{window['l2_ok']}/{window['l2_tot']} = {window['l2_pct']}**",
                "- Top L1 错误：",
            ]
            + [f"  - {k}：{n} 条" for k, n in window["top_l1_errors"]]
            or ["  - （无）"],
        )
    )

    if include_replay and replay_result:
        rb = replay_result
        lines.extend(
            _section_lines(
                "C. 规则重放 dry-run（同批样本：模型基线 → 后规则）",
                [
                    f"- L1 before：**{rb['l1_before_pct']}%** → after：**{rb['l1_after_pct']}%**",
                    f"- fixed={rb['l1_fixed']} broken={rb['l1_broken']} net={rb['l1_net']}",
                    f"- L2 before：**{rb['l2_before_pct']}%** → after：**{rb['l2_after_pct']}%**",
                    f"- L2 fixed={rb['l2_fixed']} broken={rb['l2_broken']} net={rb['l2_net']}",
                    "- 说明：before=写库时模型标签；after=当前规则链离线重放（不写库）",
                ],
            )
        )
    else:
        lines.extend(
            _section_lines(
                "C. 规则重放 dry-run",
                ["- （跳过：近窗口无已复核样本或未启用 --replay）"],
            )
        )

    lines.extend(
        _section_lines(
            "D. 近窗口匹配方式分布（v3_match_type）",
            [f"- {k}：{n} 条" for k, n in window["match_types"]] or ["- （无）"],
        )
    )

    hints = _adjustment_hint(full_ev, window, replay_result)
    lines.extend(_section_lines("E. 是否需调整（建议）", [f"- {h}" for h in hints]))

    lines.extend(
        [
            "## 附：单命令复验",
            "",
            "```bash",
            "python3 performance_evaluation/evaluate_accuracy.py",
            f"python3 performance_evaluation/eval_batch_accuracy.py --since-days {days}",
            "python3 performance_evaluation/weekly_evolution_report.py",
            f"python3 performance_evaluation/eval_recent_rule_candidate.py --since-days {days} --max-l1-broken 8 --max-l2-broken 3",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="项目汇报：各阶段准确率汇总")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--days", type=int, default=7, help="近 N 天窗口（默认 7）")
    parser.add_argument(
        "--export-md",
        type=Path,
        default=None,
        help="导出 Markdown 路径；默认 exports/project_stages_YYYYMMDD.md",
    )
    parser.add_argument(
        "--no-replay",
        action="store_true",
        help="跳过规则 dry-run（加快执行）",
    )
    args = parser.parse_args()

    if not args.db.is_file():
        print(f"数据库不存在: {args.db}", file=sys.stderr)
        return 1

    report = build_report(args.days, args.db, include_replay=not args.no_replay)
    print(report)

    out = args.export_md
    if out is None:
        out = EXPORTS_DIR / f"project_stages_{datetime.now().strftime('%Y%m%d')}.md"
    if not out.is_absolute():
        out = EXPORTS_DIR / out.name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"\n[已导出] {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
