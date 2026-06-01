#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VOC_V1.5 模型识别准确率评估（只读 SQLite，不写库、不删数据）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 项目根 = performance_evaluation 的上一级
PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
STATE_PATH = PERF_DIR / "state" / "last_eval.json"
REPORTS_DIR = PERF_DIR / "reports"
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
try:
    from taxonomy_normalize import (  # noqa: E402
        L2_EVAL_L1,
        canonicalize_l1_label,
    )
except ImportError as exc:  # pragma: no cover - CLI startup guard
    tp = PROJECT_ROOT / "label_project" / "taxonomy_normalize.py"
    hint = (
        f"请确认存在 {tp}，且含 L2_EVAL_L1（步骤1 新版 taxonomy_normalize）。"
        if not tp.is_file()
        else "taxonomy_normalize.py 已存在但版本过旧，请同步开发机上的 label_project/taxonomy_normalize.py。"
    )
    raise SystemExit(
        f"无法导入 label_project/taxonomy_normalize.py：{exc}\n{hint}"
    ) from exc


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    p = db_path.resolve()
    if not p.is_file():
        raise FileNotFoundError(f"数据库文件不存在: {p}")
    uri = f"{p.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_v3(meta: Optional[str]) -> Tuple[str, str]:
    if not meta or not str(meta).strip():
        return "", ""
    try:
        j = json.loads(meta) if isinstance(meta, str) else meta
        if not isinstance(j, dict):
            return "", ""
        return str(j.get("l1") or "").strip(), str(j.get("l2") or "").strip()
    except (json.JSONDecodeError, TypeError, ValueError):
        return "", ""


def _l2_from_keyword(mkw: Any) -> str:
    s = str(mkw or "").strip()
    if not s:
        return ""
    return s.split(",", 1)[0].strip()


def model_labels(row: sqlite3.Row) -> Tuple[str, str]:
    """与报表/复核口径一致：优先 V3，其次 model_class / model_keyword。"""
    l1, l2 = _parse_v3(row["v3_label_meta"] if "v3_label_meta" in row.keys() else None)
    if not l1:
        l1 = str(row["model_class"] or "").strip()
    if not l2:
        l2 = _l2_from_keyword(row["model_keyword"] if "model_keyword" in row.keys() else "")
    return l1, l2


def normalize_l2(s: str) -> str:
    return " ".join((s or "").strip().split())


def _l2_eval_applicable(human_l1_canon: str, model_l1_canon: str) -> bool:
    """二级准确率：仅业务三类，且一级模型与人工一致。"""
    return human_l1_canon in L2_EVAL_L1 and human_l1_canon == model_l1_canon


def fetch_reviewed_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT opinion_id, review_status, review_l1, review_l2,
               v3_label_meta, model_class, model_keyword, reviewed_at, create_time
        FROM opinion
        WHERE review_status = 1
        """
    )
    return cur.fetchall()


def count_all_reviewed(conn: sqlite3.Connection) -> int:
    r = conn.execute(
        "SELECT COUNT(*) AS c FROM opinion WHERE review_status = 1"
    ).fetchone()
    return int(r["c"] or 0) if r else 0


def evaluate(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = fetch_reviewed_rows(conn)
    l1_pairs: List[Tuple[str, str, str, str]] = []
    l2_pairs: List[Tuple[str, str]] = []
    l1_wrong_counter: Counter = Counter()
    l2_wrong_counter: Counter = Counter()

    skipped_no_human_l1 = 0
    skipped_no_model_l1 = 0
    skipped_l2_nonissue_or_mismatch = 0
    skipped_l2_no_human_l2 = 0

    for row in rows:
        hr1 = str(row["review_l1"] or "").strip()
        if not hr1:
            skipped_no_human_l1 += 1
            continue
        m1_raw, m2_raw = model_labels(row)
        if not m1_raw:
            skipped_no_model_l1 += 1
            continue
        h1c = canonicalize_l1_label(hr1)
        m1c = canonicalize_l1_label(m1_raw)
        l1_pairs.append((m1_raw, m1c, hr1, h1c))
        if m1c != h1c:
            key = f"{m1c} → {h1c}"
            l1_wrong_counter[key] += 1

        if not _l2_eval_applicable(h1c, m1c):
            skipped_l2_nonissue_or_mismatch += 1
            continue

        hr2 = normalize_l2(str(row["review_l2"] or ""))
        if not hr2:
            skipped_l2_no_human_l2 += 1
            continue
        m2n = normalize_l2(m2_raw)
        l2_pairs.append((m2n, hr2))
        if m2n != hr2:
            l2_wrong_counter[f"{m2n or '（模型空缺）'} → {hr2}"] += 1

    n1 = len(l1_pairs)
    c1 = sum(1 for _, mc, _, hc in l1_pairs if mc == hc)
    l1_acc = (c1 / n1) if n1 else 0.0

    n2 = len(l2_pairs)
    c2 = sum(1 for mn, hr2 in l2_pairs if mn == hr2)
    l2_acc = (c2 / n2) if n2 else 0.0

    top_l1_wrong = l1_wrong_counter.most_common(10)
    top_l2_wrong = l2_wrong_counter.most_common(10)

    return {
        "total_rows_reviewed_in_db": len(rows),
        "l1_denominator": n1,
        "l1_correct": c1,
        "l1_wrong": n1 - c1,
        "l1_accuracy": l1_acc,
        "l2_denominator": n2,
        "l2_correct": c2,
        "l2_wrong": n2 - c2,
        "l2_accuracy": l2_acc,
        "skipped_no_human_l1": skipped_no_human_l1,
        "skipped_no_model_l1": skipped_no_model_l1,
        "skipped_l2_nonissue_or_mismatch": skipped_l2_nonissue_or_mismatch,
        "skipped_l2_no_human_l2": skipped_l2_no_human_l2,
        "top_l1_wrong": top_l1_wrong,
        "top_l2_wrong": top_l2_wrong,
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
    }


def evaluate_qwen_shadow(conn: sqlite3.Connection, db_path: Path, limit: int) -> Optional[Dict[str, Any]]:
    """只读影子评估：调用 14B 对已复核样本重新推理，不写回 v3_label_meta。"""
    if limit <= 0:
        return None
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    try:
        from qwen_ollama import classify_text
    except Exception as exc:
        return {"error": f"无法导入 qwen_ollama: {exc}"}
    rows = conn.execute(
        """SELECT opinion_id, original_text, country, review_l1, review_l2
           FROM opinion
           WHERE review_status = 1
           AND TRIM(IFNULL(review_l1,'')) != ''
           ORDER BY COALESCE(reviewed_at, create_time, '') DESC
           LIMIT ?""",
        [limit],
    ).fetchall()
    l1_total = l1_ok = l2_total = l2_ok = 0
    errors: Counter = Counter()
    failed = 0
    for row in rows:
        try:
            pred = classify_text(
                row["original_text"] or "",
                db_path=str(db_path),
                country=row["country"] or "",
            )
        except Exception:
            failed += 1
            continue
        if pred.get("needs_review") or pred.get("match_type") in (
            "qwen_parse_failed",
            "qwen_whitelist_reject",
        ):
            failed += 1
            continue
        human_l1 = canonicalize_l1_label(row["review_l1"])
        pred_l1 = canonicalize_l1_label(pred.get("l1"))
        l1_total += 1
        if pred_l1 == human_l1:
            l1_ok += 1
        else:
            errors[f"{pred_l1} → {human_l1}"] += 1
        if _l2_eval_applicable(human_l1, pred_l1):
            human_l2 = normalize_l2(str(row["review_l2"] or ""))
            if human_l2:
                l2_total += 1
                if normalize_l2(str(pred.get("l2") or "")) == human_l2:
                    l2_ok += 1
    return {
        "sample_limit": limit,
        "evaluated": l1_total,
        "failed": failed,
        "l1_accuracy": (l1_ok / l1_total) if l1_total else 0.0,
        "l2_accuracy": (l2_ok / l2_total) if l2_total else 0.0,
        "top_l1_errors": errors.most_common(10),
    }


def load_last_eval() -> Optional[Dict[str, Any]]:
    if not STATE_PATH.is_file():
        return None
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_last_eval(
    payload: Dict[str, Any],
    db_path: Path,
    cumulative_reviewed: int,
) -> None:
    out = {
        "eval_finished_at": payload["evaluated_at"],
        "l1_accuracy": round(payload["l1_accuracy"], 6),
        "l2_accuracy": round(payload["l2_accuracy"], 6),
        "l1_denominator": payload["l1_denominator"],
        "l2_denominator": payload["l2_denominator"],
        "cumulative_reviewed_count": cumulative_reviewed,
        "db_abspath": str(db_path.resolve()),
        "l2_metric_scope": "business_l1_only_l1_match",
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def format_report(
    res: Dict[str, Any],
    last: Optional[Dict[str, Any]],
    cumulative_reviewed: int,
    db_path: Path,
    qwen_shadow: Optional[Dict[str, Any]] = None,
) -> str:
    lines: List[str] = []
    w = 60

    def bar(title: str) -> None:
        lines.append("=" * w)
        lines.append(f" {title}")
        lines.append("=" * w)

    bar("VOC_V1.5 模型识别准确率评估（只读）")
    lines.append(f"评估时间：{res['evaluated_at']}")
    lines.append(f"数据库（只读）：{db_path.resolve()}")
    lines.append("")
    lines.append("【本次评估结果】")
    lines.append(f"  库内已复核总条数（review_status=1）：{res['total_rows_reviewed_in_db']} 条")
    lines.append(
        f"  参与一级评估条数：{res['l1_denominator']}（无人工一级 {res['skipped_no_human_l1']}，无模型一级 {res['skipped_no_model_l1']}）"
    )
    lines.append(f"  一级正确 / 错误：{res['l1_correct']} / {res['l1_wrong']}")
    lines.append(f"  一级标签准确率：{res['l1_accuracy'] * 100:.2f}%")
    lines.append(
        f"  参与二级评估条数（业务三类且一级一致）：{res['l2_denominator']}"
        f"（非问题/一级不一致跳过 {res['skipped_l2_nonissue_or_mismatch']}，无人工二级 {res['skipped_l2_no_human_l2']}）"
    )
    lines.append(f"  二级正确 / 错误：{res['l2_correct']} / {res['l2_wrong']}")
    lines.append(f"  二级标签准确率（业务三类）：{res['l2_accuracy'] * 100:.2f}%")
    lines.append("")
    lines.append("【一级错误 Top10（模型规范类 → 人工规范类）】")
    for i, (k, v) in enumerate(res["top_l1_wrong"], 1):
        lines.append(f"  {i}. {k} ({v}条)")
    if not res["top_l1_wrong"]:
        lines.append("  （无一级错误，或与上述统计口径下全部正确）")
    lines.append("")
    lines.append("【二级错误 Top10（模型二级 → 人工二级，仅业务三类）】")
    for i, (k, v) in enumerate(res["top_l2_wrong"], 1):
        lines.append(f"  {i}. {k} ({v}条)")
    if not res["top_l2_wrong"]:
        lines.append("  （无二级错误，或业务三类下全部正确）")
    lines.append("")

    if last:
        lines.append("【与上次评估对比】")
        la1 = last.get("l1_accuracy", 0) * 100
        la2 = last.get("l2_accuracy", 0) * 100
        d1 = res["l1_accuracy"] * 100 - la1
        d2 = res["l2_accuracy"] * 100 - la2
        lines.append(f"  上次评估时间：{last.get('eval_finished_at', '—')}")
        lines.append(f"  上次一级准确率：{la1:.2f}%")
        lines.append(f"  本次一级准确率：{res['l1_accuracy'] * 100:.2f}%  （变化：{d1:+.2f}%）")
        lines.append(f"  上次二级准确率（业务三类）：{la2:.2f}%")
        lines.append(f"  本次二级准确率（业务三类）：{res['l2_accuracy'] * 100:.2f}%  （变化：{d2:+.2f}%）")
        prev_c = int(last.get("cumulative_reviewed_count", 0))
        new_n = max(0, cumulative_reviewed - prev_c)
        lines.append(
            f"  两次评估间新增已复核数据（全库累计差）：{new_n} 条（上次累计 {prev_c} → 本次累计 {cumulative_reviewed}）"
        )
    else:
        lines.append("【与上次评估对比】")
        lines.append("  （首次运行，已生成基准；下次运行将显示升降对比）")

    if qwen_shadow:
        lines.append("")
        lines.append("【Qwen2.5-14B 影子评估（只读，不写库）】")
        if qwen_shadow.get("error"):
            lines.append(f"  未完成：{qwen_shadow['error']}")
        else:
            lines.append(
                f"  样本上限：{qwen_shadow['sample_limit']}；完成：{qwen_shadow['evaluated']}；失败：{qwen_shadow['failed']}"
            )
            lines.append(f"  14B 一级准确率：{qwen_shadow['l1_accuracy'] * 100:.2f}%")
            lines.append(f"  14B 二级准确率：{qwen_shadow['l2_accuracy'] * 100:.2f}%")
            lines.append("  14B 一级错误 Top10：")
            for i, (k, v) in enumerate(qwen_shadow.get("top_l1_errors") or [], 1):
                lines.append(f"    {i}. {k} ({v}条)")

    lines.append("")
    lines.append("【说明】")
    lines.append("  · 一级/二级「模型侧」优先 V3 JSON，否则 CSV 导入的 model_class/model_keyword")
    lines.append("  · 一级人工侧为 review_l1 规范到四类；二级为 review_l2 与模型二级字符串精确对比（规范化空白）")
    lines.append("  · 二级准确率仅统计「产品质量/服务/体验需求」且一级模型=人工一致；「非问题」不参与 L2")
    lines.append("  · 「非问题」只要一级正确即视为识别成功，不要求二级标签")
    lines.append("  · 本脚本未修改任何数据；历史状态见 performance_evaluation/state/last_eval.json")
    lines.append("=" * w)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="VOC 模型准确率评估（只读）")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite 路径，默认 {DEFAULT_DB}",
    )
    parser.add_argument(
        "--qwen-shadow-limit",
        type=int,
        default=0,
        help="只读调用 Qwen2.5-14B 重新推理最近 N 条已复核样本，用于新旧模型对比；默认 0 不调用模型。",
    )
    args = parser.parse_args()
    db_path = args.db

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    last = load_last_eval()
    conn = _connect_readonly(db_path)
    try:
        cumulative = count_all_reviewed(conn)
        res = evaluate(conn)
        qwen_shadow = evaluate_qwen_shadow(conn, db_path, int(args.qwen_shadow_limit or 0))
    finally:
        conn.close()

    text = format_report(res, last, cumulative, db_path, qwen_shadow)

    # 控制台
    print(text)

    # 报告文件
    fname = datetime.now().strftime("%Y%m%d_%H%M_report.txt")
    report_path = REPORTS_DIR / fname
    report_path.write_text(text, encoding="utf-8")
    print(f"\n[已保存] {report_path}")

    save_last_eval(res, db_path, cumulative)
    print(f"[已更新基准] {STATE_PATH}（供下次对比）\n")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as e:
        print(f"[错误] {e}", file=sys.stderr)
        raise SystemExit(1)
