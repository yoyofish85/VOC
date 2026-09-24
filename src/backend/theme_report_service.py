# -*- coding: utf-8 -*-
"""S9：汇报主题证据链服务（主题 → L3 → 明细，脱敏）。"""
from __future__ import annotations

import csv
import io
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent.parent
LABEL = ROOT / "label_project"
for p in (str(ROOT), str(LABEL)):
    if p not in sys.path:
        sys.path.insert(0, p)

from label_project.cross_record_insights import (  # noqa: E402
    _enrich_row,
    _parse_day,
    build_evidence_chain,
    reconcile_evidence,
)
from label_project.theme_lookup import load_theme_mapping, mapping_version  # noqa: E402

_SQL_COLS = """
  opinion_id, original_text, v3_l1, v3_l2, v3_l3, v3_label_meta,
  review_status, review_l1, review_l2, review_l3, reviewed_at, create_time,
  vin, phone, car_model, country, reflow_synced, upload_batch
"""


def mask_vin(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    if len(s) <= 6:
        return "***"
    return s[:6] + "***"


def mask_phone(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    if len(s) < 7:
        return "****"
    return s[:3] + "****" + s[-2:]


def _fetch_rows(db_path: str, limit: int = 3000) -> List[Dict[str, Any]]:
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT {_SQL_COLS}
            FROM opinion
            WHERE TRIM(IFNULL(original_text,'')) != ''
            ORDER BY rowid DESC
            LIMIT ?
            """,
            [int(limit)],
        ).fetchall()
    ]
    conn.close()
    return rows


def _empty_hint(
    *,
    current_n: int,
    skipped_no_l3: int,
    theme_n: int,
) -> str:
    if theme_n > 0:
        return ""
    if current_n <= 0:
        return "当前日期范围内没有客诉，请放宽上方「开始/结束」日期后再点查询"
    if skipped_no_l3 >= current_n:
        return f"范围内有 {current_n} 条客诉，但都没有三级标签，无法归入主题（需先完成分类/复核）"
    return f"范围内有 {current_n} 条客诉，但未能归入主题（缺标签或未命中主题映射）"


def _in_range(day: Optional[datetime], date_from: str, date_to: str) -> bool:
    if day is None:
        # 无日期：保留在当前窗，避免证据链为空
        return True
    df = _parse_day(date_from + " 00:00:00") or _parse_day(date_from)
    dt = _parse_day(date_to + " 23:59:59") or _parse_day(date_to)
    if df and day < df:
        return False
    if dt and day > dt:
        return False
    return True


def _filter_by_dates(
    rows: List[Dict[str, Any]], date_from: str, date_to: str
) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        day = _parse_day(r.get("reviewed_at")) or _parse_day(r.get("create_time"))
        if _in_range(day, date_from, date_to):
            out.append(r)
    return out


def _prev_window(date_from: str, date_to: str) -> Tuple[str, str]:
    df = _parse_day(date_from) or datetime.now()
    dt = _parse_day(date_to) or df
    span = max((dt - df).days, 0) + 1
    prev_to = df - timedelta(days=1)
    prev_from = prev_to - timedelta(days=span - 1)
    return prev_from.strftime("%Y-%m-%d"), prev_to.strftime("%Y-%m-%d")


def _trend_label(cur: int, prev: int) -> str:
    if prev <= 0:
        return "新发" if cur >= 3 else "—"
    ratio = cur / prev
    if ratio >= 1.2:
        return "上升"
    if ratio <= 0.8:
        return "下降"
    return "持平"


def build_theme_report(
    db_path: str,
    *,
    date_from: str,
    date_to: str,
    limit: int = 3000,
) -> Dict[str, Any]:
    mapping = load_theme_mapping()
    # 先取最新 limit 条再按日期筛；若窗内为空，放大取样避免「最新 N 条全在窗外」导致空白
    fetch_n = max(100, min(int(limit or 3000), 8000))
    raw = _fetch_rows(db_path, limit=fetch_n)
    current_raw = _filter_by_dates(raw, date_from, date_to)
    if not current_raw and fetch_n < 8000:
        raw = _fetch_rows(db_path, limit=8000)
        current_raw = _filter_by_dates(raw, date_from, date_to)
        fetch_n = 8000
    pdf, pdt = _prev_window(date_from, date_to)
    prev_raw = _filter_by_dates(raw, pdf, pdt)

    cur_enr = [_enrich_row(dict(r), mapping) for r in current_raw]
    prev_enr = [_enrich_row(dict(r), mapping) for r in prev_raw]
    skipped_no_l3 = sum(1 for r in cur_enr if not r.get("l1") or not r.get("l3"))
    chain = build_evidence_chain(cur_enr, quotes_per_theme=5)
    prev_chain = build_evidence_chain(prev_enr, quotes_per_theme=1)
    prev_counts = {t["theme_id"]: t["count"] for t in prev_chain}

    themes = []
    for t in chain:
        prev_n = int(prev_counts.get(t["theme_id"]) or 0)
        themes.append(
            {
                "theme_id": t["theme_id"],
                "theme_name": t["theme_name"],
                "count": t["count"],
                "reviewed_count": t["reviewed_count"],
                "model_only_count": t["model_only_count"],
                "prev_count": prev_n,
                "wow_delta": t["count"] - prev_n,
                "trend_state": _trend_label(t["count"], prev_n),
                "l3_n": len(t.get("l3_breakdown") or []),
                "quotes": t.get("quotes") or [],
            }
        )

    recon = reconcile_evidence(chain)
    empty_hint = _empty_hint(
        current_n=len(current_raw),
        skipped_no_l3=skipped_no_l3,
        theme_n=len(themes),
    )
    return {
        "meta": {
            "theme_mapping_version": mapping_version(mapping),
            "date_from": date_from,
            "date_to": date_to,
            "prev_from": pdf,
            "prev_to": pdt,
            "fetched_n": len(raw),
            "current_n": len(current_raw),
            "previous_n": len(prev_raw),
            "skipped_no_l3": skipped_no_l3,
            "theme_n": len(themes),
            "empty_hint": empty_hint,
            "query": {
                "db": str(db_path),
                "limit": fetch_n,
                "read_only": True,
            },
        },
        "themes": themes,
        "evidence_chain": chain,
        "reconcile": recon,
        "gate_ok": bool(recon.get("gate_ok")),
        "empty_hint": empty_hint,
        "_enriched": cur_enr,  # 内部用，API 层剥离
    }


def theme_detail(report: Dict[str, Any], theme_id: str) -> Optional[Dict[str, Any]]:
    tid = (theme_id or "").strip()
    for t in report.get("evidence_chain") or []:
        if t.get("theme_id") == tid:
            summary = next(
                (x for x in report.get("themes") or [] if x["theme_id"] == tid),
                {},
            )
            return {
                "theme_id": tid,
                "theme_name": t.get("theme_name"),
                "count": t.get("count"),
                "reviewed_count": t.get("reviewed_count"),
                "model_only_count": t.get("model_only_count"),
                "prev_count": summary.get("prev_count"),
                "wow_delta": summary.get("wow_delta"),
                "trend_state": summary.get("trend_state"),
                "l3_breakdown": t.get("l3_breakdown") or [],
                "quotes": t.get("quotes") or [],
                "opinion_ids": t.get("opinion_ids") or [],
                "meta": report.get("meta"),
            }
    return None


def theme_opinions(
    report: Dict[str, Any],
    theme_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    reviewed_only: Optional[bool] = None,
) -> Dict[str, Any]:
    detail = theme_detail(report, theme_id)
    if not detail:
        return {"total": 0, "page": page, "page_size": page_size, "items": []}
    oid_set = set(detail.get("opinion_ids") or [])
    items = []
    for er in report.get("_enriched") or []:
        oid = str(er.get("opinion_id") or "")
        if oid not in oid_set:
            continue
        if reviewed_only is True and not er.get("reviewed"):
            continue
        if reviewed_only is False and er.get("reviewed"):
            continue
        items.append(
            {
                "opinion_id": oid,
                "l1": er.get("l1"),
                "l2": er.get("l2"),
                "l3": er.get("l3"),
                "path": er.get("path"),
                "reviewed": bool(er.get("reviewed")),
                "vin": mask_vin(er.get("vin")),
                "phone": mask_phone(er.get("phone")),
                "car_model": er.get("car_model_norm") or er.get("car_model") or "",
                "country": er.get("country") or "",
                "intent": er.get("intent"),
                "severity": er.get("severity"),
                "text": (er.get("original_text") or "")[:300],
            }
        )
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 200))
    start = (page - 1) * page_size
    slice_items = items[start : start + page_size]
    return {
        "theme_id": theme_id,
        "theme_name": detail.get("theme_name"),
        "total": len(items),
        "page": page,
        "page_size": page_size,
        "items": slice_items,
    }


def theme_export_csv(report: Dict[str, Any], theme_id: str) -> Tuple[str, str]:
    """返回 (filename, csv_text)。"""
    opinions = theme_opinions(report, theme_id, page=1, page_size=100000)
    detail = theme_detail(report, theme_id) or {}
    buf = io.StringIO()
    w = csv.DictWriter(
        buf,
        fieldnames=[
            "theme_id",
            "theme_name",
            "opinion_id",
            "reviewed",
            "l1",
            "l2",
            "l3",
            "path",
            "vin",
            "phone",
            "car_model",
            "country",
            "intent",
            "severity",
            "text",
        ],
    )
    w.writeheader()
    for it in opinions.get("items") or []:
        w.writerow(
            {
                "theme_id": theme_id,
                "theme_name": detail.get("theme_name") or "",
                **it,
                "reviewed": int(bool(it.get("reviewed"))),
            }
        )
    name = f"theme_{theme_id}_evidence.csv"
    return name, buf.getvalue()


def public_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """去掉内部 _enriched。"""
    return {k: v for k, v in report.items() if not str(k).startswith("_")}
