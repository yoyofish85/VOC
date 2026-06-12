#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Holdout 集上 baseline（库内 v3）与 candidate（classify_text）L1 准确率对比。

用法：
  python3 performance_evaluation/ab_compare_models.py --baseline
  python3 performance_evaluation/ab_compare_models.py --candidate "qwen2.5:14b-instruct-q4_K_M"
  python3 performance_evaluation/ab_compare_models.py --compare
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
STATE_DIR = PERF_DIR / "state"
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"
HOLDOUT_IDS_PATH = DATA_DIR / "holdout_opinion_ids.json"
REPORT_PATH = STATE_DIR / "ab_comparison_report.json"

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
from taxonomy_normalize import canonicalize_l1_label  # noqa: E402

sys.path.insert(0, str(PERF_DIR))
from evaluate_accuracy import model_labels  # noqa: E402


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_holdout_ids(path: Path = HOLDOUT_IDS_PATH) -> List[str]:
    if not path.is_file():
        raise FileNotFoundError(f"holdout 文件不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError(f"holdout 文件为空或格式错误: {path}")
    return [str(x).strip() for x in data if str(x).strip()]


def _fetch_holdout_rows(conn: sqlite3.Connection, holdout_ids: Sequence[str]) -> List[sqlite3.Row]:
    placeholders = ",".join("?" * len(holdout_ids))
    rows = conn.execute(
        f"""
        SELECT opinion_id, original_text, review_l1, review_l2,
               v3_label_meta, model_class, model_keyword, country
        FROM opinion
        WHERE opinion_id IN ({placeholders})
        """,
        list(holdout_ids),
    ).fetchall()
    by_id = {str(r["opinion_id"]): r for r in rows}
    return [by_id[i] for i in holdout_ids if i in by_id]


def _l1_accuracy(
    rows: Sequence[sqlite3.Row],
    predict_l1: Callable[[sqlite3.Row], str],
) -> Dict[str, Any]:
    total = 0
    correct = 0
    details: List[Dict[str, str]] = []
    for row in rows:
        human = canonicalize_l1_label(str(row["review_l1"] or ""))
        if not human:
            continue
        pred = canonicalize_l1_label(predict_l1(row))
        if not pred:
            continue
        total += 1
        ok = pred == human
        if ok:
            correct += 1
        details.append(
            {
                "opinion_id": str(row["opinion_id"]),
                "human_l1": human,
                "pred_l1": pred,
                "correct": ok,
            }
        )
    acc = (correct / total) if total else 0.0
    return {
        "l1_correct": correct,
        "l1_total": total,
        "l1_accuracy": round(acc, 4),
        "l1_accuracy_pct": round(acc * 100, 2),
        "details": details,
    }


def compute_baseline(db_path: Path, holdout_ids_path: Path = HOLDOUT_IDS_PATH) -> Dict[str, Any]:
    holdout_ids = _load_holdout_ids(holdout_ids_path)
    conn = _connect(db_path)
    try:
        rows = _fetch_holdout_rows(conn, holdout_ids)
    finally:
        conn.close()

    def _pred(row: sqlite3.Row) -> str:
        m1, _ = model_labels(row)
        return m1

    metrics = _l1_accuracy(rows, _pred)
    return {
        "mode": "baseline",
        "model": "v3_label_meta",
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "holdout_ids_path": str(holdout_ids_path.resolve()),
        "db_path": str(db_path.resolve()),
        **metrics,
    }


def compute_candidate(
    db_path: Path,
    model: str,
    *,
    holdout_ids_path: Path = HOLDOUT_IDS_PATH,
    host: Optional[str] = None,
    classify_fn: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    holdout_ids = _load_holdout_ids(holdout_ids_path)
    conn = _connect(db_path)
    try:
        rows = _fetch_holdout_rows(conn, holdout_ids)
    finally:
        conn.close()

    if classify_fn is None:
        sys.path.insert(0, str(PROJECT_ROOT / "src" / "backend"))
        from qwen_ollama import QWEN_HOST, classify_text  # noqa: E402

        classify_fn = classify_text
        if host is None:
            host = QWEN_HOST

    def _pred(row: sqlite3.Row) -> str:
        kwargs: Dict[str, Any] = {
            "text": str(row["original_text"] or ""),
            "db_path": str(db_path),
            "country": str(row["country"] or ""),
            "model": model,
        }
        if host is not None:
            kwargs["host"] = host
        result = classify_fn(**kwargs)
        return str(result.get("l1") or "")

    metrics = _l1_accuracy(rows, _pred)
    return {
        "mode": "candidate",
        "model": model,
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "holdout_ids_path": str(holdout_ids_path.resolve()),
        "db_path": str(db_path.resolve()),
        **metrics,
    }


def compare_metrics(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    b_pct = float(baseline.get("l1_accuracy_pct") or 0)
    c_pct = float(candidate.get("l1_accuracy_pct") or 0)
    delta_pp = round(c_pct - b_pct, 2)
    return {
        "compared_at": datetime.now().isoformat(timespec="seconds"),
        "baseline_model": baseline.get("model"),
        "candidate_model": candidate.get("model"),
        "baseline_l1_accuracy_pct": b_pct,
        "candidate_l1_accuracy_pct": c_pct,
        "delta_pp": delta_pp,
        "candidate_beats_baseline": delta_pp >= 2.0,
    }


def _load_report() -> Dict[str, Any]:
    if REPORT_PATH.is_file():
        return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    return {}


def _save_report(report: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_baseline(db_path: Path, *, holdout_ids_path: Path = HOLDOUT_IDS_PATH) -> Dict[str, Any]:
    metrics = compute_baseline(db_path, holdout_ids_path)
    report = _load_report()
    report["baseline"] = metrics
    _save_report(report)
    print(
        f"[baseline] L1 准确率 {metrics['l1_accuracy_pct']}% "
        f"({metrics['l1_correct']}/{metrics['l1_total']})"
    )
    return metrics


def run_candidate(
    db_path: Path,
    model: str,
    *,
    host: Optional[str] = None,
    holdout_ids_path: Path = HOLDOUT_IDS_PATH,
) -> Dict[str, Any]:
    metrics = compute_candidate(db_path, model, host=host, holdout_ids_path=holdout_ids_path)
    report = _load_report()
    report["candidate"] = metrics
    _save_report(report)
    print(
        f"[candidate:{model}] L1 准确率 {metrics['l1_accuracy_pct']}% "
        f"({metrics['l1_correct']}/{metrics['l1_total']})"
    )
    return metrics


def run_compare(
    db_path: Path,
    *,
    candidate_model: Optional[str] = None,
    holdout_ids_path: Path = HOLDOUT_IDS_PATH,
) -> Dict[str, Any]:
    report = _load_report()
    baseline = report.get("baseline") or compute_baseline(db_path, holdout_ids_path)
    report["baseline"] = baseline

    candidate = report.get("candidate")
    if candidate is None:
        if not candidate_model:
            raise ValueError("缺少 candidate 结果，请先用 --candidate 指定模型")
        candidate = compute_candidate(db_path, candidate_model, holdout_ids_path=holdout_ids_path)
    report["candidate"] = candidate

    comparison = compare_metrics(baseline, candidate)
    report["comparison"] = comparison
    _save_report(report)

    print(
        f"[compare] baseline {comparison['baseline_l1_accuracy_pct']}% "
        f"vs candidate {comparison['candidate_l1_accuracy_pct']}% "
        f"(Δ {comparison['delta_pp']:+.2f} pp)"
    )
    return comparison


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Holdout L1 baseline / candidate A/B 对比")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument("--baseline", action="store_true", help="计算 baseline（v3_label_meta）")
    parser.add_argument("--candidate", type=str, default="", help="候选模型名")
    parser.add_argument("--compare", action="store_true", help="对比 baseline 与 candidate")
    parser.add_argument("--host", type=str, default="", help="Ollama host（candidate 用）")
    parser.add_argument(
        "--holdout-ids-path",
        type=Path,
        default=HOLDOUT_IDS_PATH,
        help="holdout ID 锁定文件",
    )
    args = parser.parse_args(argv)

    db_path = args.db_path.resolve()
    holdout_path = args.holdout_ids_path.resolve()
    if not db_path.is_file():
        print(f"错误：数据库不存在 {db_path}", file=sys.stderr)
        return 1

    if not (args.baseline or args.candidate or args.compare):
        parser.error("请指定 --baseline、--candidate 或 --compare")

    try:
        if args.baseline:
            run_baseline(db_path, holdout_ids_path=holdout_path)
        elif args.candidate:
            run_candidate(
                db_path,
                args.candidate,
                host=args.host or None,
                holdout_ids_path=holdout_path,
            )
        else:
            run_compare(
                db_path,
                candidate_model=args.candidate or None,
                holdout_ids_path=holdout_path,
            )
    except (FileNotFoundError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"运行失败: {exc}", file=sys.stderr)
        return 1

    print(f"报告已写入: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
