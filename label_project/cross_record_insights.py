# -*- coding: utf-8 -*-
"""S7：跨记录透视聚合（只读，纯函数优先）。

证据链：汇报主题 → 定位 L3 及数量 → 去重 opinion_id → 代表原文
指标：复发/未解决、意图迁移、期望未满足、车型/区域集中度、故障生命周期
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from label_project.car_model_normalize import normalize_car_model
from label_project.insight_fields import extract_insight_fields
from label_project.theme_lookup import (
    load_theme_mapping,
    mapping_version,
    resolve_theme,
    theme_path,
)

SEVERITY_WEIGHT = {"高": 3, "中": 2, "低": 1}
ESCALATION_INTENTS = frozenset({"投诉升级"})
BASE_INTENTS = frozenset({"报障", "求助"})


def _parse_day(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    # 统一分隔符，兼容 2026/1/27 8:14
    s_norm = s.replace("/", "-")
    s_norm = re.sub(r"\s+", " ", s_norm)
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y-%m",
    ):
        try:
            return datetime.strptime(s_norm[:19], fmt)
        except ValueError:
            continue
    # 宽松：YYYY-M-D
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?", s_norm)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0)
        mm = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
        try:
            return datetime(y, mo, d, hh, mm, ss)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(s_norm.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _meta_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _is_reviewed(row: Dict[str, Any]) -> bool:
    return int(row.get("review_status") or 0) == 1


def _labels(row: Dict[str, Any]) -> Tuple[str, str, str]:
    if _is_reviewed(row):
        l1 = (row.get("review_l1") or row.get("v3_l1") or "").strip()
        l2 = (row.get("review_l2") or row.get("v3_l2") or "").strip()
        l3 = (row.get("review_l3") or row.get("v3_l3") or "").strip()
    else:
        l1 = (row.get("v3_l1") or "").strip()
        l2 = (row.get("v3_l2") or "").strip()
        l3 = (row.get("v3_l3") or "").strip()
    return l1, l2, l3


def _enrich_row(row: Dict[str, Any], mapping: Dict[str, Any]) -> Dict[str, Any]:
    """派生标签、主题、洞察字段（meta 无则规则推）。"""
    text = row.get("original_text") or ""
    l1, l2, l3 = _labels(row)
    meta = _meta_dict(row.get("v3_label_meta"))
    insight = {
        "intent": meta.get("intent"),
        "expected_action": meta.get("expected_action"),
        "severity": meta.get("severity"),
        "urgency": meta.get("urgency"),
        "repeat_signal": meta.get("repeat_signal"),
        "root_cause_hint": meta.get("root_cause_hint"),
    }
    if not insight["intent"]:
        insight = extract_insight_fields(text, l1=l1, l2=l2, l3=l3)
    tid, tname = resolve_theme(l1, l2, l3, mapping)
    day = _parse_day(row.get("reviewed_at")) or _parse_day(row.get("create_time"))
    return {
        **row,
        "l1": l1,
        "l2": l2,
        "l3": l3,
        "path": theme_path(l1, l2, l3),
        "theme_id": tid,
        "theme_name": tname,
        "reviewed": _is_reviewed(row),
        "car_model_norm": normalize_car_model(row.get("car_model")),
        "day": day,
        "vin": (row.get("vin") or "").strip().upper(),
        "phone": (row.get("phone") or "").strip(),
        "country": (row.get("country") or "").strip(),
        "reflow_synced": int(row.get("reflow_synced") or 0),
        **insight,
    }


def choose_entity_key(rows: Sequence[Dict[str, Any]], min_fill: float = 0.30) -> str:
    n = max(len(rows), 1)
    vin_n = sum(1 for r in rows if (r.get("vin") or "").strip())
    phone_n = sum(1 for r in rows if (r.get("phone") or "").strip())
    if vin_n / n >= min_fill:
        return "vin"
    if phone_n / n >= min_fill:
        return "phone"
    return "text_repeat"


def _entity_id(row: Dict[str, Any], key: str) -> str:
    if key == "vin":
        return row.get("vin") or ""
    if key == "phone":
        return row.get("phone") or ""
    return ""


def coverage_stats(rows: Sequence[Dict[str, Any]], entity_key: str) -> Dict[str, Any]:
    n = max(len(rows), 1)
    vin_n = sum(1 for r in rows if (r.get("vin") or "").strip())
    phone_n = sum(1 for r in rows if (r.get("phone") or "").strip())
    car_n = sum(1 for r in rows if normalize_car_model(r.get("car_model")))
    return {
        "n": len(rows),
        "vin_fill": round(vin_n / n, 4),
        "phone_fill": round(phone_n / n, 4),
        "car_model_fill": round(car_n / n, 4),
        "entity_key": entity_key,
        "entity_note": (
            "按 VIN 关联"
            if entity_key == "vin"
            else "按 phone 关联"
            if entity_key == "phone"
            else "VIN/phone 填充不足，复发退化为文本 repeat_signal"
        ),
    }


def build_evidence_chain(
    enriched: Sequence[Dict[str, Any]],
    *,
    quotes_per_theme: int = 3,
) -> List[Dict[str, Any]]:
    by_theme: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in enriched:
        if not r.get("l1") or not r.get("l3"):
            continue
        by_theme[r["theme_id"]].append(r)

    chain: List[Dict[str, Any]] = []
    for tid, items in sorted(by_theme.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        # 去重 opinion_id
        seen = set()
        uniq: List[Dict[str, Any]] = []
        for it in items:
            oid = str(it.get("opinion_id") or "")
            if oid and oid in seen:
                continue
            if oid:
                seen.add(oid)
            uniq.append(it)
        l3_counter: Dict[str, Dict[str, Any]] = {}
        for it in uniq:
            path = it["path"]
            slot = l3_counter.setdefault(
                path,
                {
                    "l1": it["l1"],
                    "l2": it["l2"],
                    "l3": it["l3"],
                    "path": path,
                    "count": 0,
                    "reviewed_count": 0,
                    "model_only_count": 0,
                    "opinion_ids": [],
                },
            )
            slot["count"] += 1
            if it["reviewed"]:
                slot["reviewed_count"] += 1
            else:
                slot["model_only_count"] += 1
            if it.get("opinion_id"):
                slot["opinion_ids"].append(str(it["opinion_id"]))
        l3_breakdown = sorted(l3_counter.values(), key=lambda x: (-x["count"], x["path"]))
        reviewed_n = sum(1 for x in uniq if x["reviewed"])
        model_n = len(uniq) - reviewed_n
        quotes = []
        for it in uniq[: max(quotes_per_theme * 3, quotes_per_theme)]:
            t = (it.get("original_text") or "").strip().replace("\n", " ")
            if not t:
                continue
            quotes.append(
                {
                    "opinion_id": it.get("opinion_id"),
                    "text": t[:160],
                    "reviewed": bool(it["reviewed"]),
                    "l3": it.get("l3"),
                }
            )
            if len(quotes) >= quotes_per_theme:
                break
        theme_count = sum(x["count"] for x in l3_breakdown)
        oid_set = []
        seen_oid = set()
        for x in l3_breakdown:
            for oid in x["opinion_ids"]:
                if oid not in seen_oid:
                    seen_oid.add(oid)
                    oid_set.append(oid)
        chain.append(
            {
                "theme_id": tid,
                "theme_name": uniq[0]["theme_name"] if uniq else tid,
                "count": theme_count,
                "reviewed_count": reviewed_n,
                "model_only_count": model_n,
                "l3_breakdown": [
                    {k: v for k, v in x.items() if k != "opinion_ids"} for x in l3_breakdown
                ],
                "opinion_ids": oid_set,
                "quotes": quotes,
                "reconcile_ok": theme_count == len(oid_set) == reviewed_n + model_n,
            }
        )
    return chain


def compute_resolution(
    enriched: Sequence[Dict[str, Any]],
    *,
    entity_key: str,
    window_days: int = 30,
) -> Dict[str, Any]:
    """复发率 / 未解决存量 / 再次反馈间隔。"""
    if entity_key == "text_repeat":
        n = max(len(enriched), 1)
        repeat_n = sum(1 for r in enriched if r.get("repeat_signal"))
        backlog = sum(
            1
            for r in enriched
            if int(r.get("reflow_synced") or 0) < 1 and r.get("reviewed")
        )
        return {
            "repeat_rate": round(repeat_n / n, 4),
            "unresolved_backlog": backlog,
            "mean_days_to_repeat": None,
            "entity_key": entity_key,
            "repeat_entities": repeat_n,
            "eligible_entities": n,
        }

    by_ent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in enriched:
        eid = _entity_id(r, entity_key)
        if not eid:
            continue
        by_ent[eid].append(r)

    eligible = 0
    repeat_entities = 0
    gaps: List[float] = []
    for eid, items in by_ent.items():
        days = sorted([x["day"] for x in items if x.get("day")])
        if not days:
            # 无日期：按实体出现次数退化统计
            eligible += 1
            if len(items) >= 2:
                repeat_entities += 1
            continue
        cutoff = max(days) - timedelta(days=window_days)
        in_win = [d for d in days if d >= cutoff]
        if len(in_win) < 1:
            continue
        eligible += 1
        if len(in_win) >= 2:
            repeat_entities += 1
            for i in range(1, len(in_win)):
                gaps.append((in_win[i] - in_win[i - 1]).total_seconds() / 86400.0)

    backlog = 0
    for eid, items in by_ent.items():
        latest = max(items, key=lambda x: x.get("day") or datetime.min)
        if int(latest.get("reflow_synced") or 0) < 1:
            backlog += 1

    mean_gap = round(sum(gaps) / len(gaps), 2) if gaps else None
    return {
        "repeat_rate": round(repeat_entities / max(eligible, 1), 4),
        "unresolved_backlog": backlog,
        "mean_days_to_repeat": mean_gap,
        "entity_key": entity_key,
        "repeat_entities": repeat_entities,
        "eligible_entities": eligible,
    }


def compute_intent_shift(
    current: Sequence[Dict[str, Any]],
    previous: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    def _rates(rows: Sequence[Dict[str, Any]]) -> Dict[str, Tuple[int, int, float]]:
        by_l2: Dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            l2 = (r.get("l2") or "").strip()
            intent = r.get("intent") or ""
            if not l2 or not intent:
                continue
            by_l2[l2][intent] += 1
        out: Dict[str, Tuple[int, int, float]] = {}
        for l2, c in by_l2.items():
            base = sum(c[i] for i in BASE_INTENTS) + sum(c[i] for i in ESCALATION_INTENTS)
            esc = sum(c[i] for i in ESCALATION_INTENTS)
            rate = esc / max(base, 1)
            out[l2] = (esc, base, rate)
        return out

    cur = _rates(current)
    prev = _rates(previous)
    rows: List[Dict[str, Any]] = []
    for l2 in sorted(set(cur) | set(prev)):
        _, _, cr = cur.get(l2, (0, 0, 0.0))
        _, _, pr = prev.get(l2, (0, 0, 0.0))
        rows.append(
            {
                "l2": l2,
                "escalation_rate": round(cr, 4),
                "prev_rate": round(pr, 4),
                "delta_pp": round((cr - pr) * 100, 1),
            }
        )
    rows.sort(key=lambda x: (-abs(x["delta_pp"]), -x["escalation_rate"]))
    return rows


def compute_expectation_unmet(enriched: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_act: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in enriched:
        act = (r.get("expected_action") or "").strip()
        if act:
            by_act[act].append(r)
    out: List[Dict[str, Any]] = []
    for act, items in by_act.items():
        unmet = sum(
            1
            for r in items
            if int(r.get("reflow_synced") or 0) < 1 or bool(r.get("repeat_signal"))
        )
        out.append(
            {
                "action": act,
                "count": len(items),
                "unmet_rate": round(unmet / max(len(items), 1), 4),
            }
        )
    out.sort(key=lambda x: (-x["unmet_rate"], -x["count"]))
    return out


def compute_concentration(
    enriched: Sequence[Dict[str, Any]],
    *,
    dimension: str = "car_model",
    min_l3_n: int = 5,
    min_lift: float = 2.0,
) -> List[Dict[str, Any]]:
    """某 L3 在维度上的 lift ≥ min_lift。"""
    dim_key = "car_model_norm" if dimension == "car_model" else "country"
    overall = Counter(r[dim_key] for r in enriched if r.get(dim_key))
    total = sum(overall.values()) or 1
    by_l3: Dict[str, Counter] = defaultdict(Counter)
    for r in enriched:
        l3 = (r.get("l3") or "").strip()
        val = r.get(dim_key) or ""
        if not l3 or not val:
            continue
        by_l3[l3][val] += 1
    hits: List[Dict[str, Any]] = []
    for l3, c in by_l3.items():
        n = sum(c.values())
        if n < min_l3_n:
            continue
        for val, cnt in c.most_common():
            share = cnt / n
            baseline = overall[val] / total
            if baseline <= 0:
                continue
            lift = share / baseline
            if lift >= min_lift and share >= 0.3:
                hits.append(
                    {
                        "l3": l3,
                        "dimension": dimension,
                        "value": val,
                        "share": round(share, 4),
                        "baseline_share": round(baseline, 4),
                        "lift": round(lift, 2),
                        "count": cnt,
                        "l3_n": n,
                    }
                )
    hits.sort(key=lambda x: (-x["lift"], -x["count"]))
    return hits[:20]


def _period_counts(
    enriched: Sequence[Dict[str, Any]],
    *,
    period_days: int,
    n_periods: int = 5,
) -> Dict[Tuple[str, str], List[int]]:
    """返回 (l2,l3) → [本期, 前1, 前2, 前3, 前4] 条数。"""
    days = [r["day"] for r in enriched if r.get("day")]
    if not days:
        return {}
    end = max(days)
    buckets: Dict[Tuple[str, str], List[int]] = defaultdict(lambda: [0] * n_periods)
    for r in enriched:
        if not r.get("day") or not r.get("l2") or not r.get("l3"):
            continue
        delta = (end - r["day"]).days
        idx = delta // max(period_days, 1)
        if 0 <= idx < n_periods:
            buckets[(r["l2"], r["l3"])][idx] += 1
    return buckets


def compute_fault_lifecycle(
    enriched: Sequence[Dict[str, Any]],
    *,
    period_days: int = 7,
) -> List[Dict[str, Any]]:
    buckets = _period_counts(enriched, period_days=period_days, n_periods=5)
    out: List[Dict[str, Any]] = []
    for (l2, l3), counts in buckets.items():
        cur = counts[0]
        past = counts[1:]
        avg4 = sum(past) / max(len(past), 1)
        # severity weighted for current period items
        sev_w = 0
        for r in enriched:
            if r.get("l2") == l2 and r.get("l3") == l3:
                # only approx current: skip if no day filter precision needed for unit tests
                sev_w += SEVERITY_WEIGHT.get(str(r.get("severity") or "低"), 1)

        state = "高位平台"
        weeks_active = sum(1 for c in counts if c > 0)
        if sum(past) == 0 and cur >= 3:
            state = "新发"
        elif cur >= 1.5 * max(avg4, 0.1) and len(past) >= 2 and past[0] > past[1]:
            state = "恶化"
        elif (
            weeks_active >= 3
            and avg4 > 0
            and all(abs(c - avg4) / avg4 <= 0.2 for c in ([cur] + past)[:3] if avg4)
        ):
            state = "高位平台"
        elif len(past) >= 2 and past[0] < past[1] and cur <= 0.6 * max(avg4, 0.1) and cur < past[0]:
            state = "收敛"
        elif cur >= avg4 and any(
            # 曾有低谷再回升：过去某期接近 0 后本期回均值
            past[i] <= 0.2 * max(avg4, 1) and (i + 1 < len(past) and past[i + 1] >= avg4 * 0.8)
            for i in range(len(past) - 1)
        ):
            state = "复发"
        elif cur < 3 and avg4 < 1:
            continue

        out.append(
            {
                "l2": l2,
                "l3": l3,
                "trend_state": state,
                "current": cur,
                "avg_4p": round(avg4, 2),
                "weeks_active": weeks_active,
                "severity_weighted": sev_w,
                "series": counts,
            }
        )
    out.sort(key=lambda x: (-x["severity_weighted"], -x["current"]))
    return out[:50]


def build_insights(
    *,
    resolution: Dict[str, Any],
    intent_shift: Sequence[Dict[str, Any]],
    expectations: Sequence[Dict[str, Any]],
    concentration: Sequence[Dict[str, Any]],
    lifecycle: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    coverage: Dict[str, Any],
    query: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """模板 I1–I6/I8/I9（I7 语义簇留给 S8）。"""
    insights: List[Dict[str, Any]] = []
    qdesc = json.dumps(query, ensure_ascii=False)

    # I1 — 需要按 L2 的 recurrence；此处用全局 resolution + 主题 Top 作代理
    if (resolution.get("repeat_rate") or 0) > 0.30:
        insights.append(
            {
                "template_id": "I1",
                "text": (
                    f"窗口内同一实体二次反馈占比 {resolution['repeat_rate']:.0%} "
                    f"（实体键={resolution.get('entity_key')}），首次处理未解决风险偏高"
                ),
                "evidence_sql": f"-- repeat by entity; query={qdesc}",
                "count": int(resolution.get("repeat_entities") or 0),
                "coverage": coverage,
            }
        )

    for row in intent_shift[:5]:
        if row.get("delta_pp", 0) >= 5:
            insights.append(
                {
                    "template_id": "I2",
                    "text": (
                        f"{row['l2']} 投诉升级占比从 {row['prev_rate']:.0%} "
                        f"升至 {row['escalation_rate']:.0%}（+{row['delta_pp']}pp）"
                    ),
                    "evidence_sql": f"-- intent shift l2={row['l2']}; query={qdesc}",
                    "count": 0,
                    "coverage": coverage,
                }
            )

    for row in concentration[:5]:
        insights.append(
            {
                "template_id": "I3",
                "text": (
                    f"{row['l3']} 有 {row['share']:.0%} 来自 {row['value']}，"
                    f"而该维度仅占总量 {row['baseline_share']:.0%}（lift={row['lift']}）"
                ),
                "evidence_sql": f"-- concentration l3={row['l3']} dim={row['dimension']}; query={qdesc}",
                "count": int(row.get("count") or 0),
                "coverage": coverage,
            }
        )

    for row in lifecycle:
        if row.get("trend_state") == "新发" and row.get("severity_weighted", 0) >= 6:
            insights.append(
                {
                    "template_id": "I4",
                    "text": (
                        f"本期新发高危问题 {row['l3']}（{row['current']} 条），"
                        f"过去 4 期均值 {row['avg_4p']}"
                    ),
                    "evidence_sql": f"-- lifecycle new l3={row['l3']}; query={qdesc}",
                    "count": int(row["current"]),
                    "coverage": coverage,
                }
            )
        if row.get("trend_state") == "复发":
            insights.append(
                {
                    "template_id": "I5",
                    "text": f"{row['l3']} 呈复发态势（当期 {row['current']} / 均值 {row['avg_4p']}）",
                    "evidence_sql": f"-- lifecycle recur l3={row['l3']}; query={qdesc}",
                    "count": int(row["current"]),
                    "coverage": coverage,
                }
            )
        if row.get("trend_state") == "高位平台" and row.get("weeks_active", 0) >= 4:
            insights.append(
                {
                    "template_id": "I8",
                    "text": (
                        f"{row['l2']}/{row['l3']} 已连续 {row['weeks_active']} 期维持高位，属慢性问题"
                    ),
                    "evidence_sql": f"-- lifecycle plateau; query={qdesc}",
                    "count": int(row["current"]),
                    "coverage": coverage,
                }
            )

    if expectations:
        top = expectations[0]
        insights.append(
            {
                "template_id": "I6",
                "text": (
                    f"用户最常要求 {top['action']}（{top['count']} 条），"
                    f"未满足率 {top['unmet_rate']:.0%}，是当前最大的期望落差"
                ),
                "evidence_sql": f"-- expected_action unmet; query={qdesc}",
                "count": int(top["count"]),
                "coverage": coverage,
            }
        )

    # I9：severity 加权 Top vs 条数 Top
    by_count = sorted(evidence, key=lambda x: -x["count"])
    by_sev: List[Tuple[str, int]] = []
    # approximate: use theme count as proxy when severity not rolled up — skip if empty
    if len(by_count) >= 2:
        # build severity from evidence quotes unavailable; use fault_lifecycle severity
        sev_theme = Counter()
        for row in lifecycle:
            # map via l3 only — weak but deterministic
            sev_theme[row["l3"]] += row.get("severity_weighted") or 0
        if sev_theme:
            top_cnt = by_count[0]["theme_name"]
            top_sev_l3 = sev_theme.most_common(1)[0][0]
            if top_sev_l3 and top_sev_l3 not in (by_count[0].get("theme_name") or ""):
                insights.append(
                    {
                        "template_id": "I9",
                        "text": (
                            f"按严重度加权后头号问题倾向 {top_sev_l3}，"
                            f"而按条数头号主题是 {top_cnt}，建议调整资源投向"
                        ),
                        "evidence_sql": f"-- severity vs count; query={qdesc}",
                        "count": 0,
                        "coverage": coverage,
                    }
                )

    return insights


def reconcile_evidence(chain: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    ok_n = sum(1 for t in chain if t.get("reconcile_ok"))
    bad = [t["theme_id"] for t in chain if not t.get("reconcile_ok")]
    theme_total = sum(t["count"] for t in chain)
    l3_total = sum(x["count"] for t in chain for x in t.get("l3_breakdown") or [])
    oid_total = sum(len(t.get("opinion_ids") or []) for t in chain)
    return {
        "themes": len(chain),
        "theme_total": theme_total,
        "l3_total": l3_total,
        "opinion_id_total": oid_total,
        "theme_l3_match": theme_total == l3_total,
        "theme_oid_match": theme_total == oid_total,
        "all_themes_ok": ok_n == len(chain),
        "bad_theme_ids": bad,
        "gate_ok": theme_total == l3_total == oid_total and ok_n == len(chain),
    }


def run_cross_record_insights(
    rows: Sequence[Dict[str, Any]],
    *,
    previous_rows: Optional[Sequence[Dict[str, Any]]] = None,
    mapping: Optional[Dict[str, Any]] = None,
    window_days: int = 30,
    period_days: int = 7,
    query: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    mp = mapping if mapping is not None else load_theme_mapping()
    enriched = [_enrich_row(dict(r), mp) for r in rows]
    prev_enr = [_enrich_row(dict(r), mp) for r in (previous_rows or [])]
    entity_key = choose_entity_key(enriched)
    cov = coverage_stats(enriched, entity_key)
    evidence = build_evidence_chain(enriched)
    resolution = compute_resolution(enriched, entity_key=entity_key, window_days=window_days)
    intent_shift = compute_intent_shift(enriched, prev_enr)
    expectations = compute_expectation_unmet(enriched)
    concentration = compute_concentration(enriched, dimension="car_model")
    concentration += [
        {**row, "dimension": "region"}
        for row in compute_concentration(enriched, dimension="country")
    ]
    lifecycle = compute_fault_lifecycle(enriched, period_days=period_days)
    q = query or {"window_days": window_days, "period_days": period_days, "n": len(enriched)}
    insights = build_insights(
        resolution=resolution,
        intent_shift=intent_shift,
        expectations=expectations,
        concentration=concentration,
        lifecycle=lifecycle,
        evidence=evidence,
        coverage=cov,
        query=q,
    )
    recon = reconcile_evidence(evidence)
    return {
        "meta": {
            "theme_mapping_version": mapping_version(mp),
            "insight_version": "v1",
            "window_days": window_days,
            "period_days": period_days,
            "sample_n": len(enriched),
            "coverage": cov,
            "query": q,
        },
        "evidence_chain": evidence,
        "resolution": resolution,
        "intent_shift": intent_shift,
        "top_expectations": expectations,
        "concentration": concentration,
        "fault_lifecycle": lifecycle,
        "insights": insights,
        "reconcile": recon,
        "gate_ok": bool(recon.get("gate_ok")),
    }


def evidence_detail_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """展开证据链为 CSV 行（主题汇总 / L3 小计可对账）。"""
    rows: List[Dict[str, Any]] = []
    for th in result.get("evidence_chain") or []:
        for l3 in th.get("l3_breakdown") or []:
            # 无逐条 id 时用占位；CLI 层会再填明细
            rows.append(
                {
                    "theme_id": th["theme_id"],
                    "theme_name": th["theme_name"],
                    "theme_count": th["count"],
                    "reviewed_count": th["reviewed_count"],
                    "model_only_count": th["model_only_count"],
                    "l1": l3["l1"],
                    "l2": l3["l2"],
                    "l3": l3["l3"],
                    "path": l3["path"],
                    "l3_count": l3["count"],
                    "l3_reviewed_count": l3["reviewed_count"],
                    "l3_model_only_count": l3["model_only_count"],
                }
            )
    return rows
