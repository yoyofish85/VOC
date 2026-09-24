# -*- coding: utf-8 -*-
"""S9：主题证据链服务单测。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from theme_report_service import (
    build_theme_report,
    mask_phone,
    mask_vin,
    public_report,
    theme_detail,
    theme_export_csv,
    theme_opinions,
)


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE opinion (
          opinion_id TEXT PRIMARY KEY,
          original_text TEXT,
          v3_l1 TEXT, v3_l2 TEXT, v3_l3 TEXT, v3_label_meta TEXT,
          review_status INTEGER, review_l1 TEXT, review_l2 TEXT, review_l3 TEXT,
          reviewed_at TEXT, create_time TEXT,
          vin TEXT, phone TEXT, car_model TEXT, country TEXT,
          reflow_synced INTEGER, upload_batch TEXT
        )
        """
    )
    rows = [
        (
            "a1",
            "车辆无法充电",
            "产品质量类",
            "车端充电问题",
            "无法充电",
            "",
            1,
            "产品质量类",
            "车端充电问题",
            "无法充电",
            "2026-09-20 10:00:00",
            "2026-09-20 10:00:00",
            "VIN1234567890ABCDE",
            "13800138000",
            "Eletre",
            "中国",
            0,
            "b1",
        ),
        (
            "a2",
            "充电中断又出现了",
            "产品质量类",
            "车端充电问题",
            "充电中断",
            "",
            0,
            "",
            "",
            "",
            "",
            "2026-09-21 11:00:00",
            "VIN999",
            "139",
            "EMEYA",
            "中国",
            0,
            "b1",
        ),
        (
            "a3",
            "闪充站故障",
            "产品质量类",
            "LFC问题",
            "闪充站问题",
            "",
            0,
            "",
            "",
            "",
            "",
            "2026-09-22 12:00:00",
            "",
            "13700001111",
            "Emira",
            "中国",
            0,
            "b1",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO opinion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.commit()
    conn.close()


def test_mask_helpers():
    assert mask_vin("VIN1234567890ABCDE").endswith("***")
    assert "***" in mask_vin("VIN1234567890ABCDE")
    assert "****" in mask_phone("13800138000")
    assert "138" in mask_phone("13800138000")


def test_theme_report_reconcile_and_export(tmp_path: Path):
    db = tmp_path / "t.db"
    _seed_db(db)
    report = build_theme_report(
        str(db), date_from="2026-09-01", date_to="2026-09-30", limit=100
    )
    assert report["gate_ok"] is True
    pub = public_report(report)
    assert "_enriched" not in pub
    assert pub["themes"]
    # 车端充电主题应存在
    detail = theme_detail(report, "T-Q-02")
    assert detail is not None
    assert detail["count"] == len(detail["opinion_ids"])
    ops = theme_opinions(report, "T-Q-02", page=1, page_size=50)
    assert ops["total"] == detail["count"]
    assert ops["total"] == len(ops["items"])
    for it in ops["items"]:
        if it.get("vin"):
            assert it["vin"].endswith("***") or it["vin"] == "***"
        if it.get("phone") and len(str(it.get("phone"))) > 4:
            assert "****" in it["phone"]
    name, csv_body = theme_export_csv(report, "T-Q-02")
    assert name.endswith(".csv")
    # header + rows
    lines = [ln for ln in csv_body.strip().splitlines() if ln.strip()]
    assert len(lines) - 1 == detail["count"]
    assert "empty_hint" in report["meta"]
    assert report["meta"]["theme_n"] == len(report["themes"])
    assert report["meta"]["current_n"] >= 1


def test_theme_report_reads_l3_from_meta_when_columns_empty(tmp_path: Path):
    db = tmp_path / "meta_only.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE opinion (
          opinion_id TEXT PRIMARY KEY,
          original_text TEXT,
          v3_l1 TEXT, v3_l2 TEXT, v3_l3 TEXT, v3_label_meta TEXT,
          review_status INTEGER, review_l1 TEXT, review_l2 TEXT, review_l3 TEXT,
          reviewed_at TEXT, create_time TEXT,
          vin TEXT, phone TEXT, car_model TEXT, country TEXT,
          reflow_synced INTEGER, upload_batch TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO opinion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "m1",
            "车辆无法充电",
            "",
            "",
            "",
            '{"l1":"产品质量类","l2":"车端充电问题","l3":"无法充电","confidence":0.9}',
            0,
            "",
            "",
            "",
            "",
            "2026-09-20 10:00:00",
            "",
            "",
            "Eletre",
            "中国",
            0,
            "b1",
        ),
    )
    conn.commit()
    conn.close()
    report = build_theme_report(
        str(db), date_from="2026-09-01", date_to="2026-09-30", limit=100
    )
    assert report["meta"]["theme_n"] >= 1
    assert report["meta"]["skipped_no_l3"] == 0
    assert report["meta"]["meta_l3_n"] == 1


def test_theme_report_empty_hint_when_no_rows(tmp_path: Path):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE opinion (
          opinion_id TEXT PRIMARY KEY,
          original_text TEXT,
          v3_l1 TEXT, v3_l2 TEXT, v3_l3 TEXT, v3_label_meta TEXT,
          review_status INTEGER, review_l1 TEXT, review_l2 TEXT, review_l3 TEXT,
          reviewed_at TEXT, create_time TEXT,
          vin TEXT, phone TEXT, car_model TEXT, country TEXT,
          reflow_synced INTEGER, upload_batch TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    report = build_theme_report(
        str(db), date_from="2026-09-01", date_to="2026-09-30", limit=100
    )
    assert report["themes"] == []
    assert "日期" in (report.get("empty_hint") or "")
