# -*- coding: utf-8 -*-
"""score_insight_annotation 评分逻辑单测。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "performance_evaluation"))

from score_insight_annotation import score  # noqa: E402


def test_score_ignores_empty_and_gates():
    rows = [
        {"human_ok": "1"},
        {"human_ok": "1"},
        {"human_ok": "0"},
        {"human_ok": ""},
    ]
    r = score(rows)
    assert r["total_rows"] == 4
    assert r["annotated"] == 3
    assert r["agreed"] == 2
    assert r["agreement_rate"] == round(2 / 3, 4)
    assert r["gate_pass_85"] is False


def test_score_empty_is_zero():
    r = score([])
    assert r["agreement_rate"] == 0.0
    assert r["gate_pass_85"] is False
