# -*- coding: utf-8 -*-
"""v9.7 Phase 2：症状咨询、交付投诉拉回；社区闲聊豁免。"""
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


def test_ota_progress_symptom_pullback():
    text = "咨询自己车辆的OTA进度，客户暂时不想进店，表示等我们后台处理远程升级"
    assert explain_positive_capture_block(text) == "blocked:business_issue_guard"
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(text, "非问题", "", l2_map)
    assert l1 == "产品质量类" and flags.get("non_issue_guard")


def test_delivery_complaint_five_star():
    text = "销售回访，客户打的是五星。客户表示车子到店了，门店还未交付因为还在走老车主复购审批流程，希望交付可以快一点。"
    assert not _eligible_for_positive_capture(text)
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(text, "非问题", "", l2_map)
    assert l1 == "服务类" and flags.get("non_issue_guard")


def test_glass_damage_inquiry():
    text = "反馈自己在上海售后门店贴膜的，洗车店老板告知自己玻璃坑坑洼洼，客户想要了解是什么原因造成的，可以进店检查"
    assert not _eligible_for_positive_capture(text)
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(text, "非问题", "", l2_map)
    assert l1 == "服务类" and flags.get("non_issue_guard")


def test_community_chatter_still_capturable():
    text = "不懂就问！请问大家的车，都贴车衣了吗？我的Forme还在运输中"
    assert _eligible_for_positive_capture(text)


def test_pure_praise_still_captures():
    text = "销售马女士热情有耐心,讲解很透彻到位"
    assert _eligible_for_positive_capture(text)


def test_charging_guard_regression_unchanged():
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(
        "占位费不认可,充电跳枪无法完成",
        "非问题",
        "",
        l2_map,
    )
    assert l1 == "产品质量类"
    assert flags.get("charging_guard") or flags.get("non_issue_guard")
