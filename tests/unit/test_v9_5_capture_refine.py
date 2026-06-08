# -*- coding: utf-8 -*-
"""v9.5 别/特别误匹配、损伤询价、软建议、srv_quality 收窄。"""
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


def test_te_bie_hao_not_negation_blocked():
    text = (
        "保养完还耐心讲解车辆状况和用车建议，态度特别好。真心推荐给莲花车主！"
        "售后服务贴心又靠谱，异地保养首选，值得信赖"
    )
    assert explain_positive_capture_block(text) != "blocked:negation"
    ok, l1, _ = _capture(text, "服务类", "售后服务问题")
    assert ok and l1 == "非问题"


def test_cancel_appointment_not_negation_blocked():
    text = "用户反馈app预约了保养，但今天身体不舒服，要取消预约"
    assert explain_positive_capture_block(text) != "blocked:negation"
    ok, l1, _ = _capture(text)
    assert ok and l1 == "非问题"


def test_damage_quote_inquiry():
    text = "用户反馈：车辆被剐蹭，现在需要售后报价"
    assert _eligible_for_positive_capture(text)
    ok, l1, _ = _capture(text, "服务类", "售后服务问题")
    assert ok and l1 == "非问题"


def test_interest_purchase_inquiry():
    text = "我有兴趣购买Emeya，并有一些问题，也有兴趣试驾。我期待着您的联系。"
    ok, l1, _ = _capture(text, "服务类", "销售服务问题")
    assert ok and l1 == "非问题"


def test_soft_charging_share():
    text = "闪充机器人～接受了一波来自周围其他品牌车主眼神的洗礼～漂亮～"
    assert explain_positive_capture_block(text) != "blocked:lfc_charging_domain"
    ok, l1, _ = _capture(text, "产品质量类", "LFC问题")
    assert ok and l1 == "非问题"


def test_colloquial_praise_crash():
    text = "开繁花出门扎钉瘪胎，原地崩溃，联系莲花跑车中心，移动上门补胎，也太贴心了！"
    assert explain_positive_capture_block(text) != "blocked:hard_negative"
    ok, l1, _ = _capture(text, "服务类", "售后服务问题")
    assert ok and l1 == "非问题"


def test_acknowledged_not_srv_quality_blocked():
    text = "已告知客户后台没有收到申请记录，客户知悉无异议"
    assert explain_positive_capture_block(text) != "blocked:srv_quality_issue"
    ok, l1, _ = _capture(text, "服务类", "销售服务问题")
    assert ok and l1 == "非问题"


def test_real_complaint_still_blocked():
    text = "充电故障投诉,要求退款"
    assert not _eligible_for_positive_capture(text)


def test_charging_guard_regression_unchanged():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "用户咨询家充桩什么时候到货",
        "非问题",
        "",
        l2_map,
    )
    assert l1 == "产品质量类" and flags.get("charging_guard")


def test_owner_auth_inquiry():
    text = "用户反馈购买了一台二手Emeya，需要做车主认证"
    ok, l1, _ = _capture(text, "服务类", "销售服务问题")
    assert ok and l1 == "非问题"


def test_testdrive_high_rating_soft_detail():
    text = (
        "来源：试驾回访，评级星级：4星，试驾车状态：5星，产品好感度：5星，"
        "详细意见：瞬间加速的时候体感不是很好"
    )
    assert explain_positive_capture_block(text) != "blocked:negation"
    ok, l1, _ = _capture(text, "服务类", "试驾体验问题")
    assert ok and l1 == "非问题"


def test_mixed_satisfaction_not_hard_blocked():
    text = "好，非常满意。对车辆也非常满意。不过有个情况就是店里没有标准版的车。无法体会两者的差距。"
    assert explain_positive_capture_block(text) != "blocked:hard_negative"
    ok, l1, _ = _capture(text, "产品质量类", "故障-通用")
    assert ok and l1 == "非问题"
