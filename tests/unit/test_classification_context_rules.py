# -*- coding: utf-8 -*-
"""classification_context_rules 单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend"
sys.path.insert(0, str(BACKEND))

from classification_context_rules import (  # noqa: E402
    app_narrative_should_be_non_issue,
    assistance_should_be_non_issue,
    charging_l2_target,
    vehicle_family,
)


def test_assistance_request_is_non_issue():
    assert assistance_should_be_non_issue("车辆扎钉爆胎，需要安排道路救援拖车")
    assert assistance_should_be_non_issue("咨询保养预约，需要协助安排时间")
    assert assistance_should_be_non_issue("用户反馈需要预约保养")
    assert assistance_should_be_non_issue("车辆有什么问题，需要到店检测")


def test_assistance_with_service_complaint_is_not_captured():
    assert not assistance_should_be_non_issue("爆胎后联系售后两小时无人响应，投诉服务差")
    assert not assistance_should_be_non_issue("保养后车辆故障无法启动，要求维修")
    assert not assistance_should_be_non_issue(
        "车辆发生单方事故，想协调拖车拖至门店维修，需要报价"
    )
    assert not assistance_should_be_non_issue(
        "用户反馈：车辆黑屏无法启动，需要到店检测维修"
    )
    assert not assistance_should_be_non_issue(
        "保养后车辆故障无法启动，要求到店检测"
    )


def test_app_narrative_is_non_issue_only_for_app_source():
    text = "周末自驾去了莫干山，一路风景很好，分享一下日常用车体验"
    assert app_narrative_should_be_non_issue(text, source="APP")
    assert not app_narrative_should_be_non_issue(text, source="工单")


def test_app_narrative_with_explicit_fault_is_not_captured():
    assert not app_narrative_should_be_non_issue(
        "自驾途中车机黑屏无法导航，要求尽快解决", source="APP"
    )
    assert not app_narrative_should_be_non_issue(
        "分享一下ForMe的后备箱，再弄个收纳箱和储物网兜", source="APP"
    )


def test_vehicle_family_from_vin():
    assert vehicle_family("LJUBMSA14RK008070") == "electric"
    assert vehicle_family("SCCLEKAX1RHA12345") == "emira"
    assert vehicle_family("") == ""


def test_charging_station_and_supplier_are_lfc():
    assert charging_l2_target("蔚来充电桩已下线，站点无法使用") == "LFC问题"
    assert charging_l2_target("浩瀚供应商的闪充站一直不可用") == "LFC问题"


def test_vehicle_side_charging_is_car_charging():
    assert charging_l2_target("车辆充电功率低，只有20kW") == "车端充电问题"
    assert charging_l2_target("预约充电后车辆没有开始充电") == "车端充电问题"


def test_post_rules_accept_context_without_changing_default_behavior():
    from qwen_ollama import apply_classification_post_rules, load_l2_whitelist

    l2_map = load_l2_whitelist()
    legacy = apply_classification_post_rules("纯表扬", "非问题", "", l2_map)
    contextual = apply_classification_post_rules(
        "纯表扬", "非问题", "", l2_map, source="", vin=""
    )
    assert legacy == contextual
