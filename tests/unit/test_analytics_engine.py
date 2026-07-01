# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _create_test_db() -> str:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    today = date.today()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE opinion (
            opinion_id TEXT,
            original_text TEXT,
            create_time TEXT,
            reviewed_at TEXT,
            review_status INTEGER,
            review_l1 TEXT,
            review_l2 TEXT,
            v3_l1 TEXT,
            v3_l2 TEXT,
            v3_label_meta TEXT,
            upload_batch TEXT,
            country TEXT,
            extracted_keywords TEXT,
            source TEXT,
            reflow_synced INTEGER DEFAULT 0
        )"""
    )
    current_day = today.isoformat()
    last_week = (today - timedelta(days=7)).isoformat()
    old_day = (today - timedelta(days=28)).isoformat()

    for i in range(12):
        c.execute(
            "INSERT INTO opinion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                f"W{i:03d}",
                f"本周充电反馈{i}",
                current_day,
                current_day,
                1,
                "产品质量类",
                "车端充电问题",
                "产品质量类",
                "车端充电问题",
                json.dumps({"l3_phrases": "地锁不识别,充电枪拔不出"}, ensure_ascii=False),
                "BATCH_T",
                "CN",
                "地锁,充电枪",
                "APP",
                1 if i < 8 else 0,
            ],
        )
    for i in range(4):
        c.execute(
            "INSERT INTO opinion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                f"P{i:03d}",
                f"上周充电反馈{i}",
                last_week,
                last_week,
                1,
                "产品质量类",
                "车端充电问题",
                "产品质量类",
                "车端充电问题",
                json.dumps({"l3_phrases": "充电功率低"}, ensure_ascii=False),
                "BATCH_T",
                "CN",
                "充电",
                "APP",
                1,
            ],
        )
    for i in range(8):
        c.execute(
            "INSERT INTO opinion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                f"O{i:03d}",
                f"旧服务反馈{i}",
                old_day,
                old_day,
                1,
                "服务类",
                "交付问题",
                "非问题",
                "",
                json.dumps({"confidence": 0.3}, ensure_ascii=False),
                "BATCH_T",
                "CN",
                "交付,划痕",
                "APP",
                0,
            ],
        )
    for i in range(8):
        c.execute(
            "INSERT INTO opinion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                f"U{i:03d}",
                f"未复核{i}",
                current_day,
                None,
                0,
                "",
                "",
                "",
                "",
                "",
                "BATCH_T",
                "CN",
                "",
                "APP",
                0,
            ],
        )
    conn.commit()
    conn.close()
    return db_path


def test_compute_kpi_snapshot():
    from analytics_engine import compute_kpi_snapshot

    db_path = _create_test_db()
    try:
        result = compute_kpi_snapshot(db_path)
        assert result["total"] == 32
        assert result["reviewed"] == 24
        assert result["pending"] == 8
        assert result["l1_accuracy"] == round(16 / 24 * 100, 2)
    finally:
        os.unlink(db_path)


def test_detect_anomalies():
    from analytics_engine import detect_anomalies

    db_path = _create_test_db()
    try:
        anomalies = detect_anomalies(db_path, days=7)
        charging = [a for a in anomalies if "充电" in str(a.get("l2", ""))]
        assert len(charging) > 0
        assert charging[0]["pct_change"] >= 50
        assert charging[0]["l3_anomalies"]
    finally:
        os.unlink(db_path)


def test_compute_weekly_trend():
    from analytics_engine import compute_weekly_trend

    db_path = _create_test_db()
    try:
        trend = compute_weekly_trend(db_path, days=7)
        assert len(trend) == 7
        assert all("date" in t and "accuracy" in t and "reviewed_count" in t for t in trend)
    finally:
        os.unlink(db_path)


def test_get_issue_status_by_l2():
    from analytics_engine import get_issue_status_by_l2

    db_path = _create_test_db()
    try:
        status = get_issue_status_by_l2(db_path)
        charging = next(s for s in status if s["l2"] == "车端充电问题")
        assert charging["total"] == 16
        assert charging["resolved"] == 12
        assert charging["in_progress"] == 4
    finally:
        os.unlink(db_path)


def test_weekly_report_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("VOC_WEEKLY_REPORTS_DIR", str(tmp_path))
    import importlib
    import analytics_engine

    importlib.reload(analytics_engine)
    analytics_engine.save_weekly_report("2026-W27", {"summary": "本周摘要", "total": 12})
    reports = analytics_engine.list_weekly_reports()
    assert reports[0]["week"] == "2026-W27"
    assert analytics_engine.load_weekly_report("2026-W27")["summary"] == "本周摘要"
