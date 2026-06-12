# -*- coding: utf-8 -*-
"""check_reflow_health.py 子进程回归。"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "performance_evaluation" / "check_reflow_health.py"


def _init_db(db_path: Path, *, failed: int = 0) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE opinion (
            id INTEGER PRIMARY KEY,
            opinion_id TEXT,
            review_status INTEGER DEFAULT 0,
            review_l1 TEXT,
            reflow_synced INTEGER DEFAULT 0,
            reviewed_at TEXT
        )"""
    )
    conn.execute(
        """INSERT INTO opinion (opinion_id, review_status, review_l1, reflow_synced, reviewed_at)
           VALUES ('OK1', 1, '产品质量类', 1, datetime('now'))"""
    )
    if failed:
        conn.execute(
            """INSERT INTO opinion (opinion_id, review_status, review_l1, reflow_synced, reviewed_at)
               VALUES ('BAD1', 1, '服务类', -1, datetime('now'))"""
        )
    conn.commit()
    conn.close()


def test_check_healthy_db(tmp_path: Path) -> None:
    db = tmp_path / "healthy.db"
    _init_db(db, failed=0)
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db-path",
            str(db),
            "--pending-path",
            str(tmp_path / "pending.jsonl"),
            "--failures-path",
            str(tmp_path / "failures.jsonl"),
            "--data-clear-path",
            str(tmp_path / "data_clear.csv"),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "回流健康状态正常" in proc.stdout


def test_check_failed_db(tmp_path: Path) -> None:
    db = tmp_path / "failed.db"
    _init_db(db, failed=1)
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db-path",
            str(db),
            "--pending-path",
            str(tmp_path / "pending.jsonl"),
            "--failures-path",
            str(tmp_path / "failures.jsonl"),
            "--data-clear-path",
            str(tmp_path / "data_clear.csv"),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "reflow_synced=-1" in proc.stdout
