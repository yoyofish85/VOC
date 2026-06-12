#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从复核库导出 Ollama 微调 JSONL + 固定 holdout 集。

用法：
  python3 performance_evaluation/export_finetune_from_db.py
  python3 performance_evaluation/export_finetune_from_db.py --dry-run
  python3 performance_evaluation/export_finetune_from_db.py --db-path src/backend/opinion_review.db
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
STATE_DIR = PERF_DIR / "state"
EXPORTS_DIR = PROJECT_ROOT / "exports"
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"
HOLDOUT_IDS_PATH = DATA_DIR / "holdout_opinion_ids.json"
TRAIN_JSONL_PATH = EXPORTS_DIR / "finetune_data.jsonl"
HOLDOUT_JSONL_PATH = EXPORTS_DIR / "holdout_data.jsonl"
FINETUNE_STATE_PATH = STATE_DIR / "finetune_trigger.json"

HOLDOUT_SEED = 42
HOLDOUT_RATIO = 0.10
MIN_L1_RATIO = 0.15
TEXT_TRUNCATE = 2000

sys.path.insert(0, str(PROJECT_ROOT / "label_project"))
from taxonomy_normalize import CANONICAL_L1_LABELS, canonicalize_l1_label  # noqa: E402

sys.path.insert(0, str(PERF_DIR))
from evaluate_accuracy import _parse_v3, model_labels  # noqa: E402

PROMPT_TEMPLATE = """你是专业车载 VOC 舆情标签分类专家，严格按照给定标签体系做一级、二级标签分类，严禁自创标签。

【一级标签仅四类（必须从中四选一）】
产品质量类、服务类、体验需求类、非问题

【判定顺序】
1. 是否涉及 LFC/充电/家充桩/占位费/充电功率/地锁/超充/极充？→ 产品质量类
2. 是否含真负面（故障/投诉/不满/体验不良）？→ 按领域归类
3. 是否销售/试驾好评或非充电业务咨询？→ 非问题（l2 留空 ""）

【输出要求】严格 JSON，无解释、无 markdown：
{{"l1":"一级标签","l2":"二级标签"}}
l1 为「非问题」时 l2 必须为 ""；其余 l1 的 l2 须为体系字典中的原文字符串。

【待分类原文】
{text}

请输出 JSON："""


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _collect(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT opinion_id, original_text, review_l1, review_l2,
               v3_label_meta, model_class, model_keyword
        FROM opinion
        WHERE review_status = 1
          AND TRIM(IFNULL(review_l1, '')) != ''
        """
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        oid = str(row["opinion_id"] or "").strip()
        text = str(row["original_text"] or "").strip()
        if not oid or not text:
            continue
        hr1 = str(row["review_l1"] or "").strip()
        hr2 = str(row["review_l2"] or "").strip()
        m1, m2 = model_labels(row)
        out.append(
            {
                "opinion_id": oid,
                "original_text": text,
                "review_l1": hr1,
                "review_l2": hr2,
                "human_l1": canonicalize_l1_label(hr1),
                "model_l1": canonicalize_l1_label(m1) if m1 else "",
                "model_l2": m2,
            }
        )
    return out


def _holdout_count(n: int, ratio: float = HOLDOUT_RATIO) -> int:
    if n <= 1:
        return 0
    return max(1, round(n * ratio))


def _split_holdout(
    records: Sequence[Dict[str, Any]],
    holdout_path: Path,
    *,
    seed: int = HOLDOUT_SEED,
    ratio: float = HOLDOUT_RATIO,
    write_ids: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """固定 seed 划分 holdout；若 holdout 文件已存在则复用 ID 集合。"""
    ids = [str(r["opinion_id"]) for r in records]
    id_set = set(ids)
    by_id = {str(r["opinion_id"]): r for r in records}

    if holdout_path.is_file():
        stored = json.loads(holdout_path.read_text(encoding="utf-8"))
        if isinstance(stored, list) and stored:
            holdout_ids = [x for x in stored if x in id_set]
        else:
            holdout_ids = None
    else:
        holdout_ids = None

    if holdout_ids is None:
        rng = random.Random(seed)
        shuffled = list(ids)
        rng.shuffle(shuffled)
        n_hold = _holdout_count(len(shuffled), ratio)
        holdout_ids = shuffled[:n_hold]
        if write_ids:
            holdout_path.parent.mkdir(parents=True, exist_ok=True)
            holdout_path.write_text(
                json.dumps(holdout_ids, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    holdout_set: Set[str] = set(holdout_ids)
    holdout_rows = [by_id[i] for i in holdout_ids if i in by_id]
    train_rows = [r for r in records if str(r["opinion_id"]) not in holdout_set]
    return train_rows, holdout_rows


def _is_hard_negative(row: Dict[str, Any]) -> bool:
    h1 = str(row.get("human_l1") or "").strip()
    m1 = str(row.get("model_l1") or "").strip()
    return bool(h1 and m1 and h1 != m1)


def _hard_negative_first_sort(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hard = [r for r in records if _is_hard_negative(r)]
    easy = [r for r in records if not _is_hard_negative(r)]
    return hard + easy


def _stratified_sample(
    records: List[Dict[str, Any]],
    *,
    min_l1_ratio: float = MIN_L1_RATIO,
) -> List[Dict[str, Any]]:
    """保证每个 canonical L1 在训练集中占比 ≥ min_l1_ratio（样本不足时保留全量）。"""
    if not records:
        return []
    n = len(records)
    min_per_l1 = max(1, int(n * min_l1_ratio + 0.9999))
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        buckets[str(r.get("human_l1") or "服务类")].append(r)

    selected: List[Dict[str, Any]] = []
    selected_ids: Set[str] = set()
    for l1 in CANONICAL_L1_LABELS:
        pool = buckets.get(l1, [])
        take = pool[:min_per_l1]
        for r in take:
            oid = str(r["opinion_id"])
            if oid not in selected_ids:
                selected.append(r)
                selected_ids.add(oid)

    for r in records:
        oid = str(r["opinion_id"])
        if oid not in selected_ids:
            selected.append(r)
            selected_ids.add(oid)
    return selected


def build_prompt(text: str) -> str:
    clipped = (text or "")[:TEXT_TRUNCATE]
    return PROMPT_TEMPLATE.format(text=clipped)


def build_completion(l1: str, l2: str) -> str:
    payload = {"l1": l1, "l2": l2 or ""}
    return json.dumps(payload, ensure_ascii=False)


def _record_to_jsonl(row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "prompt": build_prompt(str(row.get("original_text") or "")),
        "completion": build_completion(
            str(row.get("review_l1") or ""),
            str(row.get("review_l2") or ""),
        ),
    }


def _write_jsonl(path: Path, records: Sequence[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in records:
            f.write(json.dumps(_record_to_jsonl(row), ensure_ascii=False) + "\n")
    return len(records)


def _build_state(
    *,
    db_path: Path,
    collected: int,
    train_rows: List[Dict[str, Any]],
    holdout_rows: List[Dict[str, Any]],
    dry_run: bool,
) -> Dict[str, Any]:
    hard_n = sum(1 for r in train_rows if _is_hard_negative(r))
    l1_dist = Counter(str(r.get("human_l1") or "") for r in train_rows)
    return {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "db_path": str(db_path.resolve()),
        "total_collected": collected,
        "holdout_count": len(holdout_rows),
        "train_count": len(train_rows),
        "hard_negative_train": hard_n,
        "l1_distribution_train": dict(l1_dist),
        "holdout_ids_path": str(HOLDOUT_IDS_PATH.resolve()),
        "train_jsonl": str(TRAIN_JSONL_PATH.resolve()),
        "holdout_jsonl": str(HOLDOUT_JSONL_PATH.resolve()),
        "holdout_seed": HOLDOUT_SEED,
        "holdout_ratio": HOLDOUT_RATIO,
        "min_l1_ratio": MIN_L1_RATIO,
    }


def export_finetune(
    db_path: Path,
    *,
    dry_run: bool = False,
    holdout_path: Path = HOLDOUT_IDS_PATH,
) -> Dict[str, Any]:
    conn = _connect(db_path)
    try:
        records = _collect(conn)
    finally:
        conn.close()

    train_raw, holdout_rows = _split_holdout(records, holdout_path, write_ids=not dry_run)
    train_sorted = _hard_negative_first_sort(train_raw)
    train_rows = _stratified_sample(train_sorted)

    state = _build_state(
        db_path=db_path,
        collected=len(records),
        train_rows=train_rows,
        holdout_rows=holdout_rows,
        dry_run=dry_run,
    )

    if not dry_run:
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _write_jsonl(TRAIN_JSONL_PATH, train_rows)
        _write_jsonl(HOLDOUT_JSONL_PATH, holdout_rows)
        FINETUNE_STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return state


def _print_summary(state: Dict[str, Any]) -> None:
    mode = "DRY-RUN" if state.get("dry_run") else "EXPORT"
    print(f"[{mode}] 微调数据导出统计")
    print(f"  复核样本总数: {state['total_collected']}")
    print(f"  holdout: {state['holdout_count']} 条")
    print(f"  训练集: {state['train_count']} 条（硬负样本 {state['hard_negative_train']} 条）")
    print(f"  训练集 L1 分布: {state['l1_distribution_train']}")
    if not state.get("dry_run"):
        print(f"  训练 JSONL: {state['train_jsonl']}")
        print(f"  holdout JSONL: {state['holdout_jsonl']}")
        print(f"  holdout IDs: {state['holdout_ids_path']}")
        print(f"  状态文件: {FINETUNE_STATE_PATH}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="从复核库导出微调 JSONL + holdout")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB, help="SQLite 路径")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写 JSONL")
    args = parser.parse_args(argv)

    db_path = args.db_path.resolve()
    if not db_path.is_file():
        print(f"错误：数据库不存在 {db_path}", file=sys.stderr)
        return 1

    try:
        state = export_finetune(db_path, dry_run=args.dry_run)
    except Exception as exc:
        print(f"导出失败: {exc}", file=sys.stderr)
        return 1

    _print_summary(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
