# -*- coding: utf-8 -*-
"""v9.8 no_capture 第二轮扩展。"""
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


def _cap(text: str, model_l1: str, diag_ok: tuple = ("eligible:v98_no_capture",)) -> None:
    diag = explain_positive_capture_block(text)
    assert diag in diag_ok, diag
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(text, model_l1, "", l2_map)
    assert l1 == "非问题" and l2 == "" and flags.get("pos_captured")


def test_account_admin():
    _cap("常州-曹贾迎女士-Forme,需要变更手机号,新手机和旧手机号都是自己名下,需要保留车辆权益", "服务类")


def test_collision_assist():
    _cap(
        "15800656025王先生用户车辆发生了碰撞,需要协助。",
        "服务类",
        ("eligible:v98_no_capture", "eligible:assist_coordination"),
    )


def test_want_key_door_service():
    _cap("用户反馈:想要配个卡片钥匙,想要门店上门", "服务类")


def test_workslot_inquiry():
    _cap("用户反馈:明天上海售后门店有空闲工位吗", "服务类")


def test_followup_unreachable():
    _cap("用户再次进线表示之前有对接过,今天联系不上,电话没人接", "服务类")


def test_delivery_schedule():
    _cap("伍先生,17628236557,用户Eletre车辆下定后交付时间", "服务类")


def test_relay_rsa_feedback():
    text = "莎伦的指示:嗨,团队,请您通过以下RSA租车列表工作。请提供反馈,说明维修何时完成,是否需要延期。"
    assert explain_positive_capture_block(text) == "eligible:relay_benign"
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(text, "服务类", "", l2_map)
    assert l1 == "非问题" and flags.get("pos_captured")


def test_mild_part_note():
    _cap("用户反馈后备箱打开后掉出来两个螺母,螺母上面自带塑料的垫片", "产品质量类")


def test_accessory_missing():
    _cap("用户反馈买了一件雨衣快递包裹里没有背带的绳子", "服务类")


def test_tire_advice_ask():
    _cap("我想知道路特斯对我2020路特斯EvoraGT更换轮胎的建议。OEM轮胎是米其林", "服务类")


def test_minor_howto():
    _cap("用户反馈车内的水杯垫没有完全升起来如何解决,提车的时候就是这样子", "服务类")


def test_frustrated_recall_not_captured():
    text = "我写信表达我对我的新Emira持续存在的问题感到沮丧,我只拥有它七个月,但它已经在店里召回了一个多月。"
    assert explain_positive_capture_block(text) != "eligible:v98_no_capture"
    l2_map = load_l2_whitelist()
    l1, _, _ = apply_classification_post_rules(text, "服务类", "", l2_map)
    assert l1 == "服务类"


def test_return_request_not_captured():
    text = "无锡-翟子伟先生-Emeya反馈:遮阳棚质感太一般,想要退货"
    assert explain_positive_capture_block(text) != "eligible:v98_no_capture"
