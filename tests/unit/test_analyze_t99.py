# -*- coding: utf-8 -*-
"""analyze_t99 归因逻辑单测。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "performance_evaluation"))
sys.path.insert(0, str(ROOT / "label_project"))

from theme_lookup import load_theme_mapping  # noqa: E402
from analyze_t99 import analyze, effective_l123  # noqa: E402


def _one_mapped_path():
    mp = load_theme_mapping()
    return next(iter((mp.get("l3_mapping") or {}).keys())).split("/", 2)


def test_analyze_classifies_t99():
    l1, l2, l3 = _one_mapped_path()
    rows = [
        {"review_l1": l1, "review_l2": l2, "review_l3": l3},                      # 命中主题
        {"v3_l1": "产品质量类", "v3_l2": "座舱问题", "v3_l3": "通用"},             # 缺失/未消化
        {"v3_l1": "产品质量类", "v3_l2": "座舱问题", "v3_l3": "不存在的L3"},        # 未映射
    ]
    r = analyze(rows)
    assert r["total"] == 3
    assert r["mapped"] == 1
    assert r["t99_count"] == 2
    assert r["t99_missing_label"] == 1
    assert r["t99_unmapped_l3"] == 1


def test_effective_l123_review_priority():
    row = {
        "review_l1": "服务类", "review_l2": "交付问题", "review_l3": "交付时间长",
        "v3_l1": "产品质量类", "v3_l2": "座舱问题", "v3_l3": "车机异常",
    }
    assert effective_l123(row) == ("服务类", "交付问题", "交付时间长")
