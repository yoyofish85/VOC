# -*- coding: utf-8 -*-
"""rule_conflict_detector 单元测试。"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def conflict_json(tmp_path, monkeypatch):
    cfg = {
        "version": 1,
        "conflicts": [
            {"id": "C1", "when_l1": "非问题", "keyword_group": "negative"},
            {"id": "C2", "when_l1": "服务类", "keyword_group": "quality"},
        ],
        "keyword_groups": {
            "negative": ["投诉", "退款"],
            "quality": ["电池", "续航"],
        },
    }
    path = tmp_path / "rule_conflict_keywords_test.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("VOC_RULE_CONFLICT_JSON", str(path))
    import rule_conflict_detector as rcd

    rcd.refresh_rule_conflict_keywords()
    return rcd


def test_detect_c1_negative_on_non_issue(conflict_json) -> None:
    kind = conflict_json.detect_rule_conflict(
        {"l1": "非问题", "l2": "咨询"},
        "我要投诉你们的服务",
    )
    assert kind == "C1"


def test_detect_c2_quality_on_service(conflict_json) -> None:
    kind = conflict_json.detect_rule_conflict(
        {"l1": "服务类", "l2": "充电"},
        "电池续航明显缩水",
    )
    assert kind == "C2"


def test_detect_empty_text_returns_empty(conflict_json) -> None:
    assert conflict_json.detect_rule_conflict({"l1": "非问题"}, "") == ""


def test_detect_unknown_l1_returns_empty(conflict_json) -> None:
    assert conflict_json.detect_rule_conflict({"l1": "产品质量类"}, "投诉") == ""


def test_refresh_returns_metadata(conflict_json) -> None:
    meta = conflict_json.refresh_rule_conflict_keywords()
    assert meta["conflict_rules"] == 2
    assert meta["keyword_total"] >= 4
