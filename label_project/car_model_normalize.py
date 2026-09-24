# -*- coding: utf-8 -*-
"""车型名归一（S7）：大小写/别名合并，多车型串取主车型。"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# 小写键 → 规范显示名
_CANON = {
    "eletre": "Eletre",
    "emeya": "EMEYA",
    "emira": "Emira",
    "evora": "EVORA",
    "exige": "EXIGE",
    "elise": "Elise",
}

_SPLIT = re.compile(r"[,，/;|]+")


def normalize_car_model(raw: Optional[str]) -> str:
    """归一单个车型字段；多车型取第一个可识别项，否则空串。"""
    s = (raw or "").strip()
    if not s:
        return ""
    parts = [p.strip() for p in _SPLIT.split(s) if p.strip()]
    if not parts:
        return ""
    for p in parts:
        key = re.sub(r"\s+", "", p).lower()
        if key in _CANON:
            return _CANON[key]
    # 未知型号：保留清洗后的首段（去多余空格）
    return re.sub(r"\s+", " ", parts[0])[:40]


def normalize_car_models(raw: Optional[str]) -> List[str]:
    """返回去重后的规范车型列表（保序）。"""
    s = (raw or "").strip()
    if not s:
        return []
    out: List[str] = []
    seen = set()
    for p in _SPLIT.split(s):
        n = normalize_car_model(p)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def car_model_fill_stats(rows: List[dict]) -> Tuple[int, int, float]:
    filled = sum(1 for r in rows if normalize_car_model(r.get("car_model")))
    n = len(rows)
    return filled, n, round(filled / max(n, 1), 4)
