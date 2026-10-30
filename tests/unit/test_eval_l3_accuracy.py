# -*- coding: utf-8 -*-
"""eval_l3_accuracy 条件 Top-1 逻辑单测。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "performance_evaluation"))

from eval_l3_accuracy import evaluate  # noqa: E402


def test_evaluate_conditional_top1():
    rows = [
        {"review_l1": "产品质量类", "review_l2": "座舱问题", "review_l3": "车机异常",
         "v3_l1": "产品质量类", "v3_l2": "座舱问题", "v3_l3": "车机异常"},
        {"review_l1": "产品质量类", "review_l2": "座舱问题", "review_l3": "车机异常",
         "v3_l1": "产品质量类", "v3_l2": "座舱问题", "v3_l3": "其他-待归类"},
        {"review_l1": "服务类", "review_l2": "交付问题", "review_l3": "交付时间长",
         "v3_l1": "产品质量类", "v3_l2": "交付问题", "v3_l3": "交付时间长"},
        {"review_l1": "非问题", "review_l2": "", "review_l3": "",
         "v3_l1": "非问题", "v3_l2": "", "v3_l3": ""},
    ]
    r = evaluate(rows)
    assert r["reviewed_with_l3"] == 3
    assert r["denominator"] == 2
    assert r["top1"] == 1
    assert r["top1_rate"] == 0.5
    assert r["by_l2"][0]["l2"] == "座舱问题"


def test_evaluate_empty():
    r = evaluate([])
    assert r["denominator"] == 0
    assert r["top1_rate"] is None
