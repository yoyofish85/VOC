# -*- coding: utf-8 -*-
"""v12 M4-B：回归池 guard 拉回扩展。"""
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


def _pullback(text: str, human_l1: str) -> None:
    l2 = load_l2_whitelist()
    l1, l2_out, flags = apply_classification_post_rules(text, "非问题", "", l2)
    assert l1 == human_l1, (l1, l2_out, flags)
    assert flags.get("non_issue_guard"), flags


def _stay_non_issue(text: str) -> None:
    l2 = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(text, "非问题", "", l2)
    assert l1 == "非问题", l1
    assert not flags.get("non_issue_guard")


def _block_capture(text: str) -> None:
    diag = explain_positive_capture_block(text)
    assert diag in (
        "blocked:v12_regression_pullback",
        "blocked:business_issue_guard",
        "blocked:hard_negative",
    ), diag


def test_service_dispute_pullback():
    _pullback(
        "用户反馈：5月15号来电询问账户积分被扣除15000，23号又被扣除了11400积分，"
        "询问什么情况，前后说法不一致。辛苦老师核实下用户积分发放情况。",
        "服务类",
    )
    _block_capture(
        "用户反馈：5月15号来电询问账户积分被扣除15000，23号又被扣除了11400积分，"
        "询问什么情况，前后说法不一致。"
    )


def test_product_fault_pullback():
    _pullback(
        "用户反馈车辆黑屏死机，无法正常启动，请尽快处理",
        "产品质量类",
    )


def test_experience_issue_pullback():
    l2 = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(
        "用户反馈车机导航功能无法正常使用，希望官方优化改进", "非问题", "", l2
    )
    assert l1 in ("体验需求类", "产品质量类"), l1
    assert flags.get("non_issue_guard")


def test_m3_incident_praise_not_pulled():
    _stay_non_issue(
        "有感而发：轮胎boom了，让我非常糟心，深圳门店快速响应，为深圳门店售后点赞，强推"
    )


def test_m3_accident_relay_not_pulled():
    l2 = load_l2_whitelist()
    l1, _, _ = apply_classification_post_rules(
        "林先生，反馈高速发生交通事故，客户告知门店引导用户拨打400协调拖车接力，已报警",
        "非问题",
        "",
        l2,
    )
    assert l1 == "非问题"


def test_followup_negative_pullback():
    _pullback(
        "试驾回访5星,无建议。交付回访4星,客户反馈上海门店交付中心太寒酸了,拍照都拍不出来",
        "服务类",
    )


def test_trunk_ota_suggest_pullback():
    l2 = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(
        "来源:意见反馈 为什么后备箱可以手机控制开启,但不能关闭,需要手动关闭,建议升级改进",
        "非问题",
        "",
        l2,
    )
    assert l1 == "体验需求类", l1
    assert flags.get("non_issue_guard")


def test_sales_followup_l2_mapping():
    l2 = load_l2_whitelist()
    _, l2_out, flags = apply_classification_post_rules(
        "销售回访，五星，意见：生产太慢了",
        "非问题",
        "",
        l2,
    )
    assert flags.get("non_issue_guard")
    assert l2_out == "销售服务问题", l2_out


def test_testdrive_followup_l2_mapping():
    l2 = load_l2_whitelist()
    _, l2_out, _ = apply_classification_post_rules(
        "试驾回访，客户五星，表示想试驾EMIRA，但是门店没有",
        "非问题",
        "",
        l2,
    )
    assert l2_out == "销售服务问题", l2_out


def test_aftersale_followup_l2_mapping():
    l2 = load_l2_whitelist()
    _, l2_out, _ = apply_classification_post_rules(
        "售后回访，五星 意见：提升一下服务站 告知站内没有休息的地方",
        "非问题",
        "",
        l2,
    )
    assert l2_out == "售后服务问题", l2_out


def test_exp_tilt_cheap_car():
    l2 = load_l2_whitelist()
    l1, l2_out, flags = apply_classification_post_rules(
        "试驾回访，五星，希望车辆能便宜点",
        "非问题",
        "",
        l2,
    )
    assert l1 == "体验需求类", l1
    assert flags.get("non_issue_guard")


def test_prod_ota_signal():
    l2 = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(
        "客户反馈ota车机升级后，信号差，唤醒非常迟钝。",
        "非问题",
        "",
        l2,
    )
    assert l1 == "产品质量类", l1
    assert flags.get("non_issue_guard")


def test_pure_praise_stays():
    _stay_non_issue("销售顾问热情有耐心，服务很好，非常满意，点赞")


def test_neutral_query_delivery_complaint_pullback():
    """r4：neutral_query 误捕获 — 咨询交付+投诉应 guard 拉回服务类。"""
    _pullback(
        "用户来电咨询什么时候交付，对交付进度不满，要求尽快处理",
        "服务类",
    )
    diag = explain_positive_capture_block(
        "用户来电咨询什么时候交付，对交付进度不满，要求尽快处理"
    )
    assert diag in (
        "blocked:v12_regression_pullback",
        "blocked:business_issue_guard",
    ), diag


def test_neutral_query_pure_consult_stays():
    """纯咨询无诉求仍应捕获为非问题。"""
    _stay_non_issue("想咨询一下保养周期和费用")


def test_l1_rebalance_service_to_product_fault():
    """跨类：模型=服务类 + 明确产品故障、无服务主诉 → 产品质量类。"""
    l2 = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(
        "用户反馈车辆黑屏死机，无法正常启动，请尽快处理",
        "服务类",
        "销售服务问题",
        l2,
    )
    assert l1 == "产品质量类", l1
    assert flags.get("l1_category_rebalance")


def test_l1_rebalance_product_to_service_delivery():
    """跨类：模型=产品质量类 + 交付回访 → 服务类。"""
    l2 = load_l2_whitelist()
    l1, l2_out, flags = apply_classification_post_rules(
        "交付回访4星，客户反馈交付中心太寒酸，拍照都拍不出来",
        "产品质量类",
        "故障-通用",
        l2,
    )
    assert l1 == "服务类", l1
    assert flags.get("l1_category_rebalance")
    assert l2_out == "交付问题", l2_out


def test_o1_assistance_overrides_service_to_non_issue():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "车辆扎钉爆胎，需要协助安排道路救援拖车",
        "服务类",
        "售后服务问题",
        l2_map,
    )
    assert (l1, l2) == ("非问题", "")
    assert flags.get("context_non_issue")


def test_o1_does_not_hide_service_complaint():
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(
        "爆胎后联系售后两小时无人响应，投诉服务差",
        "服务类",
        "售后服务问题",
        l2_map,
    )
    assert l1 == "服务类"
    assert not flags.get("context_non_issue")


def test_o1_appointment_inquiry_overrides_service_to_non_issue():
    l2_map = load_l2_whitelist()
    for text in (
        "用户反馈需要预约保养",
        "车辆有什么问题，需要到店检测",
    ):
        l1, l2, flags = apply_classification_post_rules(
            text,
            "服务类",
            "售后服务问题",
            l2_map,
        )
        assert (l1, l2) == ("非问题", ""), text
        assert flags.get("context_non_issue"), text


def test_o3_app_narrative_overrides_experience_to_non_issue():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "周末自驾去了莫干山，一路风景很好，分享日常用车体验",
        "体验需求类",
        "车机智能化",
        l2_map,
        source="APP",
    )
    assert (l1, l2) == ("非问题", "")
    assert flags.get("context_non_issue")


def test_supplier_station_issue_maps_to_lfc():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "国内蔚来充电桩下线，站点无法使用",
        "产品质量类",
        "车端充电问题",
        l2_map,
    )
    assert l1 == "产品质量类"
    assert l2 == "LFC问题"
    assert flags.get("charging_l2_boundary")


def test_vehicle_power_and_scheduled_charge_map_to_car_charging():
    l2_map = load_l2_whitelist()
    cases = (
        ("车辆充电功率低，只有20kW", "LFC问题"),
        ("车辆预约充电没有开始，充不进电", "LFC问题"),
    )
    for text, wrong_l2 in cases:
        l1, l2, flags = apply_classification_post_rules(
            text, "产品质量类", wrong_l2, l2_map
        )
        assert l1 == "产品质量类", text
        assert l2 == "车端充电问题", text
        assert flags.get("charging_l2_boundary"), text
