#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S6 洞察字段人工标注评分：读取 dry-run CSV（human_ok 已填 1/0），计算一致率。

标注规则（见标注规范）：
  human_ok=1 → 该行所有洞察字段与人工判读一致；
  human_ok=0 → 至少一个字段判错；
  留空 → 未标注，不计入分母。
门禁：已标注样本一致率 ≥85%。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, Sequence


def score(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    annotated = [r for r in rows if str(r.get("human_ok") or "").strip() in ("1", "0")]
    agreed = sum(1 for r in annotated if str(r.get("human_ok") or "").strip() == "1")
    n = len(annotated)
    rate = round(agreed / n, 4) if n else 0.0
    return {
        "total_rows": total,
        "annotated": n,
        "agreed": agreed,
        "disagreed": n - agreed,
        "agreement_rate": rate,
        "gate_pass_85": rate >= 0.85,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="评分 S6 洞察字段人工标注 CSV")
    ap.add_argument("csv_path")
    args = ap.parse_args()
    p = Path(args.csv_path)
    if not p.is_file():
        print(json.dumps({"error": f"csv not found: {p}"}, ensure_ascii=False))
        return 2
    with p.open("r", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f)]
    result = score(rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["gate_pass_85"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
