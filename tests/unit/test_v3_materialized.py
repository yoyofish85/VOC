# -*- coding: utf-8 -*-
"""v3_materialized 单元测试。"""
from __future__ import annotations

import json
import sqlite3

import pytest

from v3_materialized import (
    backfill_v3_materialized,
    ensure_v3_materialized_schema,
    materialized_from_meta,
    materialized_update_params,
)


def test_materialized_from_meta_dict() -> None:
    meta = {
        "l1": "服务类",
        "l2": "充电服务",
        "l3": "",
        "confidence": 0.88,
        "match_type": "rule_l2",
    }
    out = materialized_from_meta(meta)
    assert out["v3_l1"] in ("服务类", "销售服务类")
    assert out["v3_l2"] == "充电服务"
    assert out["v3_confidence"] == pytest.approx(0.88)
    assert out["v3_match_type"] == "rule_l2"


def test_materialized_from_meta_json_string() -> None:
    raw = json.dumps({"l1": "非问题", "l2": "咨询", "confidence": 0.5, "match_type": "clean_l1"})
    out = materialized_from_meta(raw)
    assert out["v3_l2"] == "咨询"
    assert out["v3_match_type"] == "clean_l1"


def test_materialized_update_params_tuple() -> None:
    t = materialized_update_params({"l1": "产品质量类", "l2": "电池", "confidence": 0.9, "match_type": "rule"})
    assert len(t) == 5
    assert t[1] == "电池"


def test_ensure_schema_and_backfill(tmp_path) -> None:
    db_path = str(tmp_path / "mat.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE opinion (
            id INTEGER PRIMARY KEY,
            opinion_id TEXT,
            v3_label_meta TEXT,
            v3_l1 TEXT, v3_l2 TEXT, v3_l3 TEXT,
            v3_confidence REAL, v3_match_type TEXT
        )"""
    )
    meta = json.dumps(
        {"l1": "产品质量类", "l2": "电池", "l3": "", "confidence": 0.75, "match_type": "rule_l2"},
        ensure_ascii=False,
    )
    conn.execute(
        "INSERT INTO opinion (opinion_id, v3_label_meta) VALUES (?, ?)",
        ("OID001", meta),
    )
    conn.commit()
    ensure_v3_materialized_schema(conn)
    conn.close()

    n = backfill_v3_materialized(db_path)
    assert n == 1

    conn2 = sqlite3.connect(db_path)
    row = conn2.execute(
        "SELECT v3_l2, v3_confidence, v3_match_type FROM opinion WHERE opinion_id=?",
        ("OID001",),
    ).fetchone()
    conn2.close()
    assert row[0] == "电池"
    assert row[1] == pytest.approx(0.75)
    assert row[2] == "rule_l2"
