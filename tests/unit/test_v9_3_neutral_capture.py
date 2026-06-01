# -*- coding: utf-8 -*-
"""v9.3 中性陈述、意向表达、LFC 咨询捕获 bugfix。"""
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


def test_lfc_consult_eligible_now_captures():
    """v9.3 bugfix：explain 为 lfc_consult 时 _eligible 也应为 True。"""
    text = "家充桩预计什么时候能安装"
    assert explain_positive_capture_block(text) == "eligible:lfc_consult"
    assert _eligible_for_positive_capture(text)
    ok, l1, l2 = _capture(text, "产品质量类", "车端充电问题")
    assert ok and l1 == "非问题" and l2 == ""


def test_neutral_statement_activity():
    text = "门店通知客户参加周末试驾活动"
    assert _eligible_for_positive_capture(text)
    ok, l1, _ = _capture(text)
    assert ok and l1 == "非问题"


def test_interest_intent():
    text = "客户有意向考虑购买,想了解一下金融方案"
    assert explain_positive_capture_block(text) == "eligible:interest_intent"
    ok, l1, _ = _capture(text)
    assert ok and l1 == "非问题"


def test_lfc_question_tone():
    text = "公共充电桩支持即插即充吗"
    assert _eligible_for_positive_capture(text)
    ok, l1, _ = _capture(text, "产品质量类", "LFC问题")
    assert ok and l1 == "非问题"


def test_multiple_communication_not_hard_blocked():
    text = "销售与客户多次沟通,客户表示已知晓"
    assert explain_positive_capture_block(text) != "blocked:hard_negative"
    ok, l1, _ = _capture(text)
    assert ok and l1 == "非问题"


def test_complaint_still_blocked():
    text = "充电故障投诉,要求退款"
    assert not _eligible_for_positive_capture(text)
    ok, l1, _ = _capture(text, "产品质量类", "故障-通用")
    assert not ok and l1 == "产品质量类"


def test_lfc_consult_post_rules_end_to_end():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "家充桩预计什么时候能安装",
        "产品质量类",
        "车端充电问题",
        l2_map,
    )
    assert l1 == "非问题" and l2 == "" and flags.get("pos_captured")
