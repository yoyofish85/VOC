#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 export 导出的微调 JSONL 做 L1 均衡：非问题 ≤ 30%，其余三类全保留 + 体验需求升采样。"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "label_project"))
from taxonomy_normalize import CANONICAL_L1_LABELS, canonicalize_l1_label  # noqa: E402

DEFAULT_INPUT = ROOT / "exports" / "finetune_data.jsonl"
DEFAULT_OUTPUT_DIR = ROOT / "mlx_finetune_balanced"
BIZ_L1 = ("产品质量类", "服务类", "体验需求类")
NON_ISSUE_L1 = "非问题"
NON_ISSUE_MAX_RATIO = 0.30
SPLIT_RATIOS = (0.90, 0.05, 0.05)  # train / valid / test


def _parse_l1(record: Dict[str, Any]) -> str:
    completion = str(record.get("completion") or "").strip()
    if not completion:
        return ""
    try:
        obj = json.loads(completion)
        if isinstance(obj, dict):
            return canonicalize_l1_label(str(obj.get("l1") or ""))
    except json.JSONDecodeError:
        pass
    return canonicalize_l1_label(completion)


def _read_records(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} JSON 解析失败: {exc}") from exc
            if not isinstance(rec, dict) or "prompt" not in rec or "completion" not in rec:
                raise ValueError(f"{path}:{line_no} 缺少 prompt/completion 字段")
            records.append(rec)
    return records


def _upsample(pool: List[Dict[str, Any]], target: int) -> List[Dict[str, Any]]:
    if not pool or target <= 0:
        return []
    if len(pool) >= target:
        return list(pool)
    out: List[Dict[str, Any]] = []
    idx = 0
    while len(out) < target:
        out.append(pool[idx % len(pool)])
        idx += 1
    return out


def balance_records(
    records: List[Dict[str, Any]],
    *,
    non_issue_max_ratio: float = NON_ISSUE_MAX_RATIO,
    seed: int = 42,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """返回均衡后的记录列表与各 L1 计数。"""
    rng = random.Random(seed)
    tagged = [( _parse_l1(rec), rec) for rec in records]
    tagged = [(l1, rec) for l1, rec in tagged if l1 in CANONICAL_L1_LABELS]
    if not tagged:
        raise ValueError("无有效 L1 记录，请确认 completion 含 l1 或可识别的标签")

    by_l1: Dict[str, List[Dict[str, Any]]] = {k: [] for k in CANONICAL_L1_LABELS}
    for l1, rec in tagged:
        by_l1[l1].append(rec)

    keep: Dict[str, List[Dict[str, Any]]] = {}
    for l1 in BIZ_L1:
        keep[l1] = list(by_l1.get(l1, []))

    biz_max = max(len(keep[l1]) for l1 in BIZ_L1) if any(keep[l1] for l1 in BIZ_L1) else 0
    if biz_max > 0 and len(keep.get("体验需求类", [])) < biz_max:
        keep["体验需求类"] = _upsample(keep.get("体验需求类", []), biz_max)

    max_non_issue = max(1, int(len(tagged) * non_issue_max_ratio))
    non_issue_pool = by_l1.get(NON_ISSUE_L1, [])
    n_keep = min(len(non_issue_pool), max_non_issue)
    keep[NON_ISSUE_L1] = rng.sample(non_issue_pool, n_keep) if n_keep else []

    balanced: List[Dict[str, Any]] = []
    for l1 in CANONICAL_L1_LABELS:
        balanced.extend(keep.get(l1, []))
    rng.shuffle(balanced)
    counts = Counter(_parse_l1(rec) for rec in balanced)
    return balanced, dict(counts)


def write_splits(
    records: List[Dict[str, Any]],
    output_dir: Path,
    *,
    seed: int = 42,
) -> Dict[str, int]:
    rng = random.Random(seed)
    shuffled = list(records)
    rng.shuffle(shuffled)
    total = len(shuffled)
    train_end = int(total * SPLIT_RATIOS[0])
    valid_end = train_end + int(total * SPLIT_RATIOS[1])
    splits = {
        "train": shuffled[:train_end],
        "valid": shuffled[train_end:valid_end],
        "test": shuffled[valid_end:],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, int] = {}
    for name, subset in splits.items():
        path = output_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for rec in subset:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        written[name] = len(subset)
    return written


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="微调 JSONL L1 均衡 + MLX 训练集拆分")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="export 导出的 finetune_data.jsonl")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="输出 train/valid/test 目录")
    parser.add_argument("--non-issue-ratio", type=float, default=NON_ISSUE_MAX_RATIO, help="非问题占原始样本上限比例")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args(argv)

    input_path = args.input.resolve()
    if not input_path.is_file():
        print(f"错误：{input_path} 不存在，请先运行 performance_evaluation/export_finetune_from_db.py")
        return 1
    if input_path.stat().st_size == 0:
        print(f"错误：{input_path} 为空，请先导出微调数据")
        return 1

    records = _read_records(input_path)
    raw_counts = Counter(_parse_l1(rec) for rec in records)
    print(f"原始数据: {len(records)} 条")
    print(f"L1 分布: {dict(raw_counts)}")

    balanced, balanced_counts = balance_records(
        records,
        non_issue_max_ratio=args.non_issue_ratio,
        seed=args.seed,
    )
    print(f"\n均衡后: {len(balanced)} 条")
    print(f"L1 分布: {balanced_counts}")
    non_issue_pct = balanced_counts.get(NON_ISSUE_L1, 0) / len(balanced) * 100 if balanced else 0
    print(f"非问题占比: {non_issue_pct:.1f}%")

    if args.dry_run:
        print("\n[dry-run] 未写入文件")
        return 0

    written = write_splits(balanced, args.output_dir.resolve(), seed=args.seed)
    print(f"\n数据已保存到 {args.output_dir.resolve()}/")
    for name, count in written.items():
        print(f"  {name}: {count} 条 → {args.output_dir / f'{name}.jsonl'}")
    print("可直接用于: python3 -m mlx_lm lora --data mlx_finetune_balanced ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
