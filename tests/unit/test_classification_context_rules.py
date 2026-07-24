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
    service_coordination_should_be_non_issue,
    should_apply_context_non_issue,
    should_skip_charging_l2_flip,
    station_status_inquiry_preserves_lfc,
    vehicle_family,
)


def test_assistance_request_is_non_issue():
    assert assistance_should_be_non_issue("车辆扎钉爆胎，需要安排道路救援拖车")
    assert assistance_should_be_non_issue("咨询保养预约，需要协助安排时间")
    assert assistance_should_be_non_issue("用户反馈需要预约保养")
    assert assistance_should_be_non_issue("车辆有什么问题，需要到店检测")
    assert assistance_should_be_non_issue(
        "阮婷婷用户轮胎扎钉漏气，需要拖车到门店售后更换轮胎"
    )
    assert assistance_should_be_non_issue("开繁花出门扎钉瘪胎，联系中心移动上门补胎")
    assert assistance_should_be_non_issue("客户反馈今天需要做个保养")
    assert assistance_should_be_non_issue("用户反馈有售后维修需求，需要温州售后门店对接")
    assert assistance_should_be_non_issue(
        "用户轮胎爆胎，需要拖车，要求售后支持安排道路救援"
    )
    assert assistance_should_be_non_issue("用户反馈需要补胎，联系门店安排移动上门")


def test_assistance_blocks_service_delay_and_fault():
    assert not assistance_should_be_non_issue(
        "客户反馈预约了门店保养一直没人联系，拨打座机电话显示空号，需要门店主动联系对接"
    )
    assert not assistance_should_be_non_issue(
        "用户反馈车辆在地库，轮胎被扎钉了，需要移动上门服务来处理，再次致电，表示已经过了这么久，为什么还没"
    )
    assert not assistance_should_be_non_issue(
        "三角钥匙无法使用，要求安排上门处理"
    )


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
    assert not assistance_should_be_non_issue(
        "客户反馈目前还没人添加微信，为其反馈催促"
    )
    assert not service_coordination_should_be_non_issue(
        "反馈：内饰异响去门店检查2次了，上门检查1次还没解决，门店是否可以提供代步车"
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
    assert charging_l2_target("蔚来闪充站修好了没，询问什么时候维修完成") == "LFC问题"
    assert charging_l2_target("充电桩已下线，需要回复进度") == "LFC问题"


def test_station_status_inquiry_does_not_force_lfc():
    assert charging_l2_target("西安金鹰国际购物中心闪充站不能使用，需要回复") == ""
    assert charging_l2_target("闪充站修好了没，询问什么时候维修完成") == ""


def test_weak_station_non_inquiry_maps_to_lfc():
    assert charging_l2_target("成都希顿国际广场闪充站不能使用") == "LFC问题"


def test_skip_charging_flip_on_inquiry_only():
    text = "西安金鹰国际购物中心闪充站不能使用，需要回复"
    assert should_skip_charging_l2_flip(text, "车端充电问题", "LFC问题")
    assert not should_skip_charging_l2_flip(text, "", "LFC问题")
    assert not should_skip_charging_l2_flip(
        "蔚来充电桩已下线，需要回复", "车端充电问题", "LFC问题"
    )


def test_skip_lfc_to_car_when_station_domain_present():
    text = "浩瀚闪充站一直不可用，车辆预约充电没有开始"
    assert should_skip_charging_l2_flip(text, "LFC问题", "车端充电问题")


def test_context_non_issue_skips_confirmed_charging_l2():
    text = "成都希顿国际广场闪充站不能使用"
    assert not should_apply_context_non_issue(
        text, "产品质量类", "LFC问题", vin=""
    )
    assert should_apply_context_non_issue(
        "用户反馈需要预约保养", "产品质量类", "LFC问题", vin=""
    )


def test_context_non_issue_skips_confirmed_service_l2():
    text = "用户反馈：预约了周四的保养，可以安排人过来取车吗"
    assert not should_apply_context_non_issue(
        text, "服务类", "交付问题", vin=""
    )


def test_service_coordination_from_recent_error_pool():
    samples = (
        "用户询问收到积分提醒，大概有多少数额",
        "用户反馈需要救援，胎压100，目前需要拖车到门店售后更换轮胎",
        "用户反馈下了个维修单，需要为其催促确认下",
        "北京客户反馈购买了商城车膜，需要门店主动来联系对接，什么时候可以进店贴膜",
        "用户反馈有售后维修需求，需要温州售后门店对接",
        "客户反馈今天需要做个保养",
        "用户反馈想下午前往哈尔滨门店看一看FORME",
        "接到夏季专属服务活动通知，售后服务还是挺到位的，耐心解答",
    )
    for text in samples:
        assert service_coordination_should_be_non_issue(text), text
        assert should_apply_context_non_issue(
            text, "服务类", "售后服务问题", vin=""
        ), text


def test_service_coordination_blocks_real_complaints():
    assert not service_coordination_should_be_non_issue(
        "保养后车辆落锁差点夹手，需要检查车辆"
    )
    assert not should_apply_context_non_issue(
        "保养后车辆落锁差点夹手，需要检查车辆",
        "服务类",
        "售后服务问题",
        vin="",
    )


def test_station_inquiry_preserves_lfc_for_pos_capture():
    assert station_status_inquiry_preserves_lfc(
        "用户询问西安金鹰国际购物中心闪充站修好了没。催促时间。"
    )


def test_car_side_dominant_skips_lfc_flip():
    text = (
        "用户反馈在浩瀚能源充电出现充电中止，担心车辆问题，预约明天到售后检查"
    )
    assert should_skip_charging_l2_flip(text, "车端充电问题", "LFC问题")
    text2 = "客户反馈充电桩故障，充一会会停止充电，桩显示红色，再次启动后同样几分钟后显示红色"
    assert should_skip_charging_l2_flip(text2, "车端充电问题", "LFC问题")


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
