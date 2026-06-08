# -*- coding: utf-8 -*-
"""patch 脚本共用：解析 --csv（支持 shell glob 展开为多文件时取最新）。"""
from __future__ import annotations

import glob
import sys
from pathlib import Path
from typing import List, Sequence


def resolve_patch_csv(csv_args: Sequence[str], *, perf_dir: Path) -> Path:
    """解析一个或多个 CSV 路径；glob 或由 shell 展开的多文件时取 mtime 最新。"""
    candidates: List[Path] = []
    for arg in csv_args:
        if any(ch in arg for ch in "*?[]"):
            candidates.extend(Path(p) for p in glob.glob(arg))
            continue
        p = Path(arg)
        if not p.is_file() and (perf_dir / "exports" / p.name).is_file():
            p = perf_dir / "exports" / p.name
        if p.is_file():
            candidates.append(p)
    if not candidates:
        raise FileNotFoundError(f"无可用 CSV: {list(csv_args)}")
    chosen = max(candidates, key=lambda p: p.stat().st_mtime)
    if len(candidates) > 1:
        print(
            f"[CSV] 匹配 {len(candidates)} 个文件，使用最新: {chosen}",
            file=sys.stderr,
        )
    return chosen
