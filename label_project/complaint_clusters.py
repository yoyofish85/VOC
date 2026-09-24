# -*- coding: utf-8 -*-
"""S8：跨 L2 抱怨语义聚集。

输出簇：涉及 L2 列表、条数、severity 分布、代表原文、可下钻 L3/opinion_id。
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from label_project.insight_fields import extract_insight_fields

SEVERITY_WEIGHT = {"高": 3, "中": 2, "低": 1}


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    return sum(a[i] * b[i] for i in range(n))


def _union_find(n: int):
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    return find, union


def cluster_by_similarity(
    vectors: Sequence[Sequence[float]],
    *,
    min_sim: float = 0.55,
    max_clusters: int = 15,
    min_cluster_size: int = 3,
) -> List[List[int]]:
    """阈值凝聚：相似度 ≥ min_sim 合并；过大则提高阈值直到 ≤ max_clusters。"""
    n = len(vectors)
    if n == 0:
        return []
    sim = min_sim
    for _ in range(12):
        find, union = _union_find(n)
        for i in range(n):
            for j in range(i + 1, n):
                if cosine(vectors[i], vectors[j]) >= sim:
                    union(i, j)
        groups: Dict[int, List[int]] = defaultdict(list)
        for i in range(n):
            groups[find(i)].append(i)
        clusters = [idxs for idxs in groups.values() if len(idxs) >= min_cluster_size]
        clusters.sort(key=len, reverse=True)
        if len(clusters) <= max_clusters:
            return clusters[:max_clusters]
        sim = min(0.95, sim + 0.05)
    return clusters[:max_clusters]


def keyword_cooccur_clusters(
    rows: Sequence[Dict[str, Any]],
    *,
    min_cluster_size: int = 3,
    max_clusters: int = 15,
) -> List[Dict[str, Any]]:
    """降级：root_cause_hint / L3 关键词共现。"""
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        hint = (r.get("root_cause_hint") or r.get("l3") or "").strip()
        if not hint:
            text = (r.get("original_text") or "")[:40]
            hint = text or "未标注"
        # 取前 8 字作粗键
        key = re.sub(r"\s+", "", hint)[:8]
        buckets[key].append(r)
    clusters = []
    for key, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        if len(items) < min_cluster_size:
            continue
        clusters.append(_build_cluster(f"kw:{key}", items, method="keyword_cooccur"))
        if len(clusters) >= max_clusters:
            break
    return clusters


def _build_cluster(cluster_id: str, items: Sequence[Dict[str, Any]], method: str) -> Dict[str, Any]:
    l2s = sorted({(r.get("l2") or "").strip() for r in items if (r.get("l2") or "").strip()})
    l3_counter = Counter((r.get("l3") or "").strip() for r in items if (r.get("l3") or "").strip())
    sev = Counter(str(r.get("severity") or "低") for r in items)
    oids = [str(r.get("opinion_id")) for r in items if r.get("opinion_id")]
    quotes = []
    for r in items:
        t = (r.get("original_text") or "").strip().replace("\n", " ")
        if not t:
            continue
        quotes.append(
            {
                "opinion_id": r.get("opinion_id"),
                "l2": r.get("l2"),
                "l3": r.get("l3"),
                "text": t[:160],
            }
        )
        if len(quotes) >= 3:
            break
    title_l3 = l3_counter.most_common(1)[0][0] if l3_counter else "未命名簇"
    return {
        "cluster_id": cluster_id,
        "theme": title_l3,
        "method": method,
        "l2_list": l2s,
        "cross_l2": len(l2s) >= 2,
        "cross_l2_ge3": len(l2s) >= 3,
        "count": len(items),
        "severity_mix": dict(sev),
        "severity_weighted": sum(SEVERITY_WEIGHT.get(k, 1) * v for k, v in sev.items()),
        "l3_breakdown": [{"l3": k, "count": v} for k, v in l3_counter.most_common()],
        "opinion_ids": oids,
        "quotes": quotes,
        "trend_state": "",
    }


def cluster_rows_with_vectors(
    rows: Sequence[Dict[str, Any]],
    vectors: Sequence[Sequence[float]],
    *,
    model_name: str,
    min_sim: float = 0.55,
    max_clusters: int = 15,
    min_cluster_size: int = 3,
) -> List[Dict[str, Any]]:
    assert len(rows) == len(vectors)
    groups = cluster_by_similarity(
        vectors,
        min_sim=min_sim,
        max_clusters=max_clusters,
        min_cluster_size=min_cluster_size,
    )
    out: List[Dict[str, Any]] = []
    for gi, idxs in enumerate(groups):
        items = [rows[i] for i in idxs]
        cid = f"emb:{model_name}:{gi}"
        out.append(_build_cluster(cid, items, method=f"embedding:{model_name}"))
    out.sort(key=lambda x: (-x["cross_l2_ge3"], -x["count"], -x["severity_weighted"]))
    return out


def estimate_purity(
    cluster: Dict[str, Any],
    *,
    gold_label_by_oid: Dict[str, str],
) -> Optional[float]:
    """簇纯度 = 众数金标占比；无金标返回 None。"""
    labels = [
        gold_label_by_oid[oid]
        for oid in cluster.get("opinion_ids") or []
        if oid in gold_label_by_oid
    ]
    if not labels:
        return None
    top = Counter(labels).most_common(1)[0][1]
    return round(top / len(labels), 4)


def enrich_for_cluster(row: Dict[str, Any]) -> Dict[str, Any]:
    text = row.get("original_text") or ""
    l1 = (row.get("review_l1") or row.get("v3_l1") or "").strip()
    l2 = (row.get("review_l2") or row.get("v3_l2") or "").strip()
    l3 = (row.get("review_l3") or row.get("v3_l3") or "").strip()
    insight = extract_insight_fields(text, l1=l1, l2=l2, l3=l3)
    return {
        **row,
        "l1": l1,
        "l2": l2,
        "l3": l3,
        **insight,
    }


def build_i7_insights(clusters: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    insights = []
    for c in clusters:
        if not c.get("cross_l2_ge3"):
            continue
        l2s = ", ".join(c["l2_list"])
        insights.append(
            {
                "template_id": "I7",
                "text": (
                    f"「{c['theme']}」横跨 {l2s} 共 {c['count']} 条，"
                    f"按现有标签口径被拆散，实际可能是单一根因"
                ),
                "evidence_sql": (
                    f"-- cluster_id={c['cluster_id']} "
                    f"opinion_ids={len(c['opinion_ids'])}"
                ),
                "count": c["count"],
                "cluster_id": c["cluster_id"],
            }
        )
    return insights


def text_md5(text: str) -> str:
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()
