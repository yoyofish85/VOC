#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P1+ 子集评估：不必全量 batch_classify（~2h），只对「改动相关」已复核样本做对比。

原理（P1 正向捕获 / 守卫仅作用于 14B 之后）：
  - 基线：库内 v3_l1/l2（当前全量重分结果） vs 人工 review_l1
  - 改动后：对同一批样本用 apply_classification_post_rules() 离线重放新规则 vs 人工
  - 可选 --shadow-llm N：对少量样本再跑 14B（验证 Prompt 变化，较慢）

用法：
  # 1) 改动前：记录基线（或仅看当前库内 v3 表现）
  python3 performance_evaluation/eval_p1_subset.py --save-baseline

  # 2) 部署新 qwen_ollama.py 后：对比（秒级，不调 14B）
  python3 performance_evaluation/eval_p1_subset.py --compare

  # 3) 导出子集 ID 供 batch_classify 局部写库（可选）
  python3 performance_evaluation/eval_p1_subset.py --export-ids exports/p1_subset_ids.txt

  # 4) 导出 P1 主靶未修复样本（含捕获阻断原因，供 v9.2 扩规则）
  python3 performance_evaluation/eval_p1_subset.py --export-unfixed
  python3 performance_evaluation/eval_p1_subset.py --compare --export-unfixed exports/p1_primary_unfixed.csv

  # 5) 确认有效后再全量重分 + evaluate_accuracy.py
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
STATE_DIR = PERF_DIR / "state"
EXPORTS_DIR = PERF_DIR / "exports"
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"
BASELINE_PATH = STATE_DIR / "p1_subset_baseline.json"
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(PERF_DIR))

from taxonomy_normalize import L2_EVAL_L1, NON_ISSUE_L1, canonicalize_l1_label  # noqa: E402
from evaluate_accuracy import _connect_readonly, model_labels  # noqa: E402
from post_rules_replay import apply_post_rules_with_context  # noqa: E402

# P1 主靶：人工=非问题，模型=业务三类（正向捕获应修复）
P1_PRIMARY_HUMAN = NON_ISSUE_L1
P1_PRIMARY_MODEL = frozenset(L2_EVAL_L1)

# P1 回归：人工=业务，模型=非问题（守卫误伤监控；Phase2 重点，P1 只统计不优化）
P1_REGRESSION_HUMAN = L2_EVAL_L1
P1_REGRESSION_MODEL = NON_ISSUE_L1


def _row_dict(row) -> Dict[str, Any]:
    return {
        "opinion_id": str(row["opinion_id"] or ""),
        "text": str(row["original_text"] or "").strip(),
        "human_l1": canonicalize_l1_label(row["review_l1"]),
        "human_l2": str(row["review_l2"] or "").strip(),
        "source": str(row["source"] or "").strip(),
        "vin": str(row["vin"] or "").strip(),
        "model_l1": "",
        "model_l2": "",
    }


def load_reviewed(conn) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT opinion_id, original_text, review_l1, review_l2,
               source, vin, v3_label_meta, model_class, model_keyword
        FROM opinion WHERE review_status = 1
        """
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        hr1 = str(row["review_l1"] or "").strip()
        if not hr1:
            continue
        m1, m2 = model_labels(row)
        if not m1:
            continue
        d = _row_dict(row)
        d["model_l1"] = canonicalize_l1_label(m1)
        d["model_l2"] = (m2 or "").strip()
        out.append(d)
    return out


def classify_subset(rows: List[Dict[str, Any]]) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    primary: List[Dict] = []
    regression: List[Dict] = []
    other_errors: List[Dict] = []
    for d in rows:
        h, m = d["human_l1"], d["model_l1"]
        if h == P1_PRIMARY_HUMAN and m in P1_PRIMARY_MODEL:
            primary.append(d)
        elif h in P1_REGRESSION_HUMAN and m == P1_REGRESSION_MODEL:
            regression.append(d)
        elif h != m:
            other_errors.append(d)
    return primary, regression, other_errors


def replay_rules(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from qwen_ollama import load_l2_whitelist  # noqa: E402

    l2_map = load_l2_whitelist()
    enriched: List[Dict[str, Any]] = []
    for d in rows:
        nl1, nl2, flags = apply_post_rules_with_context(
            d["text"],
            d["model_l1"],
            d["model_l2"],
            l2_map,
            source=d["source"],
            vin=d["vin"],
        )
        nd = dict(d)
        nd["replay_l1"] = canonicalize_l1_label(nl1)
        nd["replay_l2"] = (nl2 or "").strip()
        nd["rule_flags"] = flags
        enriched.append(nd)
    return enriched


def _acc(rows: List[Dict[str, Any]], pred_key: str) -> Tuple[int, int, float]:
    if not rows:
        return 0, 0, 0.0
    ok = sum(1 for r in rows if r[pred_key] == r["human_l1"])
    return ok, len(rows), ok / len(rows)


def _delta_report(
    label: str, rows: List[Dict[str, Any]], baseline_key: str, new_key: str
) -> Dict[str, Any]:
    b_ok, b_n, b_acc = _acc(rows, baseline_key)
    n_ok, n_n, n_acc = _acc(rows, new_key)
    fixed = sum(
        1
        for r in rows
        if r[baseline_key] != r["human_l1"] and r[new_key] == r["human_l1"]
    )
    broken = sum(
        1
        for r in rows
        if r[baseline_key] == r["human_l1"] and r[new_key] != r["human_l1"]
    )
    return {
        "label": label,
        "count": len(rows),
        "baseline_correct": b_ok,
        "baseline_accuracy": round(b_acc, 6),
        "replay_correct": n_ok,
        "replay_accuracy": round(n_acc, 6),
        "fixed": fixed,
        "broken": broken,
        "delta_pp": round((n_acc - b_acc) * 100, 2),
    }


UNFIXED_CSV_FIELDS = [
    "舆情编号",
    "原文",
    "模型一级",
    "模型二级",
    "人工一级",
    "人工二级",
    "重放一级",
    "重放二级",
    "捕获诊断",
    "规则标记",
]


def _format_rule_flags(flags: Dict[str, bool]) -> str:
    if not flags:
        return ""
    return ";".join(sorted(k for k, v in flags.items() if v))


def _local_explain_positive_capture_block(text: str) -> str:
    """兼容旧版 qwen_ollama（无 explain_positive_capture_block 时本地诊断）。"""
    try:
        from qwen_ollama import explain_positive_capture_block  # noqa: E402

        return explain_positive_capture_block(text)
    except ImportError:
        pass

    import re

    from qwen_ollama import (  # noqa: E402
        _KZ_AFTERSALE_PRAISE,
        _KZ_ANNOUNCEMENT,
        _KZ_BIZ_BOUNDARY,
        _KZ_NEUTRAL_QUERY,
        _KZ_PRAISE,
        _KZ_SALES_TESTDRIVE_PRAISE,
        _KZ_SHORT_BENIGN,
        _KZ_SHORT_MAX_CHARS,
        _KZ_TRUE_NEGATIVE,
        _LFC_CHARGING_DOMAIN,
        _NEGATION_BEFORE_POSITIVE,
        _SRV_QUALITY_ISSUE,
    )

    t = (text or "").strip()
    if not t:
        return "empty_text"
    if _KZ_TRUE_NEGATIVE.search(t):
        return "blocked:true_negative"
    if _NEGATION_BEFORE_POSITIVE.search(t):
        return "blocked:negation"
    if _LFC_CHARGING_DOMAIN.search(t):
        return "blocked:lfc_charging_domain"
    if _SRV_QUALITY_ISSUE.search(t):
        return "blocked:srv_quality_issue"
    norm = re.sub(r"\s+", "", t)
    if norm and len(norm) <= _KZ_SHORT_MAX_CHARS and _KZ_SHORT_BENIGN.match(norm):
        return "eligible:short_benign"
    if _KZ_PRAISE.search(t):
        return "eligible:praise"
    if _KZ_NEUTRAL_QUERY.search(t):
        return "eligible:neutral_query"
    if _KZ_ANNOUNCEMENT.search(t):
        return "eligible:announcement"
    if _KZ_SALES_TESTDRIVE_PRAISE.search(t):
        return "eligible:sales_testdrive_praise"
    if _KZ_AFTERSALE_PRAISE.search(t):
        return "eligible:aftersale_praise"
    if _KZ_BIZ_BOUNDARY.search(t) and _KZ_PRAISE.search(t):
        return "eligible:biz_boundary_praise"
    return "no_capture_pattern"


def _get_capture_diagnoser():
    try:
        from qwen_ollama import explain_positive_capture_block  # noqa: E402

        return explain_positive_capture_block
    except ImportError:
        return _local_explain_positive_capture_block


def export_unfixed_primary(enriched_primary: List[Dict[str, Any]], path: Path) -> Tuple[int, Counter]:
    """导出 P1 主靶中规则重放仍未修复的样本。"""
    diagnose = _get_capture_diagnoser()

    unfixed = [r for r in enriched_primary if r["replay_l1"] != r["human_l1"]]
    reason_counter: Counter = Counter()
    rows: List[Dict[str, str]] = []
    for r in unfixed:
        diag = diagnose(r["text"])
        reason_counter[diag] += 1
        flags = r.get("rule_flags") or {}
        if r["replay_l1"] != r["model_l1"] and diag.startswith("eligible:"):
            diag = f"{diag};replay_changed_but_wrong_l1"
        rows.append(
            {
                "舆情编号": r["opinion_id"],
                "原文": r["text"],
                "模型一级": r["model_l1"],
                "模型二级": r["model_l2"],
                "人工一级": r["human_l1"],
                "人工二级": r["human_l2"],
                "重放一级": r["replay_l1"],
                "重放二级": r["replay_l2"],
                "捕获诊断": diag,
                "规则标记": _format_rule_flags(flags),
            }
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=UNFIXED_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(unfixed), reason_counter


def export_fixable_primary(enriched_primary: List[Dict[str, Any]], path: Path) -> int:
    """导出 P1 主靶中规则重放可修复、但库内 v3 仍为业务类的样本。"""
    diagnose = _get_capture_diagnoser()
    fixable = [
        r
        for r in enriched_primary
        if r["model_l1"] != r["human_l1"] and r["replay_l1"] == r["human_l1"]
    ]
    rows: List[Dict[str, str]] = []
    for r in fixable:
        diag = diagnose(r["text"])
        flags = r.get("rule_flags") or {}
        rows.append(
            {
                "舆情编号": r["opinion_id"],
                "原文": r["text"],
                "模型一级": r["model_l1"],
                "模型二级": r["model_l2"],
                "人工一级": r["human_l1"],
                "人工二级": r["human_l2"],
                "重放一级": r["replay_l1"],
                "重放二级": r["replay_l2"],
                "捕获诊断": diag,
                "规则标记": _format_rule_flags(flags),
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=UNFIXED_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(fixable)


def export_unfixed_regression(enriched_regression: List[Dict[str, Any]], path: Path) -> Tuple[int, Counter]:
    """导出 P1 回归中规则重放仍未拉回业务类的样本（M4-B 主战场）。"""
    diagnose = _get_capture_diagnoser()
    unfixed = [r for r in enriched_regression if r["replay_l1"] != r["human_l1"]]
    reason_counter: Counter = Counter()
    rows: List[Dict[str, str]] = []
    for r in unfixed:
        diag = diagnose(r["text"])
        reason_counter[diag.split(";")[0]] += 1
        flags = r.get("rule_flags") or {}
        rows.append(
            {
                "舆情编号": r["opinion_id"],
                "原文": r["text"],
                "模型一级": r["model_l1"],
                "模型二级": r["model_l2"],
                "人工一级": r["human_l1"],
                "人工二级": r["human_l2"],
                "重放一级": r["replay_l1"],
                "重放二级": r["replay_l2"],
                "捕获诊断": diag,
                "规则标记": _format_rule_flags(flags),
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=UNFIXED_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(unfixed), reason_counter


def export_fixable_regression(enriched_regression: List[Dict[str, Any]], path: Path) -> int:
    """导出 P1 回归中规则重放可拉回业务类、但库内 v3 仍为非问题的样本（M4-A）。"""
    fixable = [
        r
        for r in enriched_regression
        if r["model_l1"] != r["human_l1"] and r["replay_l1"] == r["human_l1"]
    ]
    rows: List[Dict[str, str]] = []
    for r in fixable:
        flags = r.get("rule_flags") or {}
        rows.append(
            {
                "舆情编号": r["opinion_id"],
                "原文": r["text"],
                "模型一级": r["model_l1"],
                "模型二级": r["model_l2"],
                "人工一级": r["human_l1"],
                "人工二级": r["human_l2"],
                "重放一级": r["replay_l1"],
                "重放二级": r["replay_l2"],
                "捕获诊断": _format_rule_flags(flags) or "guard_pullback",
                "规则标记": _format_rule_flags(flags),
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=UNFIXED_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(fixable)


def export_other_errors_replay(
    other_errors: List[Dict[str, Any]], enriched_other: List[Dict[str, Any]], path: Path
) -> Tuple[int, int]:
    """导出其他 L1 错误及规则重放结果（M4-B 候选池）。"""
    by_oid = {r["opinion_id"]: r for r in enriched_other}
    rows: List[Dict[str, str]] = []
    fixable = 0
    for d in other_errors:
        r = by_oid.get(d["opinion_id"], d)
        flags = r.get("rule_flags") or {}
        replay_ok = r.get("replay_l1") == r["human_l1"]
        if replay_ok and r.get("model_l1") != r["human_l1"]:
            fixable += 1
        err_pat = f"{r['model_l1']} → {r['human_l1']}"
        rows.append(
            {
                "舆情编号": r["opinion_id"],
                "原文": r["text"],
                "模型一级": r["model_l1"],
                "模型二级": r["model_l2"],
                "人工一级": r["human_l1"],
                "人工二级": r["human_l2"],
                "重放一级": r.get("replay_l1", r["model_l1"]),
                "重放二级": r.get("replay_l2", r["model_l2"]),
                "捕获诊断": err_pat if not replay_ok else f"fixable:{err_pat}",
                "规则标记": _format_rule_flags(flags),
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=UNFIXED_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows), fixable


def export_fixable_other(enriched_other: List[Dict[str, Any]], path: Path) -> int:
    """导出其他 L1 错误中规则重放可纠偏样本（跨类 tilt 等）。"""
    fixable = [
        r
        for r in enriched_other
        if r["model_l1"] != r["human_l1"] and r["replay_l1"] == r["human_l1"]
    ]
    rows: List[Dict[str, str]] = []
    for r in fixable:
        flags = r.get("rule_flags") or {}
        err_pat = f"{r['model_l1']} → {r['human_l1']}"
        rows.append(
            {
                "舆情编号": r["opinion_id"],
                "原文": r["text"],
                "模型一级": r["model_l1"],
                "模型二级": r["model_l2"],
                "人工一级": r["human_l1"],
                "人工二级": r["human_l2"],
                "重放一级": r["replay_l1"],
                "重放二级": r["replay_l2"],
                "捕获诊断": f"fixable:{err_pat}",
                "规则标记": _format_rule_flags(flags),
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=UNFIXED_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(fixable)


def estimate_global_l1_delta(all_rows: List[Dict[str, Any]], enriched_primary: List[Dict]) -> float:
    """估算全库 L1 提升百分点（仅 P1 主靶修复数 / 全库 L1 分母）。"""
    l1_den = len(all_rows)
    fixed = sum(
        1
        for r in enriched_primary
        if r["model_l1"] != r["human_l1"] and r["replay_l1"] == r["human_l1"]
    )
    return round(fixed / l1_den * 100, 2) if l1_den else 0.0


def run_shadow_llm(
    rows: List[Dict[str, Any]], db_path: Path, limit: int
) -> List[Dict[str, Any]]:
    from qwen_ollama import classify_text  # noqa: E402

    out: List[Dict[str, Any]] = []
    for d in rows[:limit]:
        pred = classify_text(d["text"], db_path=str(db_path))
        nd = dict(d)
        nd["llm_l1"] = canonicalize_l1_label(pred.get("l1"))
        nd["llm_l2"] = str(pred.get("l2") or "").strip()
        nd["llm_flags"] = {
            k: pred.get(k)
            for k in (
                "positive_capture",
                "charging_domain_guard",
                "srv_quality_guard",
                "non_issue_guard",
            )
            if pred.get(k)
        }
        out.append(nd)
    return out


def format_report(
    stats: Dict[str, Any],
    *,
    compare: bool,
    shadow: Optional[List[Dict[str, Any]]] = None,
) -> str:
    lines = ["=" * 60, " P1 子集评估（规则重放，非全量重分）", "=" * 60]
    lines.append(f"时间：{stats['evaluated_at']}")
    lines.append(f"数据库：{stats['db_abspath']}")
    lines.append(f"已复核且有模型标签：{stats['reviewed_with_model']} 条")
    lines.append("")
    lines.append("【子集定义】")
    lines.append(f"  P1 主靶（人工=非问题，模型=业务三类）：{stats['primary_count']} 条")
    lines.append(f"  P1 回归监控（人工=业务，模型=非问题）：{stats['regression_count']} 条")
    lines.append(f"  其他 L1 错误（本脚本不优化）：{stats['other_error_count']} 条")
    lines.append("")

    if compare:
        for key in ("primary", "regression"):
            r = stats[key]
            lines.append(f"【{r['label']}】")
            lines.append(
                f"  基线（库内 v3）：{r['baseline_correct']}/{r['count']} = {r['baseline_accuracy']*100:.2f}%"
            )
            lines.append(
                f"  规则重放后：  {r['replay_correct']}/{r['count']} = {r['replay_accuracy']*100:.2f}%"
            )
            lines.append(
                f"  修复 {r['fixed']} 条，回退 {r['broken']} 条，子集 Δ {r['delta_pp']:+.2f}pp"
            )
            lines.append("")
        lines.append(
            f"【全库 L1 估算提升】若仅 P1 主靶修复生效：约 +{stats['estimated_global_l1_delta_pp']:.2f}pp"
        )
        lines.append(f"  （{stats['primary']['fixed']} / {stats['reviewed_with_model']} 条 L1 分母）")
        if stats.get("baseline_saved"):
            lines.append(f"  已写入基线：{stats['baseline_saved']}")
    else:
        p = stats["primary"]
        lines.append("【当前库内 v3 · P1 主靶】")
        lines.append(
            f"  准确率：{p['baseline_correct']}/{p['count']} = {p['baseline_accuracy']*100:.2f}%"
        )
        lines.append("")
        lines.append("（部署新规则后执行 --compare 查看规则重放效果）")

    if shadow:
        lines.append("")
        lines.append(f"【14B 影子抽样 n={len(shadow)}】")
        llm_ok = sum(1 for r in shadow if r.get("llm_l1") == r["human_l1"])
        lines.append(f"  全链路 L1 准确：{llm_ok}/{len(shadow)} = {llm_ok/len(shadow)*100:.1f}%")
        cap = sum(1 for r in shadow if (r.get("llm_flags") or {}).get("positive_capture"))
        lines.append(f"  其中 positive_capture：{cap} 条")

    lines.append("")
    lines.append("【说明】")
    lines.append("  · 规则重放以库内 v3 为 14B 输出代理；对主靶误判通常成立（捕获未触发时 v3≈14B）")
    lines.append("  · 确认有效后再 batch_classify 全量写库，并用 evaluate_accuracy.py 验收全库")
    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 子集评估（避免全量 2h 重分）")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="保存当前库内 v3 子集准确率到 state/p1_subset_baseline.json",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="对比库内 v3 vs 当前代码规则重放",
    )
    parser.add_argument(
        "--export-ids",
        type=Path,
        default=None,
        help="导出 P1 主靶+回归 opinion_id 列表（供局部 batch_classify）",
    )
    parser.add_argument(
        "--export-unfixed",
        nargs="?",
        const="auto",
        default=None,
        metavar="PATH",
        help="导出 P1 主靶未修复 CSV（含捕获诊断）；省略路径则写 exports/p1_primary_unfixed_时间戳.csv",
    )
    parser.add_argument(
        "--export-fixable",
        nargs="?",
        const="auto",
        default=None,
        metavar="PATH",
        help="导出 P1 主靶可修复 CSV（v3 仍为业务，规则重放→非问题）；省略路径则写 exports/p1_primary_fixable_时间戳.csv",
    )
    parser.add_argument(
        "--export-regression-fixable",
        nargs="?",
        const="auto",
        default=None,
        metavar="PATH",
        help="导出 P1 回归可拉回 CSV（v3=非问题，重放→业务）；M4-A",
    )
    parser.add_argument(
        "--export-regression-unfixed",
        nargs="?",
        const="auto",
        default=None,
        metavar="PATH",
        help="导出 P1 回归未拉回 CSV（含捕获诊断）；M4-B",
    )
    parser.add_argument(
        "--export-other-errors",
        nargs="?",
        const="auto",
        default=None,
        metavar="PATH",
        help="导出其他 L1 错误 + 重放结果；M4-B 候选池",
    )
    parser.add_argument(
        "--export-other-fixable",
        nargs="?",
        const="auto",
        default=None,
        metavar="PATH",
        help="导出其他 L1 可纠偏 CSV（跨类 tilt）；M4-B2",
    )
    parser.add_argument(
        "--shadow-llm",
        type=int,
        default=0,
        help="对主靶子集前 N 条调用 14B 全链路（慢，验证 Prompt）",
    )
    args = parser.parse_args()

    if (
        not args.save_baseline
        and not args.compare
        and not args.export_ids
        and args.export_unfixed is None
        and args.export_fixable is None
        and args.export_regression_fixable is None
        and args.export_regression_unfixed is None
        and args.export_other_errors is None
        and args.export_other_fixable is None
        and not args.shadow_llm
    ):
        args.compare = True

    conn = _connect_readonly(args.db)
    try:
        all_rows = load_reviewed(conn)
    finally:
        conn.close()

    primary, regression, other = classify_subset(all_rows)
    enriched_p = replay_rules(primary)
    enriched_r = replay_rules(regression)
    enriched_o = replay_rules(other)

    for r in enriched_p:
        r["model_l1_key"] = r["model_l1"]
        r["baseline_l1"] = r["model_l1"]
        r["replay_l1_key"] = r["replay_l1"]
    for r in enriched_r:
        r["baseline_l1"] = r["model_l1"]
        r["replay_l1_key"] = r["replay_l1"]

    stats: Dict[str, Any] = {
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "db_abspath": str(args.db.resolve()),
        "reviewed_with_model": len(all_rows),
        "primary_count": len(primary),
        "regression_count": len(regression),
        "other_error_count": len(other),
    }

    primary_report = _delta_report(
        "P1 主靶（人工非问题←模型业务）",
        enriched_p,
        "model_l1",
        "replay_l1",
    )
    regression_report = _delta_report(
        "P1 回归（人工业务←模型非问题）",
        enriched_r,
        "model_l1",
        "replay_l1",
    )
    stats["primary"] = primary_report
    stats["regression"] = regression_report
    stats["estimated_global_l1_delta_pp"] = estimate_global_l1_delta(all_rows, enriched_p)

    if args.save_baseline:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": stats["evaluated_at"],
            "db_abspath": stats["db_abspath"],
            "primary": primary_report,
            "regression": regression_report,
            "primary_ids": [r["opinion_id"] for r in primary],
            "regression_ids": [r["opinion_id"] for r in regression],
        }
        BASELINE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        stats["baseline_saved"] = str(BASELINE_PATH)

    shadow_rows: Optional[List[Dict[str, Any]]] = None
    if args.shadow_llm and enriched_p:
        shadow_rows = run_shadow_llm(enriched_p, args.db, int(args.shadow_llm))

    if args.export_ids:
        args.export_ids.parent.mkdir(parents=True, exist_ok=True)
        ids: Set[str] = {r["opinion_id"] for r in primary + regression}
        args.export_ids.write_text("\n".join(sorted(ids)) + "\n", encoding="utf-8")
        print(f"[已导出] {len(ids)} 个 opinion_id → {args.export_ids}")

    if args.export_unfixed is not None:
        if args.export_unfixed == "auto":
            unfixed_path = (
                EXPORTS_DIR
                / f"p1_primary_unfixed_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            )
        else:
            unfixed_path = Path(args.export_unfixed)
            if not unfixed_path.is_absolute():
                # 相对路径统一写入 performance_evaluation/exports/（避免落到项目根 exports/）
                unfixed_path = EXPORTS_DIR / unfixed_path.name
        n_unfixed, reasons = export_unfixed_primary(enriched_p, unfixed_path)
        print(f"[已导出] P1 主靶未修复 {n_unfixed} 条 → {unfixed_path}")
        if reasons:
            print("[捕获诊断分布]")
            for reason, cnt in reasons.most_common():
                print(f"  {reason}: {cnt}")

    if args.export_fixable is not None:
        if args.export_fixable == "auto":
            fixable_path = (
                EXPORTS_DIR
                / f"p1_primary_fixable_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            )
        else:
            fixable_path = Path(args.export_fixable)
            if not fixable_path.is_absolute():
                fixable_path = EXPORTS_DIR / fixable_path.name
        n_fixable = export_fixable_primary(enriched_p, fixable_path)
        print(f"[已导出] P1 主靶可修复 {n_fixable} 条 → {fixable_path}")

    if args.export_regression_fixable is not None:
        if args.export_regression_fixable == "auto":
            reg_path = (
                EXPORTS_DIR
                / f"p1_regression_fixable_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            )
        else:
            reg_path = Path(args.export_regression_fixable)
            if not reg_path.is_absolute():
                reg_path = EXPORTS_DIR / reg_path.name
        n_reg = export_fixable_regression(enriched_r, reg_path)
        print(f"[已导出] P1 回归可拉回 {n_reg} 条 → {reg_path}")

    if args.export_regression_unfixed is not None:
        if args.export_regression_unfixed == "auto":
            reg_unfixed_path = (
                EXPORTS_DIR
                / f"p1_regression_unfixed_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            )
        else:
            reg_unfixed_path = Path(args.export_regression_unfixed)
            if not reg_unfixed_path.is_absolute():
                reg_unfixed_path = EXPORTS_DIR / reg_unfixed_path.name
        n_reg_unfixed, reg_reasons = export_unfixed_regression(enriched_r, reg_unfixed_path)
        print(f"[已导出] P1 回归未拉回 {n_reg_unfixed} 条 → {reg_unfixed_path}")
        if reg_reasons:
            print("[回归未拉回·捕获诊断分布]")
            for reason, cnt in reg_reasons.most_common():
                print(f"  {reason}: {cnt}")

    if args.export_other_errors is not None:
        if args.export_other_errors == "auto":
            other_path = (
                EXPORTS_DIR
                / f"p1_other_l1_errors_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            )
        else:
            other_path = Path(args.export_other_errors)
            if not other_path.is_absolute():
                other_path = EXPORTS_DIR / other_path.name
        n_other, n_other_fix = export_other_errors_replay(other, enriched_o, other_path)
        print(
            f"[已导出] 其他 L1 错误 {n_other} 条（重放可修 {n_other_fix}）→ {other_path}"
        )

    if args.export_other_fixable is not None:
        if args.export_other_fixable == "auto":
            other_fix_path = (
                EXPORTS_DIR
                / f"p1_other_fixable_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            )
        else:
            other_fix_path = Path(args.export_other_fixable)
            if not other_fix_path.is_absolute():
                other_fix_path = EXPORTS_DIR / other_fix_path.name
        n_other_fixable = export_fixable_other(enriched_o, other_fix_path)
        print(f"[已导出] 其他 L1 可纠偏 {n_other_fixable} 条 → {other_fix_path}")

    text = format_report(stats, compare=args.compare or args.save_baseline, shadow=shadow_rows)
    print(text)

    report_path = PERF_DIR / "reports" / f"{datetime.now().strftime('%Y%m%d_%H%M')}_p1_subset.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text, encoding="utf-8")
    print(f"[已保存] {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
