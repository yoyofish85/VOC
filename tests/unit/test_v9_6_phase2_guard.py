# -*- coding: utf-8 -*-
"""v9.6 Phase 2 业务诉求守卫：防止误捕获为非问题。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend"
LABEL = Path(__file__).resolve().parents[2] / "label_project"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(LABEL))

from qwen_ollama import (  # noqa: E402
    _eligible_for_positive_capture,
    apply_classification_post_rules,
    explain_positive_capture_block,
    load_l2_whitelist,
)


def test_points_dispute_not_captured():
    text = "用户反馈商城话题活动积分两个平台都发了，但是只发了800积分"
    assert explain_positive_capture_block(text) == "blocked:business_issue_guard"
    assert not _eligible_for_positive_capture(text)


def test_non_issue_guardrail_pulls_back_points():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        text := "售后积分活动发布了，怎么积分只有一个渠道的",
        "非问题",
        "",
        l2_map,
    )
    assert l1 == "服务类" and flags.get("non_issue_guard")


def test_accident_repair_not_captured():
    text = "车辆发生单方事故，想协调拖车拖至门店维修，需要报价"
    assert not _eligible_for_positive_capture(text)


def test_pure_praise_still_captures():
    text = "销售马女士热情有耐心,讲解很透彻到位"
    assert _eligible_for_positive_capture(text)


def test_charging_guard_regression_unchanged():
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(
        "用户咨询家充桩什么时候到货",
        "非问题",
        "",
        l2_map,
    )
    assert l1 == "产品质量类" and flags.get("charging_guard")
