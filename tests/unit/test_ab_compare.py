# -*- coding: utf-8 -*-
"""ab_compare_models.py 单元测试。"""
from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PERF_DIR = ROOT / "performance_evaluation"
sys.path.insert(0, str(PERF_DIR))

ab_mod = importlib.import_module("ab_compare_models")
compute_baseline = ab_mod.compute_baseline


def _create_holdout_db(db_path: Path, holdout_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE opinion (
            opinion_id TEXT PRIMARY KEY,
            original_text TEXT,
            review_l1 TEXT,
            review_l2 TEXT,
            v3_label_meta TEXT,
            model_class TEXT,
            model_keyword TEXT,
            country TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO opinion VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "AB_T001",
            "车机黑屏",
            "产品质量类",
            "车机",
            json.dumps({"l1": "产品质量类", "l2": "车机"}),
            "产品质量类",
            "车机",
            "",
        ),
    )
    conn.execute(
        """
        INSERT INTO opinion VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "AB_T002",
            "销售讲解少体验差",
            "服务类",
            "销售",
            json.dumps({"l1": "产品质量类", "l2": "车机"}),
            "产品质量类",
            "车机",
            "",
        ),
    )
    conn.commit()
    conn.close()
    holdout_path.write_text(
        json.dumps(["AB_T001", "AB_T002"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_baseline_matches_expected(tmp_path) -> None:
    db_path = tmp_path / "ab.db"
    holdout_path = tmp_path / "holdout_opinion_ids.json"
    _create_holdout_db(db_path, holdout_path)

    metrics = compute_baseline(db_path, holdout_path)
    assert metrics["l1_total"] == 2
    assert metrics["l1_correct"] == 1
    assert metrics["l1_accuracy_pct"] == 50.0


def test_no_holdout_file_exits_nonzero(tmp_path) -> None:
    db_path = tmp_path / "ab.db"
    holdout_path = tmp_path / "holdout_opinion_ids.json"
    missing = tmp_path / "missing_holdout.json"
    _create_holdout_db(db_path, holdout_path)

    with pytest.raises(FileNotFoundError):
        compute_baseline(db_path, missing)

    script = PERF_DIR / "ab_compare_models.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--baseline",
            "--db-path",
            str(db_path),
            "--holdout-ids-path",
            str(missing),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
