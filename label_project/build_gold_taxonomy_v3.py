# -*- coding: utf-8 -*-
"""
从 testdata3.csv（5628）构建：
- gold_l2_whitelist_v3.json：按「舆情分类」映射到体系一级后，将业务「二级标签」对齐到 label_hierarchy 的二级白名单（频次降序）
- gold_l3_clusters_v3.json：按 (一级,体系二级) 对「问题细分」做字符 Jaccard 合并，生成 canonical + synonyms + 关键词

仅作离线构建，不参与线上推理输入泄漏（识别时仍只用原文）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

_BASE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_GOLD = os.path.normpath(os.path.join(_BASE, "..", "testdata3.csv"))
_DEFAULT_MAP = os.path.join(_BASE, "label_hierarchy_final.json")
OUT_L2 = os.path.join(_BASE, "gold_l2_whitelist_v3.json")
OUT_L3 = os.path.join(_BASE, "gold_l3_clusters_v3.json")

VALID_L1 = ("产品质量类", "服务类", "体验需求类")


def _norm(s: Any) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return re.sub(r"\s+", " ", str(s).strip())


def jaccard_chars(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def map_business_l2_to_hierarchy(l1: str, l2_raw: Any, mapping: Dict[str, Any]) -> Optional[str]:
    """将 5628 业务二级名对齐到体系 JSON 中的二级 key。"""
    s = _norm(l2_raw)
    if not s:
        return None
    l1d = mapping.get(l1, {})
    cand = [
        k
        for k in l1d.keys()
        if not str(k).startswith("_") and isinstance(l1d.get(k), dict)
    ]
    if not cand:
        return None
    best = None
    best_sc = -1.0
    for k in cand:
        sc = jaccard_chars(s, k)
        if k in s or s in k:
            sc = max(sc, 0.45)
        if sc > best_sc:
            best_sc, best = sc, k
    if best is not None and best_sc >= 0.12:
        return best
    return None


def merge_l3_labels(
    labels: List[str], min_sim: float = 0.5
) -> List[Dict[str, Any]]:
    """对问题细分字符串列表做贪心合并。"""
    freq = Counter()
    for x in labels:
        x = _norm(x)
        if len(x) < 2:
            continue
        freq[x] += 1
    items = sorted(freq.items(), key=lambda x: -x[1])
    used: set = set()
    clusters: List[Dict[str, Any]] = []
    for label, cnt in items:
        if label in used:
            continue
        group = [label]
        used.add(label)
        for other, _ in items:
            if other in used or other == label:
                continue
            if jaccard_chars(label, other) >= min_sim:
                group.append(other)
                used.add(other)
        canonical = max(group, key=lambda z: freq.get(z, 0))
        syns = [g for g in group if g != canonical]
        keywords: set = set()
        for g in group:
            for L in (4, 3, 2):
                if len(g) < L:
                    continue
                for i in range(len(g) - L + 1):
                    w = g[i : i + L]
                    if len(w) >= 2:
                        keywords.add(w)
        kw_list = sorted(keywords, key=lambda x: (-len(x), -freq.get(x, 0)))[:24]
        clusters.append(
            {
                "canonical": canonical,
                "count": int(sum(freq[g] for g in group)),
                "synonyms": syns[:40],
                "keywords": kw_list,
            }
        )
    clusters.sort(key=lambda x: -x.get("count", 0))
    return clusters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", default=_DEFAULT_GOLD, help="5628 金标 CSV")
    ap.add_argument("-m", "--mapping", default=_DEFAULT_MAP, help="label_hierarchy_final.json")
    ap.add_argument("--out-l2", default=OUT_L2)
    ap.add_argument("--out-l3", default=OUT_L3)
    args = ap.parse_args()

    with open(args.mapping, "r", encoding="utf-8") as f:
        mapping = json.load(f)

    df = pd.read_csv(args.input, encoding="utf-8-sig")
    if "舆情分类" not in df.columns:
        raise SystemExit("需要列「舆情分类」")

    l2_counts: Dict[str, Counter] = {x: Counter() for x in VALID_L1}
    l3_by_pair: Dict[Tuple[str, str], List[str]] = defaultdict(list)

    n_map = 0
    for _, row in df.iterrows():
        l1 = _norm(row.get("舆情分类", ""))
        if l1 not in VALID_L1:
            continue
        l2h = map_business_l2_to_hierarchy(l1, row.get("二级标签"), mapping)
        if l2h:
            l2_counts[l1][l2h] += 1
            n_map += 1
        l3 = row.get("问题细分", "")
        if l2h and _norm(l3):
            l3_by_pair[(l1, l2h)].append(_norm(l3))

    # 白名单：每级保留频次>=1 的全部体系二级（已映射），按频次降序
    whitelist: Dict[str, List[str]] = {}
    for l1 in VALID_L1:
        pairs = l2_counts[l1].most_common()
        whitelist[l1] = [p[0] for p in pairs if p[1] >= 1]

    # 若某一级无映射，退化为 hierarchy 全部二级
    for l1 in VALID_L1:
        if not whitelist[l1]:
            whitelist[l1] = [
                k
                for k in mapping.get(l1, {}).keys()
                if not str(k).startswith("_") and isinstance(mapping[l1][k], dict)
            ]

    l3_out: Dict[str, Any] = {
        "_meta": {
            "source": os.path.basename(args.input),
            "rows": len(df),
            "l2_mapped_rows": n_map,
        },
        "by_l1_l2": {},
    }

    for (l1, l2), lst in l3_by_pair.items():
        if len(lst) < 2:
            if lst:
                l3_out["by_l1_l2"].setdefault(l1, {})[l2] = merge_l3_labels(lst)
        else:
            l3_out["by_l1_l2"].setdefault(l1, {})[l2] = merge_l3_labels(lst)

    with open(args.out_l2, "w", encoding="utf-8") as f:
        json.dump(whitelist, f, ensure_ascii=False, indent=2)
    with open(args.out_l3, "w", encoding="utf-8") as f:
        json.dump(l3_out, f, ensure_ascii=False, indent=2)

    print(f"✅ L2 白名单: {args.out_l2}（每级 { {k: len(v) for k, v in whitelist.items()} }）")
    print(f"✅ L3 聚类: {args.out_l3}（(l1,l2) 组数 {sum(len(v) for v in l3_out['by_l1_l2'].values())}）")


if __name__ == "__main__":
    main()
