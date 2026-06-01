# -*- coding: utf-8 -*-
"""
多条件舆情汇报：按月聚合、区域筛选、与大模型解读接口扩展点。
统计口径：已复核 → v3_label_meta → model_class / model_keyword（与历史导入一致）。
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

# 与 main 相同：由调用方保证已把 label_project 加入 sys.path
from taxonomy_normalize import canonicalize_l1_label

# ---- 展示用名称（业务一级为「服务类」，报表对标「销售服务类」）----
L1_PRODUCT = "产品质量类"
L1_SALES_DISPLAY = "销售服务类"  # 数据来自 canonical「服务类」
L1_SALES_CANON = "服务类"

_REGION_ALL = "all"
_REGION_CN = "cn"
_REGION_REST = "rest"


def _normalize_ts_prefix(raw: str) -> str:
    """取可比较的日期前缀 YYYY-MM-DD（兼容 / . 分隔、带时间）。"""
    t = (raw or "").strip().replace("/", "-").replace(".", "-")
    if len(t) >= 10:
        return t[:10]
    if len(t) == 7:
        return t + "-01"
    return ""


def _month_key_from_ts(raw: str) -> str:
    p = _normalize_ts_prefix(raw)
    return p[:7] if len(p) >= 7 else ""


def _row_primary_time(row: Dict[str, Any]) -> str:
    """统计用时间：创建时间优先，缺省用复核/入库时间（与业务「处理完成」可对照）。"""
    for k in ("create_time", "reviewed_at", "created_at"):
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _month_key_from_row(row: Dict[str, Any]) -> str:
    return _month_key_from_ts(_row_primary_time(row))


def _region_sql(region: str) -> Tuple[str, List]:
    """region: all | cn | rest。cn 含 country 为空（历史未填默认视作国内）；rest 仅非空且非中国。"""
    r = (region or _REGION_ALL).strip().lower()
    cn_cond = (
        "TRIM(IFNULL(country,'')) = '' OR IFNULL(country,'') LIKE '%中国%' OR "
        "UPPER(TRIM(IFNULL(country,''))) IN ('CN','CHINA')"
    )
    if r in ("cn", "china", "中国"):
        return (f"({cn_cond})", [])
    if r in ("rest", "abroad", "海外", "非中国"):
        return (
            f"(TRIM(IFNULL(country,'')) != '' AND NOT ({cn_cond}))",
            [],
        )
    return ("1=1", [])


def _l2_from_model_keyword(mkw: Any) -> str:
    """与年度导出 SQL 一致：二级为 model_keyword 第一段（至逗号）。"""
    s = str(mkw or "").strip()
    if not s:
        return ""
    if "," in s:
        return s.split(",", 1)[0].strip()
    return s


def _effective_l1_l2(row: Dict[str, Any]) -> Tuple[str, str]:
    """已复核 → v3 模型标签 → CSV/旧库 model_class & model_keyword（全量可统计）。"""
    try:
        st = int(row.get("review_status") or 0)
    except (TypeError, ValueError):
        st = 0
    j_l1 = _sql_cell_str(row.get("v3_l1"))
    j_l2 = _sql_cell_str(row.get("v3_l2"))
    l1_raw = ""
    l2 = ""
    meta = row.get("v3_label_meta") or ""
    j: Dict[str, Any] = {}

    if st == 1 and (row.get("review_l1") or "").strip():
        l1_raw = str(row.get("review_l1") or "").strip()
        l2 = (row.get("review_l2") or "").strip()
    else:
        l1_raw = j_l1
        l2 = j_l2
        if (not l1_raw or not l2) and meta:
            try:
                j = json.loads(meta) if isinstance(meta, str) else meta
                if not isinstance(j, dict):
                    j = {}
            except (TypeError, ValueError, json.JSONDecodeError):
                j = {}
            if not l1_raw:
                l1_raw = str(j.get("l1") or "").strip()
            if not l2:
                l2 = str(j.get("l2") or "").strip()

    if not l1_raw:
        l1_raw = str(row.get("model_class") or "").strip()
    if not l2:
        l2 = _l2_from_model_keyword(row.get("model_keyword"))
    if not l2 and j:
        l2 = str(j.get("l2") or "").strip()

    l1 = canonicalize_l1_label(l1_raw) if l1_raw else ""
    return l1, l2


def _iter_months_simple(d0: str, d1: str) -> List[str]:
    """不依赖 dateutil：按字符串逐月推进。"""
    if len(d0) < 7 or len(d1) < 7:
        return []
    y0, m0 = int(d0[:4]), int(d0[5:7])
    y1, m1 = int(d1[:4]), int(d1[5:7])
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    return out


def _sql_cell_str(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if not s or s.lower() in ("none", "null"):
        return ""
    return s


def fetch_rows_for_report(
    query_db: Callable[..., Any],
    date_from: str,
    date_to: str,
    region: str,
    *,
    include_original_text: bool = False,
) -> List[Dict[str, Any]]:
    """
    按「统计用时间」与区域过滤。
    时间轴：create_time → reviewed_at → created_at，统一归一为 YYYY-MM-DD 再比区间。
    默认不拉取 original_text（大幅减负），仅关键词筛选时需要原文。
    """
    df = (date_from or "")[:10]
    dt = (date_to or "")[:10]
    if not df or not dt:
        return []
    reg_sql, reg_params = _region_sql(region)
    raw = (
        "REPLACE(REPLACE(TRIM(COALESCE("
        "NULLIF(create_time,''), NULLIF(reviewed_at,''), NULLIF(created_at,'')"
        ")),'/','-'),'.','-')"
    )
    evt_day = (
        f"(CASE WHEN length({raw}) >= 10 THEN substr({raw}, 1, 10) "
        f"WHEN length({raw}) >= 7 THEN substr({raw}, 1, 7) || '-01' ELSE '' END)"
    )
    text_col = ", original_text" if include_original_text else ""
    sql = f"""SELECT create_time, reviewed_at, created_at, country, review_status,
        review_l1, review_l2, review_l3, review_note,
        model_class, model_keyword, extracted_keywords,
        v3_l1, v3_l2, v3_l3, v3_confidence, v3_match_type, v3_label_meta{text_col}
        FROM opinion
        WHERE length({raw}) >= 7
        AND {evt_day} != ''
        AND {evt_day} >= ?
        AND {evt_day} <= ?
        AND ({reg_sql})"""
    params: List = [df, dt] + reg_params
    rows = query_db(sql, params)
    out = rows if isinstance(rows, list) else []
    filtered = []
    for r in out:
        ts = _normalize_ts_prefix(_row_primary_time(r))
        if not ts or ts < df or ts > dt:
            continue
        filtered.append(r)
    return filtered


# 简单内存缓存（秒级 TTL），减轻 M3 上重复拖拽筛选压力
_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 45.0


def _cache_get(key: str) -> Optional[Any]:
    v = _CACHE.get(key)
    if not v:
        return None
    ts, data = v
    if time.time() - ts > _CACHE_TTL:
        del _CACHE[key]
        return None
    return data


def _cache_set(key: str, data: Any) -> None:
    if len(_CACHE) > 200:
        _CACHE.clear()
    _CACHE[key] = (time.time(), data)


def _cache_key(prefix: str, payload: Dict) -> str:
    b = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return f"{prefix}:{hashlib.md5(b).hexdigest()}"


def get_monthly_overview_data(
    query_db: Callable[..., Any],
    date_from: str,
    date_to: str,
    region: str,
    skip_cache: bool = False,
) -> Dict[str, Any]:
    payload = {"df": date_from[:10], "dt": date_to[:10], "r": region}
    ck = _cache_key("mo_v2", payload)
    if not skip_cache:
        hit = _cache_get(ck)
        if hit is not None:
            return hit

    rows = fetch_rows_for_report(query_db, date_from, date_to, region)
    months = _iter_months_simple(payload["df"], payload["dt"])
    month_l1, _, _, _, _ = _build_report_index(rows, months)
    prod = {m: int(month_l1[m].get(L1_PRODUCT, 0)) for m in months}
    sales = {m: int(month_l1[m].get(L1_SALES_CANON, 0)) for m in months}

    out = {
        "months": months,
        "series": {
            L1_PRODUCT: [prod[m] for m in months],
            L1_SALES_DISPLAY: [sales[m] for m in months],
        },
        "meta": {"row_count": len(rows), "cached": False},
    }
    if not skip_cache:
        _cache_set(ck, out)
    return out


def _build_report_index(
    rows: List[Dict[str, Any]], months: List[str]
) -> Tuple[
    Dict[str, Counter],
    Dict[str, Dict[str, Counter]],
    Dict[str, Counter],
    Dict[str, Counter],
    Counter,
]:
    """单次扫描：month→l1 计数、month→l1→l2 计数、l1→l2 总量（供多报表复用）。"""
    month_set = set(months)
    month_l1: Dict[str, Counter] = {m: Counter() for m in months}
    month_l1_l2: Dict[str, Dict[str, Counter]] = {m: defaultdict(Counter) for m in months}
    l1_l2_total: Dict[str, Counter] = defaultdict(Counter)
    month_l2: Dict[str, Counter] = {m: Counter() for m in months}
    total_l2: Counter = Counter()

    for r in rows:
        mk = _month_key_from_row(r)
        if mk not in month_set:
            continue
        l1, l2 = _effective_l1_l2(r)
        if l1:
            month_l1[mk][l1] += 1
        if l1 and l2:
            month_l1_l2[mk][l1][l2] += 1
            l1_l2_total[l1][l2] += 1
        if l2:
            month_l2[mk][l2] += 1
            total_l2[l2] += 1

    return month_l1, month_l1_l2, l1_l2_total, month_l2, total_l2


def get_monthly_subtag_trend_data(
    query_db: Callable[..., Any],
    date_from: str,
    date_to: str,
    region: str,
    l1_param: str,
    top_secondary: int = 3,
    skip_cache: bool = False,
) -> Dict[str, Any]:
    """折线：每类取第 N 高频二级（top_secondary=1 为最高频），按月条数。"""
    rank = max(1, min(int(top_secondary or 1), 8))
    payload = {"df": date_from[:10], "dt": date_to[:10], "r": region, "l1": l1_param, "rank": rank}
    ck = _cache_key("mst_v2", payload)
    if not skip_cache:
        hit = _cache_get(ck)
        if hit is not None:
            return hit

    rows = fetch_rows_for_report(query_db, date_from, date_to, region)
    months = _iter_months_simple(date_from[:10], date_to[:10])

    _ = (l1_param or "").strip()

    _, month_l1_l2, l1_l2_total, _, _ = _build_report_index(rows, months)
    lines = []
    color_map = {L1_PRODUCT: "#EECA1F", L1_SALES_DISPLAY: "#3B82F6"}

    for bucket_name, c_l1 in ((L1_PRODUCT, L1_PRODUCT), (L1_SALES_DISPLAY, L1_SALES_CANON)):
        tops = [t for t, _ in l1_l2_total.get(c_l1, Counter()).most_common(rank)]
        tag = tops[rank - 1] if len(tops) >= rank else (tops[0] if tops else "")
        per_m = {
            m: int(month_l1_l2.get(m, {}).get(c_l1, Counter()).get(tag, 0)) if tag else 0
            for m in months
        }
        lines.append(
            {
                "l1_bucket": bucket_name,
                "l2_tag": tag or "(无二级)",
                "color": color_map.get(bucket_name, "#94a3b8"),
                "values": [per_m[m] for m in months],
            }
        )

    out = {"months": months, "lines": lines, "meta": {"row_count": len(rows)}}
    if not skip_cache:
        _cache_set(ck, out)
    return out


def get_top_subtag_monthly_data(
    query_db: Callable[..., Any],
    date_from: str,
    date_to: str,
    region: str,
    top_n: int = 8,
    skip_cache: bool = False,
) -> Dict[str, Any]:
    """全时间段内二级标签总量 TopN，按月堆叠/分组计数。"""
    top_n = max(3, min(int(top_n or 8), 20))
    payload = {"df": date_from[:10], "dt": date_to[:10], "r": region, "n": top_n}
    bk = _cache_key("tsm_v2", payload)
    if not skip_cache:
        hit = _cache_get(bk)
        if hit is not None:
            return hit

    rows = fetch_rows_for_report(query_db, date_from, date_to, region)
    months = _iter_months_simple(date_from[:10], date_to[:10])
    _, _, _, month_l2, total_l2 = _build_report_index(rows, months)

    top_tags = [t for t, _ in total_l2.most_common(top_n)]
    series = []
    for tag in top_tags:
        series.append(
            {
                "name": tag,
                "values": [int(month_l2[m].get(tag, 0)) for m in months],
                "total": int(total_l2[tag]),
            }
        )

    out = {
        "months": months,
        "tags": top_tags,
        "series": series,
        "meta": {"row_count": len(rows)},
    }
    if not skip_cache:
        _cache_set(bk, out)
    return out


def _canon_report_l1(raw: str) -> str:
    t = (raw or "").strip()
    if not t:
        return ""
    if t in (L1_SALES_DISPLAY, "销售服务", "营销服务类"):
        return L1_SALES_CANON
    return canonicalize_l1_label(t)


def get_single_issue_trend_data(
    query_db: Callable[..., Any],
    date_from: str,
    date_to: str,
    region: str,
    l1_param: str = "",
    l2_param: str = "",
    keyword: str = "",
) -> Dict[str, Any]:
    """只读：按月统计单一问题筛选趋势。沿用报表时间/区域/历史数据口径。"""
    target_l1 = _canon_report_l1(l1_param)
    target_l2 = (l2_param or "").strip()
    kw = (keyword or "").strip().lower()
    rows = fetch_rows_for_report(
        query_db,
        date_from,
        date_to,
        region,
        include_original_text=bool(kw),
    )
    months = _iter_months_simple(date_from[:10], date_to[:10])
    per_m = {m: 0 for m in months}

    matched = 0
    for r in rows:
        mk = _month_key_from_row(r)
        if mk not in per_m:
            continue
        l1, l2 = _effective_l1_l2(r)
        if target_l1 and l1 != target_l1:
            continue
        if target_l2 and l2 != target_l2:
            continue
        if kw:
            hay = " ".join(
                str(r.get(k) or "")
                for k in (
                    "original_text",
                    "extracted_keywords",
                    "review_note",
                    "review_l3",
                    "model_keyword",
                )
            ).lower()
            if kw not in hay:
                continue
        per_m[mk] += 1
        matched += 1

    return {
        "months": months,
        "values": [per_m[m] for m in months],
        "filters": {
            "l1": l1_param or "",
            "l2": target_l2,
            "keyword": keyword or "",
            "region": region or _REGION_ALL,
            "date_from": date_from[:10],
            "date_to": date_to[:10],
        },
        "meta": {"row_count": len(rows), "matched": matched},
    }
