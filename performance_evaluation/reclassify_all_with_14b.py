#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全量 / 分组 14B 重分类：调用 classify_text()（14B + 当前 v9.x 规则链），写回 v3_label_meta。

【需部署服务器】依赖本地 Ollama Qwen 14B；约 2300 条约 2–3 小时（24 组）。

用法：
  # 全量（M2 门禁后首次写库）
  nohup python3 performance_evaluation/reclassify_all_with_14b.py --all \
    > performance_evaluation/state/reclassify_console.log 2>&1 &

  # 从 checkpoint 续跑（中断后续跑，不加 --all）
  nohup python3 performance_evaluation/reclassify_all_with_14b.py \
    > performance_evaluation/state/reclassify_console.log 2>&1 &

  # 仅补跑失败组（服务器脚本用 --retry-failed；开发机亦支持 --only-failed 同义）
  python3 performance_evaluation/reclassify_all_with_14b.py --retry-failed --dry-run
  python3 performance_evaluation/reclassify_all_with_14b.py --retry-failed

  # 失败组列表见 state/reclassify_failed.json（通常为 group_idx 4,5,6,7）

  # 查看进度
  tail -f performance_evaluation/state/reclassify_console.log
  cat performance_evaluation/state/reclassify_checkpoint.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

PERF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PERF_DIR.parent
STATE_DIR = PERF_DIR / "state"
DEFAULT_DB = PROJECT_ROOT / "src" / "backend" / "opinion_review.db"
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
CHECKPOINT_PATH = STATE_DIR / "reclassify_checkpoint.json"
FAILED_PATH = STATE_DIR / "reclassify_failed.json"

sys.path.insert(0, str(BACKEND_DIR))


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_reviewed_ids(db_path: Path) -> List[str]:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT opinion_id FROM opinion
            WHERE review_status = 1
              AND original_text IS NOT NULL AND TRIM(original_text) != ''
            ORDER BY opinion_id
            """
        ).fetchall()
        return [str(r[0]) for r in rows if r[0]]
    finally:
        conn.close()


def _split_groups(ids: List[str], num_groups: int) -> List[List[str]]:
    if not ids:
        return []
    n = max(1, num_groups)
    groups: List[List[str]] = [[] for _ in range(n)]
    for i, oid in enumerate(ids):
        groups[i % n].append(oid)
    return groups


def _parse_groups_arg(s: str) -> List[int]:
    out: List[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return sorted(set(out))


def _resolve_target_groups(
    args: argparse.Namespace, checkpoint: Dict[str, Any]
) -> List[int]:
    if args.groups:
        return _parse_groups_arg(args.groups)
    if args.only_failed:
        failed_doc = _load_json(FAILED_PATH, {})
        from_file = failed_doc.get("failed_group_idx") or failed_doc.get("failed_groups") or []
        if from_file:
            return sorted({int(x) for x in from_file})
        from_ckpt = checkpoint.get("failed_group_idx") or checkpoint.get("failed_groups") or []
        if from_ckpt:
            return sorted({int(x) for x in from_ckpt})
        print(f"[错误] --only-failed 但 {FAILED_PATH} 与 checkpoint 均无失败组", file=sys.stderr)
        print("请显式指定: --groups 4,5,6,7", file=sys.stderr)
        sys.exit(1)
    completed = set(checkpoint.get("completed_group_idx") or checkpoint.get("completed_groups") or [])
    num_groups = int(checkpoint.get("num_groups") or args.num_groups)
    return [g for g in range(num_groups) if g not in completed]


def main() -> int:
    parser = argparse.ArgumentParser(description="14B 全量/分组重分类写 v3")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--num-groups",
        type=int,
        default=int(os.environ.get("VOC_RECLASSIFY_NUM_GROUPS", "24")),
        help="分组数，默认 24",
    )
    parser.add_argument(
        "--only-failed",
        "--retry-failed",
        action="store_true",
        dest="only_failed",
        help="仅跑失败组（reclassify_failed.json / checkpoint；服务器用 --retry-failed）",
    )
    parser.add_argument(
        "--groups",
        type=str,
        default="",
        help="指定 group_idx，逗号分隔，如 4,5,6,7（0 起，与 checkpoint 一致）",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="清空 checkpoint 后全量重跑（慎用）",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="全量重分：清空 checkpoint 并跑全部 24 组（M2 门禁后写库用这个）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将运行的组与条数，不写库",
    )
    args = parser.parse_args()

    db_path = args.db.resolve()
    if not db_path.is_file():
        print(f"[错误] 数据库不存在: {db_path}", file=sys.stderr)
        return 1

    if args.all:
        args.reset_checkpoint = True

    if args.reset_checkpoint and CHECKPOINT_PATH.is_file():
        CHECKPOINT_PATH.unlink()
        print(f"[已删除] {CHECKPOINT_PATH}")

    checkpoint: Dict[str, Any] = _load_json(CHECKPOINT_PATH, {})
    all_ids = _fetch_reviewed_ids(db_path)
    groups = _split_groups(all_ids, args.num_groups)
    checkpoint.setdefault("num_groups", args.num_groups)
    checkpoint["total_reviewed"] = len(all_ids)
    checkpoint["started_at"] = checkpoint.get("started_at") or datetime.now().isoformat(timespec="seconds")

    target = _resolve_target_groups(args, checkpoint)
    if not target:
        print("[完成] 无待跑分组（均已 completed）")
        return 0

    print(f"数据库: {db_path}")
    print(f"已复核: {len(all_ids)} 条，{args.num_groups} 组，本次跑组: {target}")
    for g in target:
        if 0 <= g < len(groups):
            print(f"  group {g}: {len(groups[g])} 条")
        else:
            print(f"  [警告] group {g} 超出范围 0..{len(groups)-1}", file=sys.stderr)

    if args.dry_run:
        return 0

    from voc_classifier_service import run_batch_classify  # noqa: E402

    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    completed: Set[int] = set(checkpoint.get("completed_group_idx") or [])
    failed: Set[int] = set(checkpoint.get("failed_group_idx") or [])

    for g in target:
        if g < 0 or g >= len(groups):
            continue
        ids = groups[g]
        if not ids:
            completed.add(g)
            failed.discard(g)
            continue
        t0 = time.time()
        print(f"\n=== group {g}/{args.num_groups - 1}  开始 {len(ids)} 条 ===", flush=True)
        try:
            result = run_batch_classify(
                str(db_path),
                opinion_ids=ids,
                use_llm=True,
                ollama_host=host,
            )
            code = int(result.get("code") or 0)
            failed_n = int(result.get("failed_count") or 0)
            if code != 200 or failed_n > 0:
                failed.add(g)
                print(
                    f"[失败] group {g}: code={code} failed={failed_n} msg={result.get('msg')}",
                    flush=True,
                )
                if result.get("errors"):
                    for err in (result["errors"] or [])[:5]:
                        print(f"  {err}", flush=True)
            else:
                completed.add(g)
                failed.discard(g)
                print(
                    f"[成功] group {g}: updated={result.get('updated')} "
                    f"耗时 {time.time()-t0:.0f}s",
                    flush=True,
                )
        except Exception as e:
            failed.add(g)
            print(f"[异常] group {g}: {e}", flush=True)

        checkpoint["completed_group_idx"] = sorted(completed)
        checkpoint["failed_group_idx"] = sorted(failed)
        checkpoint["last_group"] = g
        checkpoint["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_json(CHECKPOINT_PATH, checkpoint)
        if failed:
            _save_json(
                FAILED_PATH,
                {
                    "failed_group_idx": sorted(failed),
                    "updated_at": checkpoint["updated_at"],
                    "num_groups": args.num_groups,
                },
            )

    print("\n=== 汇总 ===")
    print(f"completed: {sorted(completed)}")
    print(f"failed:    {sorted(failed)}")
    if failed:
        print(f"补跑: python3 performance_evaluation/reclassify_all_with_14b.py --retry-failed")
        return 1
    print("验收: python3 performance_evaluation/evaluate_accuracy.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
