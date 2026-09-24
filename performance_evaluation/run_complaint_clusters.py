#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S8：离线嵌入 + 跨 L2 聚类 dry-run。

用法：
  # 开发机（无 bge-m3 时自动 hashing 降级）
  python3 performance_evaluation/run_complaint_clusters.py --limit 400

  # 指定 Ollama 模型（需已 pull）
  VOC_EMBED_MODEL=bge-m3 python3 performance_evaluation/run_complaint_clusters.py --prefer-ollama

向量写入独立库 opinion_embedding.db，不改 opinion 分类字段。
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from label_project.complaint_clusters import (  # noqa: E402
    build_i7_insights,
    cluster_rows_with_vectors,
    enrich_for_cluster,
    estimate_purity,
    keyword_cooccur_clusters,
    text_md5,
)
from label_project.embedder import HashingEmbedder, OllamaEmbedder, get_embedder  # noqa: E402
from label_project.embedding_store import (  # noqa: E402
    connect,
    count_vectors,
    default_embedding_db,
    upsert_vectors,
)

EXPORTS = Path(__file__).resolve().parent / "exports"
DEFAULT_DB = ROOT / "src" / "backend" / "opinion_review.db"


def _fetch(db: Path, limit: int) -> List[Dict[str, Any]]:
    uri = f"file:{db.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT opinion_id, original_text, v3_l1, v3_l2, v3_l3,
                   review_status, review_l1, review_l2, review_l3
            FROM opinion
            WHERE TRIM(IFNULL(original_text,'')) != ''
              AND TRIM(IFNULL(v3_l2,'')) != ''
            ORDER BY rowid DESC
            LIMIT ?
            """,
            [int(limit)],
        ).fetchall()
    ]
    conn.close()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="S8 complaint clusters dry-run")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--embed-db", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--prefer-ollama", action="store_true")
    ap.add_argument("--force-hashing", action="store_true")
    ap.add_argument("--min-sim", type=float, default=0.55)
    ap.add_argument("--max-clusters", type=int, default=15)
    ap.add_argument("--keyword-fallback", action="store_true")
    args = ap.parse_args()
    if not args.db.is_file():
        print(json.dumps({"error": f"db not found: {args.db}"}, ensure_ascii=False))
        return 2

    raw = _fetch(args.db, args.limit)
    rows = [enrich_for_cluster(r) for r in raw]
    texts = [r.get("original_text") or "" for r in rows]

    if args.force_hashing:
        embedder = HashingEmbedder()
    elif args.prefer_ollama:
        oe = OllamaEmbedder()
        embedder = oe if oe.available() else HashingEmbedder()
    else:
        embedder = get_embedder(prefer_ollama=False)

    vectors = embedder.embed(texts)
    embed_db = args.embed_db or default_embedding_db(args.db)
    econn = connect(embed_db)
    upsert_vectors(
        econn,
        [
            {
                "opinion_id": r["opinion_id"],
                "model": embedder.name,
                "vector": vectors[i],
                "text_md5": text_md5(texts[i]),
            }
            for i, r in enumerate(rows)
        ],
    )
    stored = count_vectors(econn, embedder.name)
    econn.close()

    if args.keyword_fallback:
        clusters = keyword_cooccur_clusters(rows)
        method = "keyword_cooccur"
    else:
        clusters = cluster_rows_with_vectors(
            rows,
            vectors,
            model_name=embedder.name,
            min_sim=args.min_sim,
            max_clusters=args.max_clusters,
        )
        method = f"embedding:{embedder.name}"

    cross = [c for c in clusters if c.get("cross_l2")]
    cross3 = [c for c in clusters if c.get("cross_l2_ge3")]
    insights = build_i7_insights(clusters)

    EXPORTS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = EXPORTS / f"complaint_clusters_{ts}.json"
    csv_path = EXPORTS / f"complaint_clusters_{ts}.csv"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "cluster_id",
                "theme",
                "count",
                "l2_list",
                "cross_l2",
                "l3_top",
                "quote",
            ],
        )
        w.writeheader()
        for c in clusters:
            w.writerow(
                {
                    "cluster_id": c["cluster_id"],
                    "theme": c["theme"],
                    "count": c["count"],
                    "l2_list": "|".join(c["l2_list"]),
                    "cross_l2": int(c["cross_l2"]),
                    "l3_top": (c["l3_breakdown"][0]["l3"] if c["l3_breakdown"] else ""),
                    "quote": (c["quotes"][0]["text"] if c["quotes"] else ""),
                }
            )

    payload = {
        "meta": {
            "sample_n": len(rows),
            "embedder": embedder.name,
            "method": method,
            "embed_db": str(embed_db),
            "stored_vectors": stored,
            "min_sim": args.min_sim,
            "opinion_db": str(args.db),
            "read_only_opinion": True,
        },
        "stats": {
            "cluster_n": len(clusters),
            "cross_l2_n": len(cross),
            "cross_l2_ge3_n": len(cross3),
        },
        "gate_ok": len(cross) >= 1 and stored == len(rows),
        "clusters": clusters,
        "insights": insights,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    summary = {
        "gate_ok": payload["gate_ok"],
        "meta": payload["meta"],
        "stats": payload["stats"],
        "insights": insights,
        "top_cross_l2": [
            {
                "theme": c["theme"],
                "count": c["count"],
                "l2_list": c["l2_list"],
                "l3_breakdown": c["l3_breakdown"][:5],
            }
            for c in cross[:5]
        ],
        "json": str(json_path),
        "csv": str(csv_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if payload["gate_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
