# -*- coding: utf-8 -*-
"""S6：洞察字段纯规则单测。"""
from __future__ import annotations

from label_project.insight_fields import (
    INTENT_VALUES,
    attach_insight_fields,
    classification_core_unchanged,
    extract_insight_fields,
    feature_enabled,
)

# 手标小金标：用于开发机门禁代理（全量 200 条人工一致率另见 dry-run CSV）
_GOLD = [
    ("车辆无法启动趴窝了", {}, {"intent": "报障", "severity": "高", "expected_action": "维修"}),
    ("请问续航是否正常", {"l1": "非问题"}, {"intent": "咨询", "expected_action": "解释说明"}),
    ("销售态度很好非常感谢", {"l1": "非问题"}, {"intent": "表扬", "expected_action": "无需动作"}),
    ("建议增加苹果音乐功能", {"l1": "体验需求类"}, {"intent": "建议", "expected_action": "功能实现"}),
    ("第3次反馈仍未解决，我要投诉曝光", {}, {"intent": "投诉升级", "repeat_signal": True, "severity": "高"}),
    ("请安排上门道路救援", {}, {"expected_action": "上门"}),
    ("要求退款退定赔偿", {}, {"expected_action": "退款赔偿"}),
    ("车机黑屏死机", {"l1": "产品质量类", "l2": "座舱问题", "l3": "车机异常"}, {"intent": "报障", "root_cause_hint": "车机异常"}),
    ("告知一下到店时间", {}, {"intent": "告知"}),
    ("希望帮忙处理一下充电问题", {}, {"intent": "求助"}),
]


def test_feature_flag_default_off(monkeypatch):
    monkeypatch.delenv("VOC_INSIGHT_FIELDS_V1", raising=False)
    assert feature_enabled() is False
    monkeypatch.setenv("VOC_INSIGHT_FIELDS_V1", "1")
    assert feature_enabled() is True


def test_intent_fault_and_escalate():
    f = extract_insight_fields("车辆无法启动，动力中断趴窝了", l1="产品质量类")
    assert f["intent"] == "报障"
    assert f["severity"] == "高"
    assert f["urgency"] == "紧急"
    assert f["expected_action"] == "维修"

    e = extract_insight_fields("多次催促仍未解决，我要投诉曝光", l1="服务类")
    assert e["intent"] == "投诉升级"
    assert e["repeat_signal"] is True
    assert e["severity"] == "高"


def test_intent_consult_praise_suggest():
    assert extract_insight_fields("请问续航是否正常", l1="非问题")["intent"] == "咨询"
    assert extract_insight_fields("销售顾问态度很好，非常感谢", l1="非问题")["intent"] == "表扬"
    s = extract_insight_fields("建议增加苹果音乐功能", l1="体验需求类")
    assert s["intent"] == "建议"
    assert s["expected_action"] == "功能实现"


def test_refund_and_visit_actions():
    assert extract_insight_fields("要求退款退定")["expected_action"] == "退款赔偿"
    assert extract_insight_fields("请安排上门道路救援")["expected_action"] == "上门"


def test_attach_does_not_overwrite_classification():
    meta = {
        "l1": "产品质量类",
        "l2": "座舱问题",
        "l3": "车机异常",
        "confidence": 0.9,
        "match_type": "qwen14b_structured",
    }
    out = attach_insight_fields(meta, "车机黑屏死机第3次反馈了", enabled=True)
    assert classification_core_unchanged(meta, out)
    assert out["intent"] in INTENT_VALUES
    assert out["repeat_signal"] is True
    assert out["root_cause_hint"] == "车机异常"
    assert out["insight_version"] == "v1"
    # 原 dict 不被就地修改
    assert "intent" not in meta


def test_attach_disabled_is_noop():
    meta = {"l1": "服务类", "l2": "售后服务问题", "l3": "维修/备件时间长"}
    assert attach_insight_fields(meta, "维修太慢", enabled=False) is meta


def test_gold_agreement_ge_85():
    ok = 0
    for text, kwargs, expect in _GOLD:
        got = extract_insight_fields(text, **kwargs)
        if all(got.get(k) == v for k, v in expect.items()):
            ok += 1
    rate = ok / len(_GOLD)
    assert rate >= 0.85, f"gold agreement {rate:.0%} ({ok}/{len(_GOLD)})"
