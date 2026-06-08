# -*- coding: utf-8 -*-
"""v9.7b Phase B：硬负面豁免扩展。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend"
LABEL = Path(__file__).resolve().parents[2] / "label_project"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(LABEL))

from qwen_ollama import (  # noqa: E402
    apply_classification_post_rules,
    explain_positive_capture_block,
    load_l2_whitelist,
)


def test_survey_high_rating_capture():
    text = (
        "试驾回访，评级星级5星。产品好感度5星，人员服务意识4星，"
        "客户表示整体还是很好"
    )
    diag = explain_positive_capture_block(text)
    assert diag != "blocked:hard_negative"
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(text, "服务类", "销售服务问题", l2_map)
    assert l1 == "非问题" and flags.get("pos_captured")


def test_relay_benign_capture():
    text = "路特斯布里斯托尔建议车辆将继续维修，客户要求延长他的租车。"
    assert explain_positive_capture_block(text) != "blocked:hard_negative"
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(text, "服务类", "", l2_map)
    assert l1 == "非问题" and flags.get("pos_captured")


def test_resolved_issue_praise_capture():
    text = "之前胎压报警亮了一会，门店处理及时，客户非常满意并感谢顾问专业。"
    assert not explain_positive_capture_block(text).startswith("blocked:hard_negative")
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(text, "产品质量类", "故障-通用", l2_map)
    assert l1 == "非问题" and flags.get("pos_captured")


def test_soft_hope_suggest_capture():
    text = "希望官方可以增加更多forme配件上架"
    diag = explain_positive_capture_block(text)
    assert diag != "blocked:hard_negative"
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(text, "体验需求类", "", l2_map)
    assert l1 == "非问题" and flags.get("pos_captured")


def test_real_complaint_still_blocked():
    text = "投诉售后态度非常差，要求退款赔偿，必须尽快解决"
    assert explain_positive_capture_block(text) == "blocked:hard_negative"
    l2_map = load_l2_whitelist()
    l1, _, _ = apply_classification_post_rules(text, "服务类", "", l2_map)
    assert l1 == "服务类"


def test_symptom_pullback_regression():
    text = "咨询自己车辆的OTA进度，客户暂时不想进店"
    assert explain_positive_capture_block(text) == "blocked:business_issue_guard"
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(text, "非问题", "", l2_map)
    assert l1 == "产品质量类" and flags.get("non_issue_guard")
