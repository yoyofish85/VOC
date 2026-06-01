# -*- coding: utf-8 -*-
"""classify_metrics_logger 单元测试。"""
from __future__ import annotations

import json

import pytest

from classify_metrics_logger import (
    ClassifyMetricsCollector,
    finalize_classify_metrics,
    read_classify_route_metrics,
)


def test_collector_gold_hit() -> None:
    c = ClassifyMetricsCollector()
    c.record_row({"match_type": "gold_review", "confidence": 0.99}, use_qwen14=False, was_conflict=False, low_confidence_threshold=0.6)
    d = c.to_dict()
    assert d["gold_hit"] == 1
    assert d["rule_ok"] == 0


def test_collector_conflict_and_llm() -> None:
    c = ClassifyMetricsCollector()
    c.record_row(
        {"match_type": "rule_l2", "confidence": 0.4, "conflict_kind": "C1"},
        use_qwen14=False,
        was_conflict=True,
        low_confidence_threshold=0.6,
    )
    c.record_row(
        {"match_type": "qwen_l2", "confidence": 0.8},
        use_qwen14=True,
        was_conflict=False,
        low_confidence_threshold=0.6,
    )
    d = c.to_dict()
    assert d["conflict"] == 1
    assert d["conflict_by_kind"]["C1"] == 1
    assert d["llm_arbitrated"] == 1


def test_finalize_and_read_metrics(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "metrics.jsonl"
    monkeypatch.setenv("VOC_CLASSIFY_METRICS_LOG", str(log_path))

    import classify_metrics_logger as cml

    monkeypatch.setattr(cml, "METRICS_LOG_PATH", log_path)

    collector = ClassifyMetricsCollector()
    collector.record_row({"match_type": "rule_ok", "confidence": 0.85}, use_qwen14=False, was_conflict=False, low_confidence_threshold=0.6)
    entry = finalize_classify_metrics(
        collector,
        upload_batch="BATCH_TEST",
        opinion_ids=None,
        use_llm=False,
        total=1,
        updated=1,
        started_at=__import__("time").time() - 0.5,
    )
    assert entry["upload_batch"] == "BATCH_TEST"
    assert entry["rule_ok"] == 1
    assert log_path.is_file()

    rows = read_classify_route_metrics(limit=5)
    assert len(rows) == 1
    assert rows[0]["upload_batch"] == "BATCH_TEST"
