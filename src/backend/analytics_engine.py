#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VOC 舆情分析引擎：KPI、异常检测、趋势、问题状态与周报存档。"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("voc.analytics")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEEKLY_REPORTS_DIR = Path(
    os.environ.get(
        "VOC_WEEKLY_REPORTS_DIR",
        str(PROJECT_ROOT / "performance_evaluation" / "state" / "weekly_reports"),
    )
)
WEEKLY_REPORTS_MAX = 12


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _day(value: date) -> str:
    return value.isoformat()


def _event_day_sql(column: str = "reviewed_at") -> str:
    return f"DATE(COALESCE(NULLIF({column}, ''), create_time))"


def _parse_l3_phrases(meta_raw: Any, fallback_keywords: str = "") -> List[str]:
    phrases: List[str] = []
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) and meta_raw.strip() else {}
        raw = meta.get("l3_phrases") or meta.get("l3") or ""
        if isinstance(raw, list):
            phrases.extend(str(x).strip() for x in raw)
        else:
            phrases.extend(str(raw).replace("，", ",").split(","))
    except Exception:
        pass
    if not phrases and fallback_keywords:
        phrases.extend(str(fallback_keywords).replace("，", ",").split(","))
    return [p.strip() for p in phrases if 2 <= len(p.strip()) <= 18][:8]


def compute_kpi_snapshot(db_path: str) -> Dict[str, Any]:
    """计算实时看板 KPI 快照。"""
    conn = _connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM opinion").fetchone()[0]
        reviewed = conn.execute("SELECT COUNT(*) FROM opinion WHERE review_status = 1").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM opinion WHERE IFNULL(review_status, 0) != 1").fetchone()[0]
        new_7d = conn.execute(
            "SELECT COUNT(*) FROM opinion WHERE DATE(create_time) >= ?",
            [_day(date.today() - timedelta(days=7))],
        ).fetchone()[0]
        rows = conn.execute(
            """SELECT review_l1, v3_l1 FROM opinion
            WHERE review_status = 1
            AND TRIM(IFNULL(review_l1, '')) != ''
            AND TRIM(IFNULL(v3_l1, '')) != ''"""
        ).fetchall()
    finally:
        conn.close()
    correct = sum(1 for r in rows if str(r["review_l1"]).strip() == str(r["v3_l1"]).strip())
    l1_accuracy = round(correct / max(len(rows), 1) * 100, 2)
    return {
        "total": int(total or 0),
        "reviewed": int(reviewed or 0),
        "reviewed_count": int(reviewed or 0),
        "pending": int(pending or 0),
        "pending_review": int(pending or 0),
        "new_7d": int(new_7d or 0),
        "l1_accuracy": l1_accuracy,
        "l1_correct": correct,
        "l1_total": len(rows),
    }


def detect_anomalies(db_path: str, days: int = 7) -> List[Dict[str, Any]]:
    """按 L2 检测近 days 天相对过去 4 个同周期的异常增长。"""
    days = max(1, min(int(days or 7), 30))
    today = date.today()
    current_start = today - timedelta(days=days - 1)
    conn = _connect(db_path)
    try:
        current_rows = conn.execute(
            f"""SELECT review_l2, COUNT(*) AS cnt FROM opinion
            WHERE review_status = 1
            AND TRIM(IFNULL(review_l2, '')) != ''
            AND {_event_day_sql()} BETWEEN ? AND ?
            GROUP BY review_l2""",
            [_day(current_start), _day(today)],
        ).fetchall()
        anomalies: List[Dict[str, Any]] = []
        for row in current_rows:
            l2 = str(row["review_l2"] or "").strip()
            current = int(row["cnt"] or 0)
            if not l2 or current < 5:
                continue
            past_counts: List[int] = []
            for offset in range(1, 5):
                period_end = current_start - timedelta(days=1 + (offset - 1) * days)
                period_start = period_end - timedelta(days=days - 1)
                past = conn.execute(
                    f"""SELECT COUNT(*) FROM opinion
                    WHERE review_status = 1
                    AND review_l2 = ?
                    AND {_event_day_sql()} BETWEEN ? AND ?""",
                    [l2, _day(period_start), _day(period_end)],
                ).fetchone()[0]
                past_counts.append(int(past or 0))
            avg = sum(past_counts) / max(len(past_counts), 1)
            if avg <= 0 or current < avg * 1.5:
                continue
            l3_rows = conn.execute(
                f"""SELECT v3_label_meta, extracted_keywords FROM opinion
                WHERE review_status = 1
                AND review_l2 = ?
                AND {_event_day_sql()} BETWEEN ? AND ?""",
                [l2, _day(current_start), _day(today)],
            ).fetchall()
            l3_counter: Counter[str] = Counter()
            for l3_row in l3_rows:
                l3_counter.update(_parse_l3_phrases(l3_row["v3_label_meta"], l3_row["extracted_keywords"] or ""))
            anomalies.append(
                {
                    "l2": l2,
                    "current": current,
                    "avg_4w": round(avg, 1),
                    "pct_change": round((current - avg) / max(avg, 1) * 100, 1),
                    "l3_anomalies": [
                        {"phrase": phrase, "count": count}
                        for phrase, count in l3_counter.most_common(3)
                    ],
                }
            )
    finally:
        conn.close()
    anomalies.sort(key=lambda x: (-float(x.get("pct_change") or 0), -int(x.get("current") or 0)))
    return anomalies[:5]


def compute_weekly_trend(db_path: str, days: int = 7) -> List[Dict[str, Any]]:
    """返回近 days 天每日 L1 准确率与复核量。"""
    days = max(1, min(int(days or 7), 30))
    today = date.today()
    conn = _connect(db_path)
    try:
        trend: List[Dict[str, Any]] = []
        for offset in range(days - 1, -1, -1):
            d = today - timedelta(days=offset)
            rows = conn.execute(
                f"""SELECT review_l1, v3_l1 FROM opinion
                WHERE review_status = 1
                AND {_event_day_sql()} = ?
                AND TRIM(IFNULL(review_l1, '')) != ''
                AND TRIM(IFNULL(v3_l1, '')) != ''""",
                [_day(d)],
            ).fetchall()
            correct = sum(1 for r in rows if str(r["review_l1"]).strip() == str(r["v3_l1"]).strip())
            trend.append(
                {
                    "date": d.strftime("%m-%d"),
                    "full_date": _day(d),
                    "accuracy": round(correct / max(len(rows), 1) * 100, 2),
                    "reviewed_count": len(rows),
                }
            )
    finally:
        conn.close()
    return trend


def get_issue_status_by_l2(db_path: str, upload_batch: Optional[str] = None) -> List[Dict[str, Any]]:
    """按 L2 聚合问题处理状态。

    当前 schema 未提供独立客服处理状态列，先以 reflow_synced>=1 视为已处理、
    review_status=1 且未回流视为处理中，未复核视为未处理。
    """
    conn = _connect(db_path)
    try:
        where = "WHERE TRIM(IFNULL(review_l2, '')) != ''"
        params: List[Any] = []
        if upload_batch:
            where += " AND upload_batch = ?"
            params.append(upload_batch)
        rows = conn.execute(
            f"""SELECT review_l2 AS l2,
                COUNT(*) AS total,
                SUM(CASE WHEN IFNULL(reflow_synced, 0) >= 1 THEN 1 ELSE 0 END) AS resolved,
                SUM(CASE WHEN review_status = 1 AND IFNULL(reflow_synced, 0) < 1 THEN 1 ELSE 0 END) AS in_progress,
                SUM(CASE WHEN IFNULL(review_status, 0) != 1 THEN 1 ELSE 0 END) AS pending
            FROM opinion {where}
            GROUP BY review_l2
            ORDER BY total DESC, l2 ASC
            LIMIT 20""",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "l2": str(r["l2"] or "").strip(),
            "total": int(r["total"] or 0),
            "resolved": int(r["resolved"] or 0),
            "in_progress": int(r["in_progress"] or 0),
            "pending": int(r["pending"] or 0),
        }
        for r in rows
        if str(r["l2"] or "").strip()
    ]


def save_weekly_report(week_label: str, data: Dict[str, Any]) -> str:
    """保存周报 JSON，并保留最近 WEEKLY_REPORTS_MAX 期。"""
    safe_week = "".join(ch for ch in str(week_label or "") if ch.isalnum() or ch in ("-", "_"))
    if not safe_week:
        raise ValueError("week_label is required")
    WEEKLY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = WEEKLY_REPORTS_DIR / f"{safe_week}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    existing = sorted(WEEKLY_REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in existing[WEEKLY_REPORTS_MAX:]:
        try:
            stale.unlink()
        except OSError:
            logger.warning("failed to remove stale weekly report: %s", stale)
    return str(path)


def load_weekly_report(week_label: str) -> Dict[str, Any]:
    safe_week = "".join(ch for ch in str(week_label or "") if ch.isalnum() or ch in ("-", "_"))
    if not safe_week:
        raise ValueError("week_label is required")
    path = WEEKLY_REPORTS_DIR / f"{safe_week}.json"
    if not path.is_file():
        raise FileNotFoundError(safe_week)
    return json.loads(path.read_text(encoding="utf-8"))


def list_weekly_reports() -> List[Dict[str, Any]]:
    WEEKLY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    reports: List[Dict[str, Any]] = []
    for path in sorted(WEEKLY_REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:WEEKLY_REPORTS_MAX]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("failed to read weekly report: %s", path)
            continue
        reports.append(
            {
                "week": path.stem,
                "path": str(path),
                "total": data.get("total") or data.get("volume", {}).get("total", "?"),
                "summary": str(data.get("summary") or "")[:100],
                "mtime": path.stat().st_mtime,
            }
        )
    return reports
