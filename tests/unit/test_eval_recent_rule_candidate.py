# -*- coding: utf-8 -*-
"""eval_recent_rule_candidate 单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

PERF = Path(__file__).resolve().parents[2] / "performance_evaluation"
sys.path.insert(0, str(PERF))

from eval_recent_rule_candidate import compare_rows  # noqa: E402


def test_compare_rows_reports_fixed_broken_and_net():
    rows = [
        {
            "human_l1": "非问题",
            "model_l1": "服务类",
            "replay_l1": "非问题",
            "human_l2": "",
            "model_l2": "售后服务问题",
            "replay_l2": "",
        },
        {
            "human_l1": "产品质量类",
            "model_l1": "产品质量类",
            "replay_l1": "非问题",
            "human_l2": "LFC问题",
            "model_l2": "LFC问题",
            "replay_l2": "",
        },
    ]
    result = compare_rows(rows)
    assert result["l1_fixed"] == 1
    assert result["l1_broken"] == 1
    assert result["l1_net"] == 0
    assert result["l1_total"] == 2
    assert result["l1_before_correct"] == 1
    assert result["l1_after_correct"] == 1


def test_compare_rows_l2_fixed_broken():
    rows = [
        {
            "human_l1": "产品质量类",
            "model_l1": "产品质量类",
            "replay_l1": "产品质量类",
            "human_l2": "LFC问题",
            "model_l2": "车端充电问题",
            "replay_l2": "LFC问题",
        },
        {
            "human_l1": "产品质量类",
            "model_l1": "产品质量类",
            "replay_l1": "产品质量类",
            "human_l2": "车端充电问题",
            "model_l2": "车端充电问题",
            "replay_l2": "LFC问题",
        },
    ]
    result = compare_rows(rows)
    assert result["l2_total"] == 2
    assert result["l2_fixed"] == 1
    assert result["l2_broken"] == 1
    assert result["l2_net"] == 0


def test_apply_post_rules_with_context_falls_back_without_source_kw(monkeypatch):
    import post_rules_replay as prr

    prr._compat_warned = False
    calls: list = []

    def legacy_apply(text, l1, l2, l2_map):
        calls.append((text, l1, l2, l2_map))
        return l1, l2, {}

    fake_mod = type(sys)("qwen_ollama")
    fake_mod.apply_classification_post_rules = legacy_apply  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "qwen_ollama", fake_mod)

    l1, l2, flags = prr.apply_post_rules_with_context(
        "test", "服务类", "", {}, source="APP", vin="SCC123"
    )
    assert (l1, l2, flags) == ("服务类", "", {})
    assert len(calls) == 1
