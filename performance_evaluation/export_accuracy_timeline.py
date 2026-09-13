#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出项目准确率时间线（用于后续绘制曲线）。

数据来源（按优先顺序）：
1) performance_evaluation/reports/*_report.txt（evaluate_accuracy 历史报告）
2) performance_evaluation/state/evolution_baseline*.json（早期基线）
3) 可选手工补充 CSV（--manual-csv）

输出：
- CSV：时间序列明细（默认 exports/accuracy_timeline_YYYYMMDD.csv）
- Markdown：简表与关键变化（默认 exports/accuracy_timeline_YYYYMMDD.md）
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

PERF_DIR = Path(__file__).resolve().parent
EXPORTS_DIR = PERF_DIR / "exports"
REPORTS_DIR = PERF_DIR / "reports"
STATE_DIR = PERF_DIR / "state"


@dataclass
class Point:
    eval_time: datetime
    l1_accuracy_pct: float
    l2_accuracy_pct: float
    cumulative_reviewed: int
    source: str
    note: str = ""


def _parse_report(path: Path) -> Optional[Point]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    m_time = re.search(r"评估时间：([0-9T:\-]+)", text)
    m_l1 = re.search(r"一级标签准确率：([0-9.]+)%", text)
    m_l2 = re.search(r"二级标签准确率（业务三类）：([0-9.]+)%", text)
    m_cnt = re.search(r"库内已复核总条数（review_status=1）：([0-9]+)\s*条", text)
    m_l1_den = re.search(r"参与一级评估条数：([0-9]+)", text)
    m_l2_den = re.search(r"参与二级评估条数[^：]*：([0-9]+)", text)
    if not (m_time and m_l1 and m_l2 and m_cnt and m_l1_den and m_l2_den):
        return None
    if int(m_l1_den.group(1)) <= 0 or int(m_l2_den.group(1)) <= 0:
        return None
    return Point(
        eval_time=datetime.fromisoformat(m_time.group(1)),
        l1_accuracy_pct=float(m_l1.group(1)),
        l2_accuracy_pct=float(m_l2.group(1)),
        cumulative_reviewed=int(m_cnt.group(1)),
        source=f"report:{path.name}",
    )


def _parse_baseline_json(path: Path) -> Optional[Point]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if "l1_accuracy" not in data or "l2_accuracy" not in data:
        return None
    ts = data.get("locked_at") or data.get("eval_finished_at")
    if not ts:
        return None
    cnt = int(
        data.get("cumulative_reviewed")
        or data.get("cumulative_reviewed_count")
        or 0
    )
    if cnt <= 0:
        return None
    return Point(
        eval_time=datetime.fromisoformat(str(ts).replace("Z", "")),
        l1_accuracy_pct=round(float(data["l1_accuracy"]) * 100, 2),
        l2_accuracy_pct=round(float(data["l2_accuracy"]) * 100, 2),
        cumulative_reviewed=cnt,
        source=f"state:{path.name}",
        note=str(data.get("source") or data.get("label") or ""),
    )


def _parse_manual_csv(path: Path) -> List[Point]:
    out: List[Point] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = csv.DictReader(f)
        for r in rows:
            try:
                out.append(
                    Point(
                        eval_time=datetime.fromisoformat(
                            (r.get("eval_time") or "").strip()
                        ),
                        l1_accuracy_pct=float(r.get("l1_accuracy_pct") or 0),
                        l2_accuracy_pct=float(r.get("l2_accuracy_pct") or 0),
                        cumulative_reviewed=int(r.get("cumulative_reviewed") or 0),
                        source=(r.get("source") or "manual").strip(),
                        note=(r.get("note") or "").strip(),
                    )
                )
            except Exception:
                continue
    return out


def _dedupe(points: List[Point]) -> List[Point]:
    by_time: Dict[str, Point] = {}
    for p in points:
        k = p.eval_time.isoformat(timespec="seconds")
        old = by_time.get(k)
        if old is None:
            by_time[k] = p
            continue
        # 优先 report 记录，其次 state/manual
        old_rank = 2 if old.source.startswith("report:") else 1
        new_rank = 2 if p.source.startswith("report:") else 1
        if new_rank >= old_rank:
            by_time[k] = p
    ordered = sorted(by_time.values(), key=lambda x: x.eval_time)
    # 过滤明显异常点（例如空库评估导致的 0%）
    cleaned: List[Point] = []
    for p in ordered:
        if p.l1_accuracy_pct == 0.0 and p.l2_accuracy_pct == 0.0:
            continue
        cleaned.append(p)
    return cleaned


def _write_csv(path: Path, points: List[Point]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "eval_time",
                "l1_accuracy_pct",
                "l2_accuracy_pct",
                "cumulative_reviewed",
                "source",
                "note",
                "delta_l1_pp",
                "delta_l2_pp",
                "delta_reviewed",
            ]
        )
        prev: Optional[Point] = None
        for p in points:
            d1 = round(p.l1_accuracy_pct - prev.l1_accuracy_pct, 2) if prev else ""
            d2 = round(p.l2_accuracy_pct - prev.l2_accuracy_pct, 2) if prev else ""
            dc = p.cumulative_reviewed - prev.cumulative_reviewed if prev else ""
            w.writerow(
                [
                    p.eval_time.isoformat(timespec="seconds"),
                    f"{p.l1_accuracy_pct:.2f}",
                    f"{p.l2_accuracy_pct:.2f}",
                    p.cumulative_reviewed,
                    p.source,
                    p.note,
                    d1,
                    d2,
                    dc,
                ]
            )
            prev = p


def _write_md(path: Path, points: List[Point]) -> None:
    lines = [
        "# 准确率时间线（用于总结曲线）",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 点位数量：{len(points)}",
        "",
        "| 时间 | L1 | L2 | 已复核累计 | ΔL1(pp) | ΔL2(pp) | Δ复核 | 来源 |",
        "|------|----|----|------------|----------|----------|-------|------|",
    ]
    prev: Optional[Point] = None
    for p in points:
        d1 = f"{p.l1_accuracy_pct - prev.l1_accuracy_pct:+.2f}" if prev else "-"
        d2 = f"{p.l2_accuracy_pct - prev.l2_accuracy_pct:+.2f}" if prev else "-"
        dc = f"{p.cumulative_reviewed - prev.cumulative_reviewed:+d}" if prev else "-"
        lines.append(
            f"| {p.eval_time.strftime('%Y-%m-%d %H:%M')} | "
            f"{p.l1_accuracy_pct:.2f}% | {p.l2_accuracy_pct:.2f}% | "
            f"{p.cumulative_reviewed} | {d1} | {d2} | {dc} | `{p.source}` |"
        )
        prev = p
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="导出准确率时间线")
    parser.add_argument(
        "--manual-csv",
        type=Path,
        default=None,
        help="可选：手工补充点位 CSV（字段见脚本说明）",
    )
    parser.add_argument("--out-csv", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    args = parser.parse_args()

    points: List[Point] = []
    for p in sorted(REPORTS_DIR.glob("*_report.txt")):
        pt = _parse_report(p)
        if pt:
            points.append(pt)

    for p in sorted(STATE_DIR.glob("evolution_baseline*.json")):
        pt = _parse_baseline_json(p)
        if pt:
            points.append(pt)

    if args.manual_csv and args.manual_csv.is_file():
        points.extend(_parse_manual_csv(args.manual_csv))

    points = _dedupe(points)
    if not points:
        print("[提示] 未找到可解析点位（reports/state/manual 都为空）")
        return 0

    stamp = datetime.now().strftime("%Y%m%d")
    out_csv = args.out_csv or (EXPORTS_DIR / f"accuracy_timeline_{stamp}.csv")
    out_md = args.out_md or (EXPORTS_DIR / f"accuracy_timeline_{stamp}.md")

    if not out_csv.is_absolute():
        out_csv = EXPORTS_DIR / out_csv.name
    if not out_md.is_absolute():
        out_md = EXPORTS_DIR / out_md.name

    _write_csv(out_csv, points)
    _write_md(out_md, points)
    print(f"[已导出 CSV] {out_csv.resolve()}")
    print(f"[已导出 MD ] {out_md.resolve()}")
    print(f"[点位数量] {len(points)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
