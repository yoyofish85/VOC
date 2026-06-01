# -*- coding: utf-8 -*-
"""
导出标注用标准标签体系：Excel + CSV
含：一级/二级/三级、同义词、关键词、聚类分组 ID
输出：../output/standard_label_system_for_annotators.xlsx / .csv
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict, List

import pandas as pd

_BASE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ROOT = os.path.normpath(os.path.join(_BASE, ".."))
MAPPING = os.path.join(_BASE, "label_hierarchy_final.json")
CLUSTERS = os.path.join(_BASE, "gold_l3_clusters_v3.json")
OUT_DIR = os.path.normpath(os.path.join(_BASE, "..", "output"))
OUT_XLSX = os.path.join(OUT_DIR, "standard_label_system_for_annotators.xlsx")
OUT_CSV = os.path.join(OUT_DIR, "standard_label_system_for_annotators.csv")


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--mapping", default=MAPPING)
    ap.add_argument("-c", "--clusters", default=CLUSTERS)
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    out_xlsx = os.path.join(args.out_dir, "standard_label_system_for_annotators.xlsx")
    out_csv = os.path.join(args.out_dir, "standard_label_system_for_annotators.csv")

    mapping = load_json(args.mapping)
    clusters_data: Dict[str, Any] = {}
    if os.path.isfile(args.clusters):
        clusters_data = load_json(args.clusters).get("by_l1_l2", {}) or {}

    rows: List[Dict[str, Any]] = []
    gid = 0

    for l1, l2d in mapping.items():
        if str(l1).startswith("_") or not isinstance(l2d, dict):
            continue
        for l2, block in l2d.items():
            if str(l2).startswith("_") or not isinstance(block, dict):
                continue
            tags = block.get("标签列表", []) or []
            detail = block.get("详情", {}) or {}
            cl_by_l2 = (clusters_data.get(l1) or {}).get(l2) or []

            for idx, l3 in enumerate(tags):
                l3s = str(l3).strip()
                freq = int(detail.get(l3s, 0) or 0)
                rows.append(
                    {
                        "一级标签": l1,
                        "二级标签": l2,
                        "三级标签_体系标准": l3s,
                        "体系频次": freq,
                        "聚类分组ID": "",
                        "聚类canonical": "",
                        "同义词_聚类": "",
                        "关键词_聚类": "",
                        "来源": "label_hierarchy",
                    }
                )

            for cl in cl_by_l2:
                if not isinstance(cl, dict):
                    continue
                gid += 1
                can = str(cl.get("canonical") or "").strip()
                syns = "；".join(str(x) for x in (cl.get("synonyms") or [])[:30])
                kws = "；".join(str(x) for x in (cl.get("keywords") or [])[:24])
                rows.append(
                    {
                        "一级标签": l1,
                        "二级标签": l2,
                        "三级标签_体系标准": can,
                        "体系频次": "",
                        "聚类分组ID": f"G{gid}",
                        "聚类canonical": can,
                        "同义词_聚类": syns,
                        "关键词_聚类": kws,
                        "来源": "gold_cluster_v3",
                    }
                )

    df = pd.DataFrame(rows)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    try:
        df.to_excel(out_xlsx, index=False, engine="openpyxl")
    except ImportError:
        print("⚠️ 未安装 openpyxl，已跳过 xlsx，请执行: pip install openpyxl")
    except Exception as e:
        print(f"⚠️ 写入 Excel 失败: {e}，已保留 CSV")

    print(f"✅ CSV: {out_csv}")
    print(f"✅ XLSX: {out_xlsx}（若失败请 pip install openpyxl）")
    print(f"   行数: {len(df)}")


if __name__ == "__main__":
    main()
