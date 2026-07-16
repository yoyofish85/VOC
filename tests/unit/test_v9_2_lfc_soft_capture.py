# -*- coding: utf-8 -*-
"""v9.2 LFC 咨询细分、硬负面拆分、弱正向捕获。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend"
LABEL = Path(__file__).resolve().parents[2] / "label_project"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(LABEL))

from qwen_ollama import (  # noqa: E402
    _eligible_for_positive_capture,
    _lfc_blocks_positive_capture,
    apply_classification_post_rules,
    apply_positive_consult_capture,
    explain_positive_capture_block,
    load_l2_whitelist,
)


def _capture(text: str, l1: str = "产品质量类", l2: str = "LFC问题"):
    l2_map = load_l2_whitelist()
    nl1, nl2, ok = apply_positive_consult_capture(text, l1, l2, l2_map)
    return ok, nl1, nl2


def test_soft_suggestion_not_hard_blocked():
    text = "用户建议了解一下超充站分布情况"
    assert not _lfc_blocks_positive_capture(text)
    assert explain_positive_capture_block(text) != "blocked:hard_negative"
    ok, l1, _ = _capture(text, "体验需求类", "充电建议")
    assert ok and l1 == "非问题"


def test_lfc_charging_power_query():
    text = "用户咨询公共充电功率是多少"
    assert not _lfc_blocks_positive_capture(text)
    ok, l1, l2 = _capture(text)
    assert ok and l1 == "非问题" and l2 == ""


def test_lfc_complaint_power_low():
    text = "公共充电功率只有30kw,太慢了"
    assert _lfc_blocks_positive_capture(text)
    assert explain_positive_capture_block(text).startswith("blocked:lfc")


def test_weak_benign_acknowledged():
    text = "客户已知晓,无异议"
    assert _eligible_for_positive_capture(text)
    ok, l1, l2 = _capture(text, "服务类", "销售服务问题")
    assert ok and l1 == "非问题" and l2 == ""


def test_weak_benign_too_long_not_captured():
    text = "客户已知晓本次活动的全部内容并且表示无异议后续会配合相关工作的各项安排与要求详情说明请查阅附件材料及后续流程等内容"
    assert not _eligible_for_positive_capture(text)


def test_post_rules_lfc_consult_end_to_end():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "用户咨询家充桩什么时候到货",
        "产品质量类",
        "车端充电问题",
        l2_map,
    )
    assert l1 == "非问题" and l2 == "" and flags.get("pos_captured")


def test_charging_guard_still_fixes_regression():
    """模型=非问题 + LFC 投诉/故障 → 守卫仍拉回产品质量。"""
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "占位费不认可,充电跳枪无法完成",
        "非问题",
        "",
        l2_map,
    )
    assert l1 == "产品质量类"
    assert flags.get("charging_guard") or flags.get("non_issue_guard")
