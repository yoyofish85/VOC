# -*- coding: utf-8 -*-
"""v9.4 烫金误拦、咨询词表、家充购买、故障+好评豁免。"""
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
    apply_positive_consult_capture,
    explain_positive_capture_block,
    load_l2_whitelist,
)


def _capture(text: str, l1: str = "服务类", l2: str = "销售服务问题"):
    l2_map = load_l2_whitelist()
    nl1, nl2, ok = apply_positive_consult_capture(text, l1, l2, l2_map)
    return ok, nl1, nl2


def test_tangjin_not_hard_blocked():
    text = (
        "新年礼盒涵盖了烫金红包、Spark公仔盲盒，也希望品牌越来越好，"
        "有更多人欣赏车的驾控。"
    )
    assert explain_positive_capture_block(text) != "blocked:hard_negative"
    assert _eligible_for_positive_capture(text)
    ok, l1, l2 = _capture(text, "体验需求类", "OTA建议")
    assert ok and l1 == "非问题" and l2 == ""


def test_exaggerated_praise_not_negation_blocked():
    text = "路特斯新年礼盒不要太好看啦!盲盒我太爱了"
    assert explain_positive_capture_block(text) != "blocked:negation"
    assert _eligible_for_positive_capture(text)
    ok, l1, _ = _capture(text, "产品质量类", "故障-通用")
    assert ok and l1 == "非问题"


def test_home_charger_purchase_consult():
    text = "梁先生需要购买家充桩"
    assert explain_positive_capture_block(text) in (
        "eligible:lfc_consult",
        "eligible:interest_intent",
    )
    ok, l1, l2 = _capture(text, "产品质量类", "LFC问题")
    assert ok and l1 == "非问题" and l2 == ""


def test_feedback_service_need():
    text = "陈欢，15972925350，用户反馈需要取送车上门保养"
    assert _eligible_for_positive_capture(text)
    ok, l1, _ = _capture(text, "服务类", "交付问题")
    assert ok and l1 == "非问题"


def test_short_expectation():
    text = "期待"
    assert explain_positive_capture_block(text) == "eligible:short_intent"
    ok, l1, _ = _capture(text, "产品质量类", "其他")
    assert ok and l1 == "非问题"


def test_resolved_issue_praise():
    text = (
        "Emira门把手故障，预约到佛山莲花更换配件，整个服务流程专业又高效，"
        "必须给五星好评！"
    )
    assert explain_positive_capture_block(text) != "blocked:hard_negative"
    ok, l1, _ = _capture(text, "产品质量类", "故障-通用")
    assert ok and l1 == "非问题"


def test_hope_quick_contact_not_hard_blocked():
    text = "用户再次来电希望尽快和自己联系，需要上门更换轮胎"
    assert explain_positive_capture_block(text) != "blocked:hard_negative"


def test_real_complaint_still_blocked():
    text = "充电故障投诉,要求退款"
    assert not _eligible_for_positive_capture(text)
    ok, l1, _ = _capture(text, "产品质量类", "故障-通用")
    assert not ok and l1 == "产品质量类"


def test_charging_guard_regression_unchanged():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "占位费不认可,充电跳枪无法完成",
        "非问题",
        "",
        l2_map,
    )
    assert l1 == "产品质量类"
    assert flags.get("charging_guard") or flags.get("non_issue_guard")
