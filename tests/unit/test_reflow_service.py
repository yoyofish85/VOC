# -*- coding: utf-8 -*-
"""reflow_service.py 核心函数单元测试（隔离 VOC_TEST_ARTIFACTS_DIR）。"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"


@pytest.fixture()
def rs_mod(tmp_path, monkeypatch):
    """重载 reflow_service，使清洗库/审计日志指向 tmp_path。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    monkeypatch.setenv("VOC_TEST_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setenv("VOC_REFLOW_MERGE_GOLD", "0")
    monkeypatch.setenv("VOC_REFLOW_KEYWORD_BOOST", "0")
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    for name in list(sys.modules):
        if name == "reflow_service" or name.startswith("reflow_service."):
            del sys.modules[name]
    mod = importlib.import_module("reflow_service")
    importlib.reload(mod)
    return mod


def test_build_clear_row(rs_mod) -> None:
    row = rs_mod._build_clear_row(
        "OID001",
        "原文内容",
        "2025-10-01",
        "产品质量类",
        "0.85",
        "导入类",
        "VOC复核",
        "服务类",
        "修正说明",
    )
    assert set(row.keys()) == set(rs_mod.FIELDNAMES)
    assert len(row) == 9
    assert row["舆情编号"] == "OID001"
    assert row["原文"] == "原文内容"
    assert row["人工复核结果"] == "服务类"


def test_build_clear_row_default_conf(rs_mod) -> None:
    row = rs_mod._build_clear_row("OID002", "t", "", "L1", "", "", "", "H1", "")
    assert row["置信度"] == "0"


def test_parse_v3_empty(rs_mod) -> None:
    assert rs_mod._parse_v3(None) == {}
    assert rs_mod._parse_v3("") == {}
    assert rs_mod._parse_v3({}) == {}


def test_parse_v3_valid_json(rs_mod) -> None:
    raw = json.dumps({"l1": "产品质量类", "l2": "车机", "confidence": 0.91})
    v3 = rs_mod._parse_v3(raw)
    assert v3["l1"] == "产品质量类"
    assert v3["confidence"] == 0.91


def test_parse_v3_invalid_json(rs_mod) -> None:
    assert rs_mod._parse_v3("{not-json") == {}


def test_DATA_CLEAR_PATH_isolated(rs_mod, tmp_path) -> None:
    assert str(tmp_path) in str(rs_mod.DATA_CLEAR_PATH.resolve())
    assert rs_mod.DATA_CLEAR_PATH.name == "data_clear.csv"


def test_upsert_data_clear_row_create(rs_mod) -> None:
    created = rs_mod.upsert_data_clear_row(
        "OID_NEW",
        "text",
        "2025-01-01",
        "M1",
        "0.5",
        "I1",
        "filter",
        "H1",
        "fix",
    )
    assert created is False
    assert rs_mod.DATA_CLEAR_PATH.is_file()
    by_id, _ = rs_mod._load_data_clear_index()
    assert "OID_NEW" in by_id


def test_upsert_data_clear_row_overwrite(rs_mod) -> None:
    rs_mod.upsert_data_clear_row("OID_OV", "t1", "2025-01-01", "M", "0", "I", "F", "H1", "f1")
    overwritten = rs_mod.upsert_data_clear_row("OID_OV", "t2", "2025-01-02", "M", "0", "I", "F", "H2", "f2")
    assert overwritten is True
    by_id, _ = rs_mod._load_data_clear_index()
    assert by_id["OID_OV"]["人工复核结果"] == "H2"


def test_reflow_batch_rows_basic(rs_mod) -> None:
    kw = MagicMock()
    kw.extract_keywords.return_value = ["充电", "故障"]
    rows = [
        {
            "opinion_id": "RFLOW01",
            "original_text": "充电故障测试",
            "create_time": "2025-10-01",
            "review_l1": "产品质量类",
            "review_l2": "车端充电问题",
            "model_class": "产品质量类",
            "v3_label_meta": json.dumps({"l1": "产品质量类", "l2": "车机", "confidence": 0.8}),
        }
    ]
    out = rs_mod.reflow_batch_rows(rows, kw, reviewer="pytest", trigger="unit")
    assert out["reflowed"] == 1
    assert out["new_in_clear"] == 1
    assert out["overwritten_in_clear"] == 0
    kw.extract_keywords.assert_called_once()
    by_id, _ = rs_mod._load_data_clear_index()
    assert "RFLOW01" in by_id


def test_reflow_batch_rows_empty_l1(rs_mod) -> None:
    kw = MagicMock()
    rows = [{"opinion_id": "RFLOW02", "original_text": "x", "review_l1": ""}]
    with pytest.raises(RuntimeError, match="缺少人工一级"):
        rs_mod.reflow_batch_rows(rows, kw, reviewer="pytest", trigger="unit")


def test_append_audit_and_detail_log(rs_mod) -> None:
    kw = MagicMock()
    kw.extract_keywords.return_value = ["kw1"]
    rows = [
        {
            "opinion_id": "AUDIT01",
            "original_text": "审计测试",
            "create_time": "2025-10-02",
            "review_l1": "服务类",
            "review_l2": "销售服务问题",
        }
    ]
    rs_mod.reflow_batch_rows(rows, kw, reviewer="pytest", trigger="audit_test")
    audit_lines = [
        ln for ln in rs_mod.AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    detail_lines = [
        ln for ln in rs_mod.REFLOW_DETAIL_LOG_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert audit_lines
    assert detail_lines
    audit = json.loads(audit_lines[-1])
    detail = json.loads(detail_lines[-1])
    assert audit.get("opinion_id") == "AUDIT01"
    assert audit.get("trigger") == "audit_test"
    assert detail.get("opinion_id") == "AUDIT01"
    assert detail.get("after_l1") == "服务类"
