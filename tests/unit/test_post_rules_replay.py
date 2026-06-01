# -*- coding: utf-8 -*-
"""apply_classification_post_rules 与子集评估逻辑。"""
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


def test_post_rules_praise_from_service():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "销售马女士热情有耐心,讲解很透彻到位",
        "服务类",
        "销售服务问题",
        l2_map,
    )
    assert l1 == "非问题" and l2 == "" and flags.get("pos_captured")


def test_post_rules_lfc_regression_stays_product():
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(
        "用户咨询家充桩什么时候到货",
        "非问题",
        "",
        l2_map,
    )
    assert l1 == "产品质量类" and flags.get("charging_guard")


def test_explain_block_true_negative():
    assert explain_positive_capture_block("希望官方尽快优化充电功能") == "blocked:hard_negative"


def test_explain_block_lfc_consult():
    diag = explain_positive_capture_block("用户咨询家充桩什么时候到货")
    assert diag in ("eligible:lfc_consult", "eligible:neutral_query")


def test_explain_eligible_praise():
    assert explain_positive_capture_block("销售服务很好") == "eligible:praise"


def test_explain_no_pattern():
    assert explain_positive_capture_block("客户已知晓") == "eligible:weak_benign"


def test_local_diagnose_fallback_matches_explain():
    import performance_evaluation.eval_p1_subset as eps  # noqa: E402

    samples = [
        "希望官方尽快优化充电功能",
        "用户咨询家充桩什么时候到货",
        "销售服务很好",
        "客户已知晓",
    ]
    for text in samples:
        assert eps._local_explain_positive_capture_block(text) == explain_positive_capture_block(text)
