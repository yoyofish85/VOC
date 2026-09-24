# -*- coding: utf-8 -*-
"""S8：嵌入存储与跨 L2 聚类单测。"""
from __future__ import annotations

from pathlib import Path

from label_project.complaint_clusters import (
    build_i7_insights,
    cluster_rows_with_vectors,
    estimate_purity,
    keyword_cooccur_clusters,
)
from label_project.embedder import HashingEmbedder
from label_project.embedding_store import connect, count_vectors, load_vectors, upsert_vectors


def test_embedding_store_roundtrip(tmp_path: Path):
    db = tmp_path / "emb.db"
    conn = connect(db)
    upsert_vectors(
        conn,
        [
            {"opinion_id": "a", "model": "hash", "vector": [0.1, 0.2], "text_md5": "x"},
            {"opinion_id": "b", "model": "hash", "vector": [0.3, 0.4], "text_md5": "y"},
        ],
    )
    assert count_vectors(conn, "hash") == 2
    rows = load_vectors(conn, model="hash")
    assert {r["opinion_id"] for r in rows} == {"a", "b"}
    conn.close()


def test_hashing_embedder_deterministic():
    e = HashingEmbedder(dim=64)
    a = e.embed(["车辆无法充电，充电桩故障"])[0]
    b = e.embed(["车辆无法充电，充电桩故障"])[0]
    assert a == b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-5


def test_cross_l2_cluster_and_purity():
    """合成向量：同一根因落在 3 个 L2，应聚成跨 L2 簇且纯度 ≥80%。"""
    rows = []
    gold = {}
    vectors = []
    l2s = ["LFC问题", "车端充电问题", "售后服务问题"]
    # 充电根因：近似同向向量
    for i in range(6):
        oid = f"c{i}"
        rows.append(
            {
                "opinion_id": oid,
                "original_text": f"充电相关问题样本{i}",
                "l2": l2s[i % 3],
                "l3": "无法充电",
                "severity": "中",
            }
        )
        gold[oid] = "充电根因"
        v = [1.0, 0.05 * i] + [0.0] * 14
        # L2 normalize lightly
        n = sum(x * x for x in v) ** 0.5
        vectors.append([x / n for x in v])
    # 噪音：正交方向
    for i in range(3):
        oid = f"n{i}"
        rows.append(
            {
                "opinion_id": oid,
                "original_text": f"座椅问题{i}",
                "l2": "座舱问题",
                "l3": "座椅",
                "severity": "低",
            }
        )
        gold[oid] = "噪音"
        v = [0.0, 0.0, 1.0, 0.1 * i] + [0.0] * 12
        n = sum(x * x for x in v) ** 0.5
        vectors.append([x / n for x in v])

    clusters = cluster_rows_with_vectors(
        rows, vectors, model_name="planted", min_sim=0.85, min_cluster_size=3
    )
    assert clusters, "应至少形成一个簇"
    cross = [c for c in clusters if c.get("cross_l2")]
    assert cross, "应至少有一个跨 L2 簇"
    best = max(
        clusters,
        key=lambda c: sum(1 for oid in c["opinion_ids"] if gold.get(oid) == "充电根因"),
    )
    purity = estimate_purity(best, gold_label_by_oid=gold)
    assert purity is not None and purity >= 0.80, purity
    assert any(c.get("cross_l2_ge3") for c in clusters)
    assert build_i7_insights(clusters)


def test_hashing_similar_texts_closer_than_noise():
    from label_project.complaint_clusters import cosine

    e = HashingEmbedder(dim=256)
    a, b, c = e.embed(
        ["车辆无法充电充电桩故障", "无法充电家充桩异常", "座椅加热完全不工作"]
    )
    assert cosine(a, b) > cosine(a, c)


def test_keyword_fallback_cross_l2():
    rows = [
        {"opinion_id": "1", "l2": "A", "l3": "无法充电", "root_cause_hint": "无法充电", "original_text": "x"},
        {"opinion_id": "2", "l2": "B", "l3": "无法充电", "root_cause_hint": "无法充电", "original_text": "y"},
        {"opinion_id": "3", "l2": "C", "l3": "无法充电", "root_cause_hint": "无法充电", "original_text": "z"},
    ]
    clusters = keyword_cooccur_clusters(rows, min_cluster_size=3)
    assert len(clusters) == 1
    assert clusters[0]["cross_l2_ge3"] is True
