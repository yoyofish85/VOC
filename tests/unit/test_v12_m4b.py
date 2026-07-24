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


def test_scc_vin_maps_empty_l2_to_emira():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "用户反馈车辆故障灯亮",
        "产品质量类",
        "",
        l2_map,
        vin="SCCLEKAX1RHA12345",
    )
    assert l1 == "产品质量类"
    assert l2 == "Emira问题"
    assert flags.get("vin_vehicle_hint")


def test_scc_vin_does_not_override_generic_fault_l2():
    l2_map = load_l2_whitelist()
    _, l2, flags = apply_classification_post_rules(
        "用户反馈车辆故障灯亮",
        "产品质量类",
        "故障-通用",
        l2_map,
        vin="SCCLEKAX1RHA12345",
    )
    assert l2 == "故障-通用"
    assert not flags.get("vin_vehicle_hint")


def test_lju_vin_does_not_override_emira_label():
    l2_map = load_l2_whitelist()
    _, l2, flags = apply_classification_post_rules(
        "用户反馈车辆故障灯亮",
        "产品质量类",
        "Emira问题",
        l2_map,
        vin="LJUBMSA14RK008070",
    )
    assert l2 == "Emira问题"
    assert not flags.get("vin_vehicle_hint")


def test_o1_does_not_break_correct_lfc_l2():
    l2_map = load_l2_whitelist()
    text = "客户反馈西安金鹰国际购物中心闪充站不能使用，需要回复"
    l1, l2, flags = apply_classification_post_rules(
        text, "产品质量类", "车端充电问题", l2_map
    )
    assert l1 == "产品质量类"
    assert l2 == "车端充电问题"
    assert not flags.get("context_non_issue")


def test_o1_does_not_break_service_pickup_request():
    l2_map = load_l2_whitelist()
    text = "用户反馈：预约了周四的保养，可以安排人过来取车吗"
    l1, l2, flags = apply_classification_post_rules(
        text, "服务类", "交付问题", l2_map
    )
    assert l1 == "服务类"
    assert l2 == "交付问题"
    assert not flags.get("context_non_issue")


def test_o1_expand_fixes_coordination_service_to_non_issue():
    l2_map = load_l2_whitelist()
    cases = (
        "用户询问收到积分提醒，大概有多少数额",
        "用户反馈需要救援，胎压100，目前需要拖车到门店售后更换轮胎",
        "用户反馈有售后维修需求，需要温州售后门店对接",
        "客户反馈今天需要做个保养",
        "用户轮胎扎钉漏气，需要拖车",
        "用户反馈需要预约保养",
    )
    for text in cases:
        l1, l2, flags = apply_classification_post_rules(
            text, "服务类", "售后服务问题", l2_map
        )
        assert (l1, l2) == ("非问题", ""), text
        assert flags.get("context_non_issue"), text


def test_pos_capture_preserves_lfc_station_inquiry():
    l2_map = load_l2_whitelist()
    text = "用户询问西安金鹰国际购物中心闪充站修好了没。催促时间。"
    l1, l2, flags = apply_classification_post_rules(
        text, "产品质量类", "LFC问题", l2_map
    )
    assert l1 == "产品质量类"
    assert l2 == "LFC问题"
    assert not flags.get("pos_captured")


def test_o1_expand_v2_preserves_experience_and_lfc():
    from qwen_ollama import apply_classification_post_rules, load_l2_whitelist

    l2_map = load_l2_whitelist()
    cases = (
        (
            "【2.1.0】用户表示能否OTA下自定义主题",
            "体验需求类",
            "车机智能化",
        ),
        (
            "用户反馈：换充电桩的人什么时候联系我？",
            "产品质量类",
            "LFC问题",
        ),
        (
            "催促：丽水缙云丽缙产业园蔚来充电站尽快上线，方便自己补能",
            "体验需求类",
            "充电建议",
        ),
        (
            "客户反馈拨广州售后门店电话一直没人接听，询问如何迁出ETC",
            "服务类",
            "售后服务问题",
        ),
    )
    for text, l1_in, l2_in in cases:
        l1, l2, flags = apply_classification_post_rules(
            text, l1_in, l2_in, l2_map
        )
        assert l1 == l1_in, text
        assert l2 == l2_in, text
        assert not flags.get("context_non_issue"), text


def test_supplier_charge_abort_keeps_car_charging_l2():
    l2_map = load_l2_whitelist()
    text = (
        "用户反馈在浩瀚能源充电出现充电中止的情况，担心车辆问题，预约明天到售后检查"
    )
    _, l2, flags = apply_classification_post_rules(
        text, "产品质量类", "车端充电问题", l2_map
    )
    assert l2 == "车端充电问题"
    assert not flags.get("charging_l2_boundary")


def test_charging_consult_stays_non_issue_in_full_chain():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "用户咨询成都希顿国际广场闪充站如何开启充电",
        "非问题",
        "",
        l2_map,
    )
    assert (l1, l2) == ("非问题", "")
    assert not flags.get("charging_guard")


def test_station_inquiry_keeps_car_charging_l2():
    l2_map = load_l2_whitelist()
    text = "客户反馈西安金鹰国际购物中心闪充站不能使用，需要回复"
    _, l2, flags = apply_classification_post_rules(
        text, "产品质量类", "车端充电问题", l2_map
    )
    assert l2 == "车端充电问题"
    assert not flags.get("charging_l2_boundary")


def test_station_fault_fixes_empty_l2():
    l2_map = load_l2_whitelist()
    text = "成都希顿国际广场闪充站不能使用"
    _, l2, flags = apply_classification_post_rules(
        text, "产品质量类", "", l2_map
    )
    assert l2 == "LFC问题"
    assert flags.get("charging_l2_boundary")


def test_range_inquiry_stays_non_issue():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "反馈自己车辆充满电之后，续航只有480左右，询问是否正常",
        "非问题",
        "",
        l2_map,
    )
    assert (l1, l2) == ("非问题", "")
    assert not flags.get("non_issue_guard")


def test_o1_v4_broken_gate_regressions():
    """O1 v4：dry-run 5 条 broken 回归 — 保持人工标签不被规则改错。"""
    l2_map = load_l2_whitelist()
    mall = (
        "武汉-邓羽先生-For me 18502792278\n"
        "用户反馈商城购买的支架晃动量大，用户原声：那个地方跟磁吸头没关系，"
        "而且不是用力晃，那个间隙比较大，你们让商城同事拍个其他的，把后面那段晃动我看看"
    )
    wechat = (
        "客户告知需要保养售后门店添加微信对接\n"
        "客户反馈需要售后门店添加微信\n"
        "客户反馈还没人添加微信\n"
        "客户反馈目前还没人添加微信，为其反馈催促"
    )
    noise = (
        "温州-曾冰冰女士-For me，13758855766，LJULMSA3XTK003386，反馈："
        "自己车辆内饰异响去门店检查2次了，上门检查1次还没解决，"
        "想问下去到门店检查维修的话，门店是否可以提供代步车，不想要滴滴打车服务"
    )
    parking = (
        "李先生，手机18553133851，济南用车，19:49:17"
        "反馈近距离代客泊车无法使用及没有代客泊车选项问题。"
    )
    followup = (
        "售后回访，四星，1、用户反馈ETC经常跑着跑着就掉线了，自己咨询过门店，"
        "门店反馈也有其他很多测住有过类似反馈。"
        "2、自己接到销售以及售后的电话，说后摄像头盖板需要更换，"
        "自己到店更换时发现了额外的问题，仪表不显示了油量了，"
        "门店各种检测发现不了问题，后续和自己说需要先将电量跑到30%之后再进店检测油什么的"
    )
    for text in (mall, wechat, noise, followup):
        l1, _, flags = apply_classification_post_rules(
            text, "服务类", "售后服务问题", l2_map
        )
        assert l1 == "服务类", text[:60]
        assert not flags.get("context_non_issue"), text[:60]
        assert not flags.get("pos_captured"), text[:60]
    l1, _, flags = apply_classification_post_rules(parking, "非问题", "", l2_map)
    assert l1 == "非问题"
    assert not flags.get("non_issue_guard")


def test_o1_v41_preserves_l2_on_real_faults():
    """O1 v4.1：context_non_issue 不清空已正确的故障/服务 L2。"""
    l2_map = load_l2_whitelist()
    cases = (
        (
            "用户反馈车辆中控屏提示需要保养了是什么情况",
            "产品质量类",
            "故障告警",
        ),
        (
            "反馈：风油精不小心翻在安全带上了，把安全带弄脏了，需要售后协助自己处理",
            "服务类",
            "售后服务问题",
        ),
        (
            "用户反馈：再此之前有反馈过车辆问题，今天再联系400后，上午北京售后主管联系",
            "服务类",
            "售后服务问题",
        ),
        (
            "卡片钥匙烧蚀了，数字钥匙失效，目前无法启动车辆，需要售后尽快处理",
            "服务类",
            "钥匙问题",
        ),
        (
            "用户反馈:车卡感应不上，希望成都售后安排取车保养",
            "服务类",
            "售后服务问题",
        ),
    )
    for text, l1_in, l2_in in cases:
        l1, l2, flags = apply_classification_post_rules(
            text, l1_in, l2_in, l2_map
        )
        assert not flags.get("context_non_issue"), text[:50]
        assert l1 == l1_in, text[:50]
        if l2_in == "钥匙问题":
            assert l2, text[:50]
        else:
            assert l2 == l2_in, text[:50]


def test_v42_non_issue_guard_preserves_maintenance_consult():
    l2_map = load_l2_whitelist()
    cases = (
        "杭州-曹徐铭先生-Emira  13868189465\n用户表示仪表提示保养了，咨询保养费用",
        "彭俊清先生，18824587276，用户反馈需要明天我安排人把车开过来，你们检修一下，"
        "另外你们可以查一下这个车需不需要进行保养？车的雨刷刮的时候有异响，最好把它换一个",
    )
    for text in cases:
        l1, l2, flags = apply_classification_post_rules(text, "非问题", "", l2_map)
        assert (l1, l2) == ("非问题", ""), text[:60]
        assert not flags.get("non_issue_guard"), text[:60]
