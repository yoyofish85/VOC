# -*- coding: utf-8 -*-
"""export_finetune_from_db.py 单元测试。"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PERF_DIR = Path(__file__).resolve().parents[2] / "performance_evaluation"
sys.path.insert(0, str(PERF_DIR))

export_mod = importlib.import_module("export_finetune_from_db")
_collect = export_mod._collect
_split_holdout = export_mod._split_holdout
_is_hard_negative = export_mod._is_hard_negative
_hard_negative_first_sort = export_mod._hard_negative_first_sort


def _create_test_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE opinion (
            opinion_id TEXT PRIMARY KEY,
            original_text TEXT,
            review_status INTEGER,
            review_l1 TEXT,
            review_l2 TEXT,
            v3_label_meta TEXT,
            model_class TEXT,
            model_keyword TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO opinion VALUES
        (?, ?, 1, ?, ?, ?, ?, ?)
        """,
        (
            "FT_T001",
            "车机黑屏卡顿，多次重启无效",
            "产品质量类",
            "车机",
            json.dumps({"l1": "服务类", "l2": "销售服务问题"}),
            "服务类",
            "销售服务问题",
        ),
    )
    conn.execute(
        """
        INSERT INTO opinion VALUES
        (?, ?, 1, ?, ?, ?, ?, ?)
        """,
        (
            "FT_T002",
            "销售顾问讲解很专业，服务很好",
            "服务类",
            "销售",
            json.dumps({"l1": "服务类", "l2": "销售服务问题"}),
            "服务类",
            "销售服务问题",
        ),
    )
    conn.commit()
    conn.close()


def test_collect_returns_list(tmp_path) -> None:
    db_path = tmp_path / "finetune.db"
    _create_test_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = _collect(conn)
    finally:
        conn.close()
    assert len(rows) >= 2
    ids = {r["opinion_id"] for r in rows}
    assert "FT_T001" in ids and "FT_T002" in ids


def test_split_holdout_creates_ids_file(tmp_path) -> None:
    db_path = tmp_path / "finetune.db"
    holdout_path = tmp_path / "holdout_opinion_ids.json"
    _create_test_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        records = _collect(conn)
    finally:
        conn.close()

    train, holdout = _split_holdout(records, holdout_path, seed=42)
    assert holdout_path.is_file()
    stored = json.loads(holdout_path.read_text(encoding="utf-8"))
    assert isinstance(stored, list)
    assert len(stored) >= 1
    assert len(train) + len(holdout) == len(records)


def test_split_holdout_reuses_ids(tmp_path) -> None:
    db_path = tmp_path / "finetune.db"
    holdout_path = tmp_path / "holdout_opinion_ids.json"
    _create_test_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        records = _collect(conn)
    finally:
        conn.close()

    _, holdout1 = _split_holdout(records, holdout_path, seed=42)
    ids1 = {r["opinion_id"] for r in holdout1}
    _, holdout2 = _split_holdout(records, holdout_path, seed=99)
    ids2 = {r["opinion_id"] for r in holdout2}
    assert ids1 == ids2


def test_hard_negative_first_order(tmp_path) -> None:
    db_path = tmp_path / "finetune.db"
    _create_test_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        records = _collect(conn)
    finally:
        conn.close()

    sorted_rows = _hard_negative_first_sort(records)
    assert _is_hard_negative(sorted_rows[0])
    assert not _is_hard_negative(sorted_rows[1])
    assert sorted_rows[0]["opinion_id"] == "FT_T001"
