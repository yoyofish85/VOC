# -*- coding: utf-8 -*-
"""v11 M3：business_guard 误拦回收 + intentional no_capture 保持。"""
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

_INCIDENT_PRAISE = (
    "有感而发：上月月底最后一天，在途经福永高速桥的时候发生了压到大石头，"
    "导致车辆右前轮胎boom了，让我非常糟心，紧接着立马联系上售后兄弟，"
    "深圳门店快速响应，即刻安排拖车前往，一系列的安全操作让我感到非常安心。"
    "深表感谢，为深圳门店售后点赞，强推哈哈哈哈 另外新车forme 已到 即刻开启预约试驾～"
)
_MAINTENANCE_PRAISE = (
    "车辆刚好到第三次保养送去海口店里，接待顾问说此次保养需要更换空调格和刹车油"
    "属于车主权益的保养不收任何费用，店里还安排了把我送去机场，"
    "这服务也就莲花跑车可以做得到了。希望能一直保持下去才能换来更多的老车主的复购"
)


def _cap(text: str, model_l1: str, diag_ok: tuple = ("eligible:v11_m3",)) -> None:
    diag = explain_positive_capture_block(text)
    assert diag in diag_ok, diag
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(text, model_l1, "", l2_map)
    assert l1 == "非问题" and l2 == "" and flags.get("pos_captured")


def _not_cap(text: str, model_l1: str) -> None:
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(text, model_l1, "", l2_map)
    assert l1 == model_l1 and not flags.get("pos_captured")


def test_incident_praise_long_share():
    _cap(_INCIDENT_PRAISE, "服务类")


def test_maintenance_praise():
    _cap(_MAINTENANCE_PRAISE, "产品质量类")


def test_reward_policy_relay():
    _cap(
        "告知客户，ForMe车型按实际交付台数享受阶梯奖励 客户咨询邀请购车的积分阶梯如何计算",
        "服务类",
    )


def test_accident_relay_highway():
    _cap(
        "林先生13957757151，反馈高速发生交通事故，客户告知门店引导用户拨打400协调拖车接力，"
        "已报警，稍后会拖车下高速",
        "服务类",
    )


def test_accident_relay_single_party():
    _cap(
        "19828928052，郭先生，用户反馈车辆发生事故剐蹭，单方事故，交警需要一些数据，需要协助",
        "产品质量类",
    )


def test_app_connect_howto():
    _cap("这个应用程序连接到我的路特斯埃米拉吗？我找不到任何地方的选项？", "产品质量类")


def test_review_photo_howto():
    _cap("18680518911，用户反馈：收货确认评价那里怎样附上图片啊？也不能提交评价", "服务类")


def test_spoiler_relay():
    _cap("我的路特斯Electre R后扰流板损坏，需要更换。", "产品质量类")


def test_network_inquiry():
    _cap(
        "车内网络不工作，询问连接是否正常。",
        "产品质量类",
        ("eligible:v11_m3", "eligible:weak_benign"),
    )


def test_fire_extinguisher_ask():
    _cap(
        "13810158265北京银泰中心工作人员 反馈北京银泰中心闪充站,消防灭火器是2023年的，"
        "询问需不需要更换?今天有检查",
        "产品质量类",
    )


def test_points_purchase_ask():
    _cap("买车有多少积分", "服务类")


def test_tire_coordination():
    _cap(
        "13967588345 用户反馈需要和宁波售后门店对接，需要更换车辆轮胎",
        "服务类",
        ("eligible:v11_m3", "eligible:feedback_service_need"),
    )


def test_reward_dispute_still_blocked():
    full = (
        "周帅，18005813609，LJULMSA47TK003631。用户反馈：5月15号来电询问账户积分被扣除15000，"
        "当时坐席按话术答复用户。23号又被扣除了11400积分，询问什么情况，前后说法不一致。"
        "辛苦老师核实下用户积分发放情况。"
    )
    _not_cap(full, "服务类")
    assert explain_positive_capture_block(full) == "blocked:business_issue_guard"


def test_missing_tier_reward_still_blocked():
    _not_cap("用户咨询邀请了3个人购车，第三台未按阶梯奖励发放", "服务类")


def test_intentional_no_capture_samples():
    samples = [
        ("服务类", "13915511675，范小驰女士，交付回访，表示定金两万还没有退，以及两个车模也没有给自己"),
        ("服务类", "用户反馈购买的手机支架买重了，想要退货。"),
        ("产品质量类", "无锡-瞿子伟先生-Emeya反馈：遮阳棚质感太一般，想要退货"),
        ("服务类", "我写信表达我对我的新Emira持续存在的问题感到沮丧，已经在店里召回了一个多月。"),
        ("服务类", "没有按照我想要的时间正确启动和停止，所以我最终要支付保费！"),
        ("体验需求类", "座椅嘎嘣硬，疼的我嗷嗷叫"),
    ]
    for model_l1, text in samples:
        _not_cap(text, model_l1)
