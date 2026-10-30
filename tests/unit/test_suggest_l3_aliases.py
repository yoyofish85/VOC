# -*- coding: utf-8 -*-
"""suggest_l3_aliases / suggest_l3_candidates 单测。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for d in ("label_project", "performance_evaluation"):
    sys.path.insert(0, str(ROOT / d))

from l3_standard_resolver import suggest_l3_candidates  # noqa: E402
from suggest_l3_aliases import suggest  # noqa: E402


def test_suggest_l3_candidates_near_miss():
    out = suggest_l3_candidates("车机黑屏卡顿无法启动", "产品质量类", "座舱问题")
    names = [c for c, _ in out]
    assert "车机异常" in names


def test_suggest_skips_non_pending_and_suggests():
    rows = [
        {"v3_l1": "产品质量类", "v3_l2": "座舱问题", "v3_l3": "通用", "original_text": "车机黑屏卡顿"},
        {"v3_l1": "产品质量类", "v3_l2": "座舱问题", "v3_l3": "车机异常", "original_text": "车机黑屏卡顿"},
    ]
    r = suggest(rows)
    assert len(r) == 1
    assert r[0]["l2"] == "座舱问题"
    assert r[0]["suggestions"]
